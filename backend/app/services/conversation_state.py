"""Structured conversation facts shared by all Agent execution graphs."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from app.graphs.agent_core.contracts import AgentTask, ConversationState, migrate_conversation_state


_ANALYSIS_DIMENSIONS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("risk", ("风险", "风险评估", "风险分析")),
    ("financial", ("财务", "财务风险")),
    ("business_risk", ("商务", "商务风险", "供应依赖", "可替代性")),
    ("quality", ("质量", "质量风险")),
    ("delivery", ("交付", "交付风险")),
    ("esg", ("ESG", "esg", "环境社会治理")),
    ("sentiment", ("舆情", "新闻", "负面信息")),
    ("compliance", ("合规", "制裁", "黑名单")),
)
_DEFAULT_REVIEW_DIMENSIONS = ["risk", "financial", "business_risk"]
_GENERIC_RISK_QUERY_TOKENS = (
    "风险情况", "风险状况", "整体风险", "风险怎么样", "风险表现",
)
_PLURAL_REFERENCE_TOKENS = (
    "这些企业", "上述企业", "这些供应商", "上述供应商",
    "推荐的供应商", "推荐企业", "它们", "全部企业", "所有企业",
    "这两家", "这些家", "那些家", "两家供应商", "两家公司", "这两家公司",
)
_SINGULAR_REFERENCE_TOKENS = ("这家", "该企业", "该供应商", "该公司", "它")
_ORDINAL_TARGETS = (
    ("排名第一", 1), ("第一家", 1), ("前两家", 2),
    ("前3家", 3), ("前三家", 3), ("前五家", 5),
)
_EXCLUSION_TOKENS = ("除了", "除去", "排除")
_LOW_RISK_TOKENS = ("低风险", "风险较低")
_COMPANY_NAME_PATTERN = re.compile(
    r"([\u4e00-\u9fffA-Za-z0-9（）()·&-]{2,80}?"
    r"(?:有限责任公司|股份有限公司|集团有限公司|有限公司))"
)
_COMPANY_NAME_PREFIXES = (
    "请复核", "复核", "请对", "对", "将", "把", "分析", "评估", "查询", "查看", "监控", "请", "帮我",
)


@dataclass(frozen=True)
class TargetResolution:
    """Deterministic target selection result for a conversation turn."""

    target_supplier_names: list[str]
    confidence: float
    needs_clarification: bool = False
    reason: str = "no_target"


def analysis_dimensions_from_message(message: str) -> list[str]:
    """Return explicitly requested analysis dimensions in stable order."""
    if "五维风险" in message or "五个维度" in message:
        return ["risk", "financial", "business_risk", "quality", "delivery", "compliance"]
    explicit_dimensions = [
        dimension
        for dimension, keywords in _ANALYSIS_DIMENSIONS
        if any(keyword in message for keyword in keywords)
    ]
    if any(token in message for token in _GENERIC_RISK_QUERY_TOKENS):
        return list(_DEFAULT_REVIEW_DIMENSIONS)
    if explicit_dimensions:
        return explicit_dimensions
    if any(token in message for token in ("采购动作", "采取动作", "下一步怎么做", "是否需要处理", "要不要处理")):
        return list(_DEFAULT_REVIEW_DIMENSIONS)
    if "复核" in message:
        return list(_DEFAULT_REVIEW_DIMENSIONS)
    return []


def resolve_supplier_targets(
    message: str,
    supplier_references: list[dict[str, Any]],
) -> list[str]:
    """Compatibility facade returning only the deterministically selected names."""
    return resolve_supplier_target_selection(message, supplier_references).target_supplier_names


def resolve_supplier_target_selection(
    message: str,
    supplier_references: list[dict[str, Any]],
) -> TargetResolution:
    """Resolve names, aliases and contextual expressions without LLM guessing."""
    references = _normalized_references(supplier_references)
    explicit_company_names = _explicit_company_names(message)
    short_review_target = _short_review_target(message)
    short_assessment_target = _short_assessment_target(message)
    if not references:
        if explicit_company_names:
            return TargetResolution(explicit_company_names, 1.0, False, "explicit_full_name")
        if short_review_target:
            return TargetResolution([short_review_target], 0.9, False, "explicit_short_review_name")
        if short_assessment_target:
            return TargetResolution([short_assessment_target], 0.9, False, "explicit_short_assessment_name")
        needs_clarification = _has_contextual_target_reference(message)
        return TargetResolution([], 0.0, needs_clarification, "missing_context")

    explicit = _explicit_target_names(message, references)
    exclusion = _excluded_target_names(message, references)
    if exclusion:
        remaining = [reference["name"] for reference in references if reference["name"] not in exclusion]
        return TargetResolution(remaining, 0.95, False, "exclusion")
    if explicit:
        return TargetResolution(explicit, 1.0, False, "explicit_name_or_alias")
    if explicit_company_names:
        return TargetResolution(explicit_company_names, 1.0, False, "explicit_full_name")
    if short_review_target:
        return TargetResolution([short_review_target], 0.9, False, "explicit_short_review_name")
    if short_assessment_target:
        return TargetResolution([short_assessment_target], 0.9, False, "explicit_short_assessment_name")

    names = [reference["name"] for reference in references]
    for token, limit in _ORDINAL_TARGETS:
        if token in message:
            return TargetResolution(names[:limit], 0.95, False, "ordinal")
    if any(token in message for token in _LOW_RISK_TOKENS):
        low_risk = [
            reference["name"]
            for reference in references
            if _is_low_risk(reference)
        ]
        if low_risk:
            return TargetResolution(low_risk, 0.9, False, "low_risk_filter")
    if any(token in message for token in _PLURAL_REFERENCE_TOKENS):
        return TargetResolution(names, 0.95, False, "plural_reference")
    if any(token in message for token in _SINGULAR_REFERENCE_TOKENS):
        return TargetResolution(names[:1], 0.9, False, "singular_reference")
    return TargetResolution([], 0.0, False, "no_target")


def _short_review_target(message: str) -> str | None:
    """Extract a short subject after ``复核`` for deterministic scope gating."""
    normalized = str(message or "").strip()
    if not normalized.startswith("复核"):
        return None
    candidate = normalized[2:].strip(" ：:，,。？！!?\t")
    if not 2 <= len(candidate) <= 30:
        return None
    if any(token in candidate for token in ("风险", "财务", "商务", "企业", "供应商", "公司", "前两家", "前三家", "前五家", "第一家", "这些", "上述")):
        return None
    if not all("一" <= char <= "鿿" or char.isascii() for char in candidate):
        return None
    return candidate


def _short_assessment_target(message: str) -> str | None:
    """Extract a short subject after ``评估`` for external identity search."""
    normalized = str(message or "").strip()
    if not normalized.startswith("评估"):
        return None
    candidate = normalized[2:].strip(" ：:，,。？！!?\t")
    if not 2 <= len(candidate) <= 30:
        return None
    if any(token in candidate for token in ("风险", "财务", "商务", "企业", "供应商", "公司", "前两家", "前三家", "前五家", "第一家", "这些", "上述")):
        return None
    if not all("一" <= char <= "鿿" or char.isascii() for char in candidate):
        return None
    return candidate


def build_conversation_state(
    message: str,
    supplier_references: list[dict[str, Any]],
    previous_state: dict[str, Any] | None = None,
    *,
    session_id: str = "",
) -> dict[str, Any]:
    """Build the durable, serializable state for the current conversation turn."""
    previous = migrate_conversation_state(previous_state, session_id=session_id)
    active_suppliers = [
        reference
        for reference in supplier_references
        if isinstance(reference, dict) and reference.get("name")
    ]
    if not active_suppliers:
        active_suppliers = [reference.model_dump(mode="json") for reference in previous.active_suppliers]
    target_names = resolve_supplier_targets(message, active_suppliers)
    dimensions = analysis_dimensions_from_message(message)
    task_type = "analysis" if dimensions or target_names else "sourcing"
    state = ConversationState(
        session_id=session_id or previous.session_id,
        active_suppliers=active_suppliers,
        selected_supplier_names=target_names,
        current_requirement=previous.current_requirement,
        current_task=AgentTask(
            task_id="current-task",
            task_type=task_type,
            target_supplier_names=target_names,
            analysis_dimensions=dimensions,
            user_message=message,
        ),
        recent_tasks=previous.recent_tasks,
        pending_clarification=previous.pending_clarification,
        pending_approvals=previous.pending_approvals,
    )
    payload = state.model_dump(mode="json")
    payload["selected_suppliers"] = target_names
    return payload


def _normalized_references(supplier_references: list[dict[str, Any]]) -> list[dict[str, Any]]:
    references: list[dict[str, Any]] = []
    seen_names: set[str] = set()
    for reference in supplier_references:
        if not isinstance(reference, dict):
            continue
        name = str(reference.get("name", "")).strip()
        if not name or name in seen_names:
            continue
        references.append({**reference, "name": name})
        seen_names.add(name)
    return references


def _explicit_target_names(message: str, references: list[dict[str, Any]]) -> list[str]:
    targets: list[str] = []
    for reference in references:
        aliases = reference.get("aliases", [])
        alias_names = [
            str(alias).strip()
            for alias in aliases
            if isinstance(aliases, list) and str(alias).strip()
        ]
        candidates = [reference["name"], *alias_names]
        if any(candidate and candidate in message for candidate in candidates):
            targets.append(reference["name"])
    return targets


def _explicit_company_names(message: str) -> list[str]:
    """Extract full legal entity names from the current message without LLM guessing."""
    names: list[str] = []
    for match in _COMPANY_NAME_PATTERN.finditer(message):
        name = match.group(1).strip()
        for prefix in _COMPANY_NAME_PREFIXES:
            if name.startswith(prefix):
                name = name[len(prefix):].strip()
                break
        if name and name not in names:
            names.append(name)
    return names


def _excluded_target_names(message: str, references: list[dict[str, Any]]) -> list[str]:
    if not any(token in message for token in _EXCLUSION_TOKENS):
        return []
    return _explicit_target_names(message, references)


def _is_low_risk(reference: dict[str, Any]) -> bool:
    level = str(reference.get("risk_level") or reference.get("risk_label") or "").lower()
    return level in {"low", "低风险", "低"}


def _has_contextual_target_reference(message: str) -> bool:
    return any(
        token in message
        for token in (*_PLURAL_REFERENCE_TOKENS, *_SINGULAR_REFERENCE_TOKENS, *[item[0] for item in _ORDINAL_TARGETS])
    )
