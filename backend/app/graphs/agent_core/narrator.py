"""Evidence-bound natural-language answer generation for the Agent Harness."""

from __future__ import annotations

import json
import math
import os
import re
from typing import Any

from openai import OpenAI
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.core.config import settings
from app.core.logging import get_logger
from app.graphs.agent_core.answer_contract import AgentAnswer

logger = get_logger()


class NarrativeDraft(BaseModel):
    """Private LLM response; only ``body_markdown`` enters AgentAnswer.summary."""

    model_config = ConfigDict(extra="forbid")

    headline: str = Field(min_length=1, max_length=200)
    body_markdown: str = Field(min_length=1, max_length=4000)
    claim_refs: list[str] = Field(default_factory=list, max_length=30)


_COMPANY_PATTERN = re.compile(
    r"[\u4e00-\u9fffA-Za-z0-9（）()·&\-]{2,80}(?:有限责任公司|股份有限公司|集团有限公司|有限公司)"
)
_NUMBER_PATTERN = re.compile(r"(?<![A-Za-z])[+-]?\d+(?:\.\d+)?%?")


def _normalized_numbers(text: str) -> set[str]:
    return {
        token.lstrip("+-")
        for token in _NUMBER_PATTERN.findall(text)
        if token
    }


def _numeric_values(text: str) -> set[float]:
    """Normalize formatting-only numeric changes made by the narrator."""
    values: set[float] = set()
    for token in _NUMBER_PATTERN.findall(text):
        try:
            values.add(float(token.rstrip("%")))
        except ValueError:
            continue
    return values


def _claim_payload(answer: AgentAnswer) -> list[dict[str, Any]]:
    return [
        {
            "claim_id": claim.claim_id,
            "dimension": claim.dimension,
            "statement": claim.statement,
            "value": claim.value,
            "fact_path": claim.fact_path,
            "validation_status": claim.validation_status,
        }
        for claim in answer.claims
        if claim.validation_status == "supported"
    ]


def _requested_topic_terms(user_message: str) -> tuple[str, ...]:
    """Return a small topic guard so narration cannot erase the question."""
    topic_rules = (
        (("综合风险", "风险情况", "风险评估", "风险分析"), ("综合风险分析", "风险评估", "风险分析")),
        (("供应链关系", "关联关系", "传染风险", "风险传染"), ("供应链", "关联", "传染")),
        (("财务", "营收", "净利润"), ("财务", "营收", "净利润")),
        (("舆情", "新闻", "负面信息"), ("舆情", "新闻", "负面")),
        (("合规", "制裁", "黑名单"), ("合规", "制裁", "名单")),
        (("ESG", "esg", "环境社会治理"), ("ESG", "环境", "社会", "治理")),
        (("趋势", "变化"), ("趋势", "变化", "恶化", "改善", "稳定")),
        (("司法", "诉讼", "被执行", "失信"), ("司法", "诉讼", "被执行", "失信")),
        (("报告", "导出"), ("报告", "导出")),
    )
    for request_tokens, response_tokens in topic_rules:
        if any(token in user_message for token in request_tokens):
            return response_tokens
    return ()


def _draft_validation_error(
    draft: NarrativeDraft,
    answer: AgentAnswer,
    user_message: str = "",
) -> str | None:
    """Return a stable rejection reason when prose exceeds validated facts."""
    claims = _claim_payload(answer)
    claim_ids = {str(item["claim_id"]) for item in claims}
    if claims and not draft.claim_refs:
        return "missing_claim_refs"
    if not set(draft.claim_refs).issubset(claim_ids):
        return "unknown_claim_ref"
    source_text = " ".join(str(item.get("statement") or "") for item in claims)
    allowed_numbers = _normalized_numbers(source_text) | {"100"}
    allowed_numeric_values = _numeric_values(source_text) | {100.0}
    for number in _normalized_numbers(draft.body_markdown):
        if number in allowed_numbers:
            continue
        try:
            numeric_value = float(number.rstrip("%"))
        except ValueError:
            return "invalid_number"
        if not any(math.isclose(numeric_value, allowed, rel_tol=1e-9, abs_tol=1e-9) for allowed in allowed_numeric_values):
            return f"unsupported_number:{number}"
    allowed_companies = set(_COMPANY_PATTERN.findall(source_text))
    for name in _COMPANY_PATTERN.findall(draft.body_markdown):
        if name not in allowed_companies:
            return f"unsupported_company:{name}"
    narrative_text = f"{draft.headline}\n{draft.body_markdown}"
    topic_terms = _requested_topic_terms(user_message)
    if topic_terms and not any(term in narrative_text for term in topic_terms):
        return "missing_requested_topic"
    # A narrative must not turn a recommendation into a completed write action.
    forbidden_success = ("已加入监控", "已完成审批", "已切换供应商", "已暂停采购")
    if not answer.action_receipts and any(token in draft.body_markdown for token in forbidden_success):
        return "forbidden_success_action"
    return None


def _validate_draft(draft: NarrativeDraft, answer: AgentAnswer, user_message: str = "") -> bool:
    """Fail closed when the prose adds entities or numeric facts."""
    return _draft_validation_error(draft, answer, user_message) is None


def narrate_answer(answer: AgentAnswer, user_message: str) -> AgentAnswer:
    """Generate a readable summary from validated claims, with safe fallback."""
    if not settings.LLM_API_KEY or not answer.claims:
        return answer
    claims = _claim_payload(answer)
    article_count = next(
        (
            int(item["value"])
            for item in claims
            if item.get("fact_path") == "articles_count"
            and isinstance(item.get("value"), (int, float))
        ),
        0,
    )
    prompt = {
        "user_question": user_message,
        "deterministic_summary": answer.summary,
        "validated_claims": claims,
        "limitations": answer.limitations,
        "current_status": answer.status,
        "rules": [
            "Return one JSON object only, matching response_schema.",
            "只根据 validated_claims 和 limitations 写采购人员能直接理解的结论。",
            "不得新增公司、数字、日期、风险等级、来源或采购动作。",
            "如果存在 limitations，只能说明当前资料覆盖不足，不能把缺失数据推断成风险。",
            "如果 validated_claims 中 articles_count 大于0，必须说明已有新闻可在下方逐条查看，不能写“未包含新闻原文”或“无法逐条列出原文”。",
            "不要输出表格，不要提及 Claim、证据复核点、Harness、工具、任务、模型或内部字段。",
            "不要只复述综合风险评分或套用‘供应商复核’；必须先回答用户明确请求的主能力。",
            "用 3-5 个简短段落展开：第一段给出主结论；第二段说明关键依据；第三段解释对采购的影响和数据边界；最后给出下一步核验建议。",
            "如果用户同时询问多个能力，先回答最明确、最具体的能力，再把其他已验证维度作为补充，不要遗漏主问题。",
            "关系分析必须区分直接供应链依赖、分支机构和同行业关联；只有 Claim 明确支持时，才能称为直接供应链关系或风险传染路径。",
            "claim_refs 只能填写实际使用的 validated_claims 的 claim_id。",
        ],
        "response_schema": NarrativeDraft.model_json_schema(),
    }
    try:
        client = OpenAI(api_key=settings.LLM_API_KEY, base_url=settings.LLM_BASE_URL)
        response = client.chat.completions.create(
            model=settings.LLM_MODEL,
            messages=[
                {
                    "role": "system",
                    "content": "你是采购风险复核助手，只能改写已验证事实，不得编造或执行操作。请只返回 JSON 对象。",
                },
                {"role": "user", "content": json.dumps(prompt, ensure_ascii=False)},
            ],
            temperature=0.1,
            response_format={"type": "json_object"},
            # DeepSeek reasoning models may spend part of the generation
            # budget on hidden reasoning before emitting JSON.  Keep the
            # visible answer short, but leave enough room for the object to
            # finish instead of returning a truncated/empty content field.
            max_tokens=1800,
            timeout=float(os.getenv("LLM_NARRATION_TIMEOUT", "8")),
        )
        content = response.choices[0].message.content or "{}"
        payload = json.loads(content)
        if isinstance(payload, dict) and payload.get("type") == "json_object":
            nested_payload = payload.get("content")
            payload = nested_payload if isinstance(nested_payload, dict) else {
                key: value for key, value in payload.items() if key != "type"
            }
        draft = NarrativeDraft.model_validate(payload)
        if article_count > 0 and any(
            phrase in draft.body_markdown
            for phrase in ("未包含新闻原文", "无法逐条列出原文", "没有新闻原文")
        ):
            logger.warning("answer_narration_rejected", reason="article_evidence_contradiction")
            return answer
        validation_error = _draft_validation_error(draft, answer, user_message)
        if validation_error is not None:
            logger.warning("answer_narration_rejected", reason=validation_error)
            return answer
        return answer.model_copy(update={"summary": f"{draft.headline}\n\n{draft.body_markdown}"})
    except (json.JSONDecodeError, ValidationError, TypeError, ValueError) as exc:
        logger.warning("answer_narration_invalid", error=str(exc))
        return answer
    except Exception as exc:
        logger.warning("answer_narration_failed", error=str(exc))
        return answer


__all__ = ["NarrativeDraft", "narrate_answer"]
