"""Agent 结构化软指标评分。LLM temperature=0，Redis 缓存 24h。"""

import json
import logging
from datetime import datetime, timezone
from typing import Any

from openai import OpenAI

from app.core.cache import cache_client
from app.core.config import settings
from app.core.llm_compat import completion_token_options
from app.core.structured_response import (
    StructuredResponseError,
    parse_json_object_content,
)

logger = logging.getLogger(__name__)

_CACHE_TTL = 86400  # 24 hours
_CACHE_PREFIX = "soft_risk"

SOFT_DIMENSIONS = {
    "舆情风险": {
        "max_score": 20,
        "description": "近期新闻情感、负面报道数量、公众形象、品牌声誉变化",
    },
    "ESG风险": {
        "max_score": 15,
        "description": "环境处罚、社会责任、公司治理水平、合规记录",
    },
    "宏观风险": {
        "max_score": 15,
        "description": "所属行业景气度、区域经济、政策环境、地缘政治影响",
    },
    "管理风险": {
        "max_score": 10,
        "description": "管理层稳定性、法人变更频率、股权结构稳定性、经营战略连续性",
    },
}


def score_soft_risks(company_name: str, context: dict[str, Any] | None = None) -> dict:
    """调用 LLM 对软指标进行结构化评分。结果缓存 24h。

    Args:
        company_name: 企业名称
        context: 可选上下文（如已有的舆情、ESG、宏观数据）

    Returns:
        {"dimensions": {...}, "summary": "...", "scored_at": "..."}
    """
    # Check cache
    from app.domains.risk.assessment_contracts import canonical_hash

    cache_key = f"{_CACHE_PREFIX}:evidence-v1:{canonical_hash([company_name, context or {}, settings.LLM_MODEL, 'soft-evidence-1'])}"
    try:
        cached = cache_client.get(cache_key)
        if cached:
            value = json.loads(cached)
            if value.get("status") == "available":
                return value
    except Exception:
        pass

    result = _call_llm(company_name, context)
    if result is None:
        # LLM 调用失败返回空评分
        return {
            "status": "unavailable",
            "dimensions": {
                dim: {"status": "unavailable", "reason": "LLM 不可用"}
                for dim in SOFT_DIMENSIONS
            },
            "summary": "",
            "scored_at": datetime.now(timezone.utc).isoformat(),
        }

    dimensions = result.get("dimensions") or {}
    for dim in SOFT_DIMENSIONS:
        if dim not in dimensions:
            dimensions[dim] = {"status": "unavailable", "reason": "未分析"}
        elif "score" in dimensions[dim]:
            dimensions[dim]["status"] = "available"
    result["status"] = (
        "available"
        if all(item.get("status") == "available" for item in dimensions.values())
        else "partial"
    )
    result["dimensions"] = dimensions
    result["model"] = settings.LLM_MODEL
    result["prompt_version"] = "soft-evidence-1"
    result["context_hash"] = canonical_hash(context or {})
    result["scored_at"] = datetime.now(timezone.utc).isoformat()

    # Save cache
    try:
        cache_client.setex(
            cache_key, _CACHE_TTL, json.dumps(result, ensure_ascii=False)
        )
    except Exception:
        pass

    return result


def _call_llm(company_name: str, context: dict[str, Any] | None) -> dict | None:
    """调用 LLM 获取结构化评分。"""
    try:
        client = OpenAI(api_key=settings.LLM_API_KEY, base_url=settings.LLM_BASE_URL)
    except Exception:
        return None

    dim_desc = "\n".join(
        f"- {name} (0-{info['max_score']}分): {info['description']}"
        for name, info in SOFT_DIMENSIONS.items()
    )

    context_text = ""
    if context:
        context_text = "\n已知数据:\n"
        for k, v in context.items():
            context_text += f"- {k}: {v}\n"

    prompt = f"""你是一位专业的供应商风险评估专家。请根据企业"{company_name}"的公开信息，对以下软指标进行评分。

{context_text}
评分维度：
{dim_desc}

要求：
1. 每个维度给出 0 到 max_score 的整数评分，0 表示无风险
2. 每个维度用一句话说明评分理由（中文）
3. 仅输出 JSON，不要任何其他文字

输出格式：
{{"dimensions": {{"舆情风险": {{"score": 整数, "reason": "理由"}}, "ESG风险": ..., "宏观风险": ..., "管理风险": ...}}, "summary": "一句话总结主要风险点"}}"""

    for attempt in range(2):
        request_prompt = prompt
        if attempt:
            request_prompt += "\n上一轮响应为空或不是合法 JSON。请只返回完整 JSON 对象，不要代码围栏、解释或截断。"
        try:
            response = client.chat.completions.create(
                model=settings.LLM_MODEL,
                messages=[{"role": "user", "content": request_prompt}],
                temperature=0,
                response_format={"type": "json_object"},
                **completion_token_options(600),
                timeout=30,
            )
            result = parse_json_object_content(
                response.choices[0].message.content,
                operation="soft_risk",
                response=response,
            )
            dimensions = result.get("dimensions")
            if not isinstance(dimensions, dict) or not isinstance(
                result.get("summary"), str
            ):
                raise StructuredResponseError("missing_required_fields")
            for dim in SOFT_DIMENSIONS:
                item = dimensions.get(dim)
                if item is None:
                    continue
                if not isinstance(item, dict):
                    raise StructuredResponseError("invalid_dimension")
                score = item.get("score")
                reason = item.get("reason")
                if (
                    not isinstance(score, int)
                    or isinstance(score, bool)
                    or not 0 <= score <= SOFT_DIMENSIONS[dim]["max_score"]
                    or not isinstance(reason, str)
                ):
                    raise StructuredResponseError("invalid_dimension")
            return result
        except StructuredResponseError as exc:
            logger.warning(
                "soft_risk_llm_invalid company=%s attempt=%s reason=%s",
                company_name,
                attempt + 1,
                exc.kind,
            )
            if attempt == 0:
                continue
        except Exception as exc:
            # This module uses the standard-library logger; pass context through
            # the message arguments instead of structlog-style keyword fields.
            logger.warning(
                "soft_risk_llm_failed company=%s error=%s", company_name, str(exc)
            )
            return None
    return None
