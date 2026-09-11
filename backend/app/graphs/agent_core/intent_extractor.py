"""LLM-first, validation-backed extraction for one conversation turn."""

from __future__ import annotations

import json
import os
import re
from typing import Any, Literal

from openai import OpenAI
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger()

_ANALYSIS_TOKENS = (
    "风险", "财务", "商务", "供应依赖", "可替代", "质量", "交付",
    "ESG", "esg", "舆情", "合规", "制裁", "监控", "评估", "分析", "复核",
)
_NON_AGENT_TURN_PATTERNS = (
    "你好", "您好", "嗨", "hello", "hi", "谢谢", "感谢", "好的", "ok", "收到",
)
_GENERIC_RISK_QUERY_TOKENS = (
    "风险情况", "风险状况", "整体风险", "风险怎么样", "风险表现",
)
_MONITOR_TARGET_ID_PATTERN = re.compile(
    r"(?i)\b[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}\b"
)


class ConversationIntentExtraction(BaseModel):
    """The narrow, side-effect-free contract the LLM may return."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    target_supplier_names: list[str] = Field(default_factory=list, max_length=10)
    analysis_dimensions: list[Literal[
        "risk", "financial", "business_risk", "quality", "delivery", "esg", "sentiment", "compliance"
    ]] = Field(
        default_factory=list,
        max_length=8,
    )
    provider_capabilities: list[Literal[
        "identity", "legal_risk", "business_risk", "news", "profile"
    ]] = Field(default_factory=list, max_length=5)
    task_type: Literal["sourcing", "analysis", "none"] = "none"
    requested_action: Literal["add_watchlist", "none"] = "none"
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)

    @field_validator("target_supplier_names")
    @classmethod
    def normalize_targets(cls, value: list[str]) -> list[str]:
        return list(dict.fromkeys(item.strip() for item in value if item.strip()))

    @field_validator("analysis_dimensions")
    @classmethod
    def normalize_dimensions(
        cls, value: list[Literal[
            "risk", "financial", "business_risk", "quality", "delivery", "esg", "sentiment", "compliance"
        ]]
    ) -> list[Literal[
        "risk", "financial", "business_risk", "quality", "delivery", "esg", "sentiment", "compliance"
    ]]:
        return list(dict.fromkeys(value))


def should_extract_conversation_intent(message: str) -> bool:
    """Call the LLM for every meaningful turn, skipping only clear small talk.

    Entity-only follow-ups such as ``上海某某有限公司`` deliberately pass this
    gate: the previous implementation required an analysis keyword and therefore
    never gave the LLM a chance to bind the company to the pending task.
    """
    normalized = "".join(str(message or "").strip().lower().split())
    if not normalized:
        return False
    if normalized in _NON_AGENT_TURN_PATTERNS:
        return False
    if len(normalized) <= 3 and not any(token in normalized for token in _ANALYSIS_TOKENS):
        return False
    return True


def extract_conversation_intent(
    message: str,
    supplier_references: list[dict[str, Any]],
) -> ConversationIntentExtraction | None:
    """Ask the LLM to understand the current turn; return None on safe fallback."""
    if not should_extract_conversation_intent(message) or not settings.LLM_API_KEY:
        return None

    prompt = {
        "task": "Extract the supplier-analysis intent from the current user message.",
        "current_message": message,
        "known_supplier_references": _reference_context(supplier_references),
        "response_schema": ConversationIntentExtraction.model_json_schema(),
        "rules": [
            "Return one JSON object only.",
            "Extract explicitly named companies from the current message even when they are absent from known_supplier_references.",
            "Use known_supplier_references only to resolve pronouns or aliases such as '这家' and '上述两家'.",
            "Do not invent companies, supplier codes, risk findings, or actions.",
            "Set task_type='sourcing' for finding, recommending, or listing suppliers, including requests such as '找风险最低的供应商'; risk is then a sourcing filter, not a company risk-assessment task.",
            "Set task_type='analysis' for assessing explicitly named suppliers; set task_type='none' only when no agent task is requested.",
            "For a generic supplier review ('复核' or '风险情况') without explicit dimensions, use risk, financial, and business_risk; explicit dimensions take precedence.",
            "Use provider_capabilities only when the user explicitly asks for工商主体、司法/诉讼、经营处罚、新闻舆情、工商资料或天眼查查询; choose one or more of identity, legal_risk, business_risk, news, profile.",
            "Use requested_action='add_watchlist' only when the user explicitly asks to monitor or add to monitoring.",
            "This is read-only intent extraction and must not execute an action.",
        ],
    }
    try:
        client = OpenAI(api_key=settings.LLM_API_KEY, base_url=settings.LLM_BASE_URL)
        response = client.chat.completions.create(
            model=settings.LLM_MODEL,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "你是供应商分析系统的意图与实体解析器。"
                        "只能返回符合用户消息和 JSON Schema 的结构化结果，"
                        "不得执行工具、写入数据或编造企业与风险事实。"
                    ),
                },
                {"role": "user", "content": json.dumps(prompt, ensure_ascii=False)},
            ],
            temperature=0,
            response_format={"type": "json_object"},
            max_tokens=500,
            timeout=float(os.getenv("LLM_INTENT_EXTRACTION_TIMEOUT", "8")),
        )
        content = response.choices[0].message.content or "{}"
        extracted = ConversationIntentExtraction.model_validate(
            _normalize_llm_payload(json.loads(content))
        )
    except (json.JSONDecodeError, ValidationError, TypeError, ValueError) as exc:
        logger.warning("conversation_intent_extraction_invalid", error=str(exc))
        return None
    except Exception as exc:
        logger.warning("conversation_intent_extraction_failed", error=str(exc))
        return None

    inferred_task_type = infer_task_type(message)
    extracted_dimensions = _expand_generic_risk_dimensions(
        message,
        list(extracted.analysis_dimensions),
    )
    validated = extracted.model_copy(update={
        "target_supplier_names": validate_extracted_targets(
            extracted.target_supplier_names, supplier_references
        ),
        "analysis_dimensions": extracted_dimensions,
        "task_type": inferred_task_type if inferred_task_type != "none" else extracted.task_type,
        # The model may over-read the word “监控” in a read-only identity
        # request. A write action is allowed only when the current message
        # contains an explicit add/monitor instruction.
        "requested_action": (
            extracted.requested_action
            if has_explicit_watchlist_request(message)
            else "none"
        ),
    })
    logger.info(
        "conversation_intent_extracted",
        target_supplier_names=validated.target_supplier_names,
        analysis_dimensions=validated.analysis_dimensions,
        task_type=validated.task_type,
        requested_action=validated.requested_action,
        confidence=validated.confidence,
    )
    return validated


def has_explicit_watchlist_request(message: str) -> bool:
    """Return whether the user explicitly asked to add a target to monitoring."""
    normalized = "".join(str(message or "").strip().lower().split())
    return any(token in normalized for token in (
        "加入监控", "加入风险监控", "纳入监控", "纳入风险监控",
        "加入到监控", "加入到风险监控", "添加监控", "添加到监控",
        "纳入到监控", "纳入到风险监控", "持续监控", "开始监控", "建立监控",
    ))


def is_identity_verification_request(message: str) -> bool:
    """Recognize an explicit read-only request to verify a monitor identity."""
    text = str(message or "")
    return (
        any(token in text for token in ("主体身份", "主体核验", "核验主体", "确认主体"))
        and any(token in text for token in ("核验", "确认", "检索", "查找", "验证"))
        # A user may ask to verify a named supplier without spelling out
        # “监控对象”.  The company name is enough to enter the read-only
        # identity flow; a monitor UUID remains the preferred stable key when
        # it is present.
        and (
            "监控对象" in text
            or "监控目标" in text
            or extract_monitor_target_id(text) is not None
            or bool(re.search(r"(?:有限公司|股份有限公司|集团有限公司|集团)", text))
        )
    )


def extract_monitor_target_id(message: str) -> str | None:
    """Extract a UUID monitor target identifier without trusting free text."""
    match = _MONITOR_TARGET_ID_PATTERN.search(str(message or ""))
    return match.group(0) if match else None


# Backward-compatible alias for focused tests and older callers.
_has_explicit_watchlist_request = has_explicit_watchlist_request


def _expand_generic_risk_dimensions(message: str, dimensions: list[str]) -> list[str]:
    """Use the standard review bundle for an unqualified risk-status question.

    The model may conservatively return only ``risk`` for wording such as
    ``看一下某供应商的风险情况``.  That would hide the financial and internal
    procurement checks users expect from a general risk review.  The expansion
    is deterministic and does not invent a supplier or a finding.
    """
    text = str(message or "")
    # A plural/range query already has an intentional lightweight risk scope;
    # expanding it to financial and business checks would multiply calls and
    # change the established “这些供应商的风险情况” contract.
    if any(token in text for token in ("这些供应商", "上述供应商", "所有供应商")):
        return list(dict.fromkeys(dimensions))
    if any(token in text for token in _GENERIC_RISK_QUERY_TOKENS):
        if dimensions and set(dimensions) <= {"risk"}:
            return ["risk", "financial", "business_risk"]
    return list(dict.fromkeys(dimensions))


def infer_task_type(message: str) -> Literal["sourcing", "analysis", "none"]:
    """Classify the explicit operation before applying dimension filters."""
    text = str(message or "").strip()
    if not text:
        return "none"
    if "正式供应商" in text and any(
        token in text for token in ("查询", "哪些", "列表", "目录", "清单", "有多少")
    ):
        return "sourcing"
    has_supplier_target = any(token in text for token in ("供应商", "厂家", "厂商"))
    has_discovery_verb = any(token in text for token in ("找", "推荐", "寻找", "采购", "搜寻", "寻源", "有哪些", "历史合作", "补充"))
    if has_supplier_target and has_discovery_verb:
        return "sourcing"
    if any(token in text for token in _ANALYSIS_TOKENS):
        return "analysis"
    return "none"


def validate_extracted_targets(
    targets: list[str], supplier_references: list[dict[str, Any]]
) -> list[str]:
    """Canonicalize exact known names/aliases; keep unknown explicit names for lookup."""
    known_by_alias: dict[str, str] = {}
    for reference in supplier_references:
        if not isinstance(reference, dict):
            continue
        name = reference.get("name")
        if not isinstance(name, str) or not name.strip():
            continue
        canonical_name = name.strip()
        known_by_alias[canonical_name.casefold()] = canonical_name
        aliases = reference.get("aliases")
        if isinstance(aliases, list):
            for alias in aliases:
                if isinstance(alias, str) and alias.strip():
                    known_by_alias[alias.strip().casefold()] = canonical_name
    return list(dict.fromkeys(
        known_by_alias.get(target.strip().casefold(), target.strip())
        for target in targets
        if isinstance(target, str) and target.strip()
    ))


def _normalize_llm_payload(payload: Any) -> Any:
    """Accept Chinese dimension labels while retaining the strict public contract."""
    if not isinstance(payload, dict):
        return payload
    dimension_map = {
        "风险": "risk",
        "风险评估": "risk",
        "财务": "financial",
        "财务风险": "financial",
        "商务": "business_risk",
        "商务风险": "business_risk",
        "供应依赖": "business_risk",
        "可替代性": "business_risk",
        "质量": "quality",
        "质量风险": "quality",
        "交付": "delivery",
        "交付风险": "delivery",
        "esg": "esg",
        "ESG": "esg",
        "舆情": "sentiment",
        "合规": "compliance",
        "制裁": "compliance",
    }
    dimensions = payload.get("analysis_dimensions")
    return {
        **payload,
        **({
            "analysis_dimensions": [dimension_map.get(str(item), item) for item in dimensions]
        } if isinstance(dimensions, list) else {}),
    }


def _reference_context(supplier_references: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "name": reference.get("name"),
            "aliases": reference.get("aliases", []),
            "supplier_code": reference.get("supplier_code"),
        }
        for reference in supplier_references
        if isinstance(reference, dict) and isinstance(reference.get("name"), str)
    ]
