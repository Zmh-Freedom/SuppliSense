"""Deterministic safeguards for explicit, single-capability read requests.

The LLM remains responsible for understanding composite requests.  These rules
only correct a generic ``risk``/``none`` result when one high-confidence,
read-only capability is explicit in the current message.
"""

from __future__ import annotations

from dataclasses import dataclass
import re


@dataclass(frozen=True)
class ExplicitCapabilityRule:
    """A narrow phrase set that can safely disambiguate one read capability."""

    capability: str
    phrases: tuple[str, ...]


_EXPLICIT_CAPABILITY_RULES: tuple[ExplicitCapabilityRule, ...] = (
    ExplicitCapabilityRule(
        "risk_network",
        (
            "供应链关系", "上下游关系", "关联企业", "关联公司", "关联关系",
            "风险传导", "风险传染", "传染风险", "传导路径",
        ),
    ),
    ExplicitCapabilityRule(
        "sentiment",
        ("舆情", "新闻动态", "新闻报道", "负面信息", "负面舆情", "负面报道", "媒体报道", "新闻"),
    ),
    ExplicitCapabilityRule(
        "legal_risk",
        ("司法风险", "司法", "当前未结案件", "当前未决案件"),
    ),
    ExplicitCapabilityRule(
        "business_risk",
        ("经营风险", "运营风险", "经营状况", "经营和交付风险", "运营和交付风险"),
    ),
    ExplicitCapabilityRule(
        "financial",
        ("财务数据", "财务", "营业收入", "营收", "净利润", "现金流", "负债率", "资产负债", "财报"),
    ),
    ExplicitCapabilityRule(
        "risk_prediction",
        ("风险预测", "预测风险", "情景推演", "未来一年", "未来半年", "未来三个月", "未来 3 个月", "如果", "假如"),
    ),
    ExplicitCapabilityRule(
        "risk_trend",
        ("历史风险变化", "风险走势", "上月相比", "环比", "同比", "最近三个月", "近三个月"),
    ),
    ExplicitCapabilityRule(
        "identity_review",
        ("主体核验", "核验主体", "主体身份", "统一社会信用代码", "是否同一主体", "登记状态"),
    ),
    ExplicitCapabilityRule(
        "company_profile",
        ("工商资料", "注册资本", "注册地址", "法定代表人", "联系人", "联系方式", "官网", "联系电话", "电话"),
    ),
    ExplicitCapabilityRule(
        "risk_comparison",
        ("横向比较", "企业对比", "供应商对比", "哪家更适合", "比较", "对比"),
    ),
    ExplicitCapabilityRule(
        "quality",
        ("质量风险", "质量问题", "产品质量", "不良率", "质量稳定性"),
    ),
    ExplicitCapabilityRule(
        "delivery",
        ("交付风险", "准时交付", "交付周期", "交货延期", "延期交货", "交期"),
    ),
    ExplicitCapabilityRule(
        "sourcing",
        ("寻找供应商", "推荐供应商", "候选供应商", "替代供应商", "备选供应商", "替代厂家", "备选厂家"),
    ),
    ExplicitCapabilityRule(
        "report",
        ("生成风险报告", "导出风险报告", "下载风险报告", "生成报告", "导出报告", "风险报告"),
    ),
)

_CONTROL_PHRASES = (
    "停止刚才", "取消刚才", "继续刚才", "恢复刚才", "未完成的",
    "批准刚才", "拒绝刚才", "审批申请",
)

_GENERIC_RISK_REQUEST_PHRASES = (
    "综合风险", "整体风险", "风险情况", "风险状况", "全部风险", "全面风险",
)

_DYNAMIC_CAPABILITY_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "risk_prediction",
        re.compile(r"未来\s*(?:[一二三四五六七八九十\d]+|半)\s*(?:个)?月"),
    ),
    (
        "risk_trend",
        re.compile(r"(?:近|最近)\s*[一二三四五六七八九十\d]+\s*(?:个)?月"),
    ),
)

_CAPABILITY_PRIMARY_DIMENSIONS = {
    "sentiment": "sentiment",
    "financial": "financial",
    "quality": "quality",
    "delivery": "delivery",
}


def detect_explicit_capabilities(message: str) -> list[str]:
    """Return stable, distinct high-confidence read capability candidates.

    A future-oriented sentence can naturally also contain "风险走势".  In
    that case prediction is the explicit user intent, so trend is not added as
    a second candidate merely because of the shared word "风险".
    """
    text = str(message or "")
    matched: list[tuple[int, int, int, str]] = []
    has_prediction = any(
        phrase in text
        for phrase in ("预测", "未来", "情景推演", "如果", "假如")
    )
    has_scenario = any(phrase in text for phrase in ("如果", "假如", "情景推演"))
    for priority, rule in enumerate(_EXPLICIT_CAPABILITY_RULES):
        if rule.capability == "risk_trend" and has_prediction:
            continue
        # In a scenario question, financial wording is the input condition;
        # the requested output is a risk prediction rather than a financial
        # statement. Keeping both would suppress the safe single-capability
        # correction entirely.
        if rule.capability == "financial" and has_scenario:
            continue
        phrase_matches = []
        for phrase in rule.phrases:
            start = 0
            while (position := text.find(phrase, start)) >= 0:
                prefix = text[max(0, position - 12):position]
                # Ignore a capability explicitly cancelled in the same clause.
                clause_prefix = re.split(r"[，,。；;！？!?]", prefix)[-1]
                if not re.search(r"(?:不看|先不看|不查|先不查|不需要|无需|不用看|不用查)\s*(?:再)?$", clause_prefix):
                    phrase_matches.append((position, len(phrase)))
                start = position + len(phrase)
        if not phrase_matches:
            continue
        first_position = min(position for position, _length in phrase_matches)
        phrase_length = max(
            length for position, length in phrase_matches if position == first_position
        )
        matched.append((first_position, -phrase_length, priority, rule.capability))
    for pattern_priority, (capability, pattern) in enumerate(_DYNAMIC_CAPABILITY_PATTERNS):
        if capability == "risk_trend" and has_prediction:
            continue
        match = pattern.search(text)
        if match:
            matched.append((match.start(), -len(match.group()), len(_EXPLICIT_CAPABILITY_RULES) + pattern_priority, capability))
    return list(dict.fromkeys(item[3] for item in sorted(matched)))


def has_durable_control_expression(message: str) -> bool:
    """Return whether a message is explicitly controlling an earlier run."""
    text = str(message or "")
    return any(phrase in text for phrase in _CONTROL_PHRASES)


def explicit_primary_capability(message: str) -> str | None:
    """Return one capability only when the message is unambiguous."""
    text = str(message or "")
    if "诉讼" in text and "行政处罚" in text:
        return "compliance" if any(token in text for token in ("有没有", "是否有", "是否存在", "有无")) else None
    if any(phrase in text for phrase in _GENERIC_RISK_REQUEST_PHRASES):
        return None
    candidates = detect_explicit_capabilities(text)
    # A correction clause states the final requested capability. Earlier
    # capabilities in the same sentence may have been explicitly cancelled.
    if any(token in str(message or "") for token in ("改查", "改看", "改为", "改成")) and candidates:
        return candidates[-1] if len(candidates) == 1 else None
    return candidates[0] if len(candidates) == 1 else None


def normalize_primary_capability(
    message: str,
    extracted_capability: str,
    requested_action: str = "none",
) -> str:
    """Correct only generic LLM output without changing write/control routes."""
    capability = str(extracted_capability or "none")
    if capability not in {"risk", "none"}:
        return capability
    if requested_action != "none" or has_durable_control_expression(message):
        return capability
    return explicit_primary_capability(message) or capability


def normalize_analysis_dimensions(
    message: str,
    dimensions: list[str],
    raw_capability: str,
    normalized_capability: str,
    requested_action: str = "none",
) -> list[str]:
    """Remove generic-risk work left behind by an explicit primary capability.

    Models can classify a request correctly while still emitting ``risk`` as
    an analysis dimension.  The message-level uniqueness guard is therefore
    intentional: it also removes that redundant task, while preserving any
    true composite or explicit overall-risk request.
    """
    normalized_dimensions = list(dict.fromkeys(
        str(item).strip() for item in dimensions if str(item).strip()
    ))
    del raw_capability
    if (
        requested_action != "none"
        or has_durable_control_expression(message)
        or normalized_capability == "sourcing"
        or explicit_primary_capability(message) != normalized_capability
    ):
        return normalized_dimensions
    remaining = [item for item in normalized_dimensions if item != "risk"]
    primary_dimension = _CAPABILITY_PRIMARY_DIMENSIONS.get(normalized_capability)
    if primary_dimension:
        return list(dict.fromkeys([primary_dimension, *remaining]))
    return remaining
