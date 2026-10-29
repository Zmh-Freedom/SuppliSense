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
        (
            "历史风险变化", "风险变化", "风险走势", "风险趋势", "上月相比",
            "环比", "同比",
        ),
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

_TREND_ACTION_MARKERS = (
    "变化", "变动", "怎么变", "走势", "趋势", "环比", "同比", "相比", "对比上月",
)

_PURE_GREETING_PHRASES = {
    "你好", "您好", "嗨", "哈喽", "hello", "hi", "早上好", "上午好", "中午好",
    "下午好", "晚上好", "在吗",
}
_PURE_THANKS_PHRASES = {
    "谢谢", "谢谢你", "非常感谢", "感谢", "感谢你", "多谢", "多谢你", "辛苦了", "辛苦你了",
}
_PURE_CAPABILITY_PHRASES = {
    "你能做什么", "你可以做什么", "能做什么", "可以做什么", "你能帮我什么",
    "你可以帮我什么", "你能帮我做什么", "你可以帮我做什么", "可以帮我做什么",
    "你能帮我分析什么", "你可以帮我分析什么", "你是谁", "你是做什么的",
    "介绍一下你自己", "介绍一下你的能力", "介绍一下你的功能", "你的能力是什么",
    "你的功能是什么", "你有什么能力", "你有哪些能力", "你可以提供什么帮助",
    "你能提供什么帮助", "请介绍你的能力", "请介绍一下你的功能",
}
_PURE_CONVERSATION_PUNCTUATION = re.compile(r"[\s，。！？；：、,.!?;:~～…\"'“”‘’（）()【】\[\]{}<>《》]+")


def pure_conversation_kind(message: str) -> str | None:
    """Recognize only standalone greetings, thanks, and capability questions."""
    normalized = _PURE_CONVERSATION_PUNCTUATION.sub("", str(message or "").lower())
    if normalized in _PURE_GREETING_PHRASES:
        return "greeting"
    if normalized in _PURE_THANKS_PHRASES:
        return "thanks"
    if normalized in _PURE_CAPABILITY_PHRASES:
        return "capability"

    for greeting in ("你好", "您好", "嗨", "哈喽", "早上好", "上午好", "下午好"):
        if not normalized.startswith(greeting):
            continue
        remainder = normalized[len(greeting):]
        if remainder.startswith("今天"):
            remainder = remainder[len("今天"):]
        if remainder in _PURE_CAPABILITY_PHRASES:
            return "capability"
    return None

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
    has_trend_action = any(phrase in text for phrase in _TREND_ACTION_MARKERS)
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
        if capability == "risk_trend" and not has_trend_action:
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
    if _is_quantity_bearing_purchase_request(text) and not any(
        token in text for token in ("风险分析", "风险评估", "风险复核", "质量风险", "交付风险")
    ):
        return "sourcing"
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


def _is_quantity_bearing_purchase_request(message: str) -> bool:
    """Recognize concrete read-only sourcing requests that name an order quantity."""
    match = re.search(
        r"(?:采购|购买|订购)\s*\d+(?:\.\d+)?\s*(?:件|台|套|个|只|箱|吨|公斤|千克|米|根|枚|张|组|批)\s*[^，,。；;\n]{2,}",
        str(message or ""),
    )
    if not match:
        return False
    return not is_non_operational_request(message)


def is_non_operational_request(message: str) -> bool:
    """Identify explicit meta, general-knowledge, or topic-switch requests.

    These requests may mention supplier capabilities, risk terms, or sourcing
    phrases as content, but they do not ask the agent to inspect suppliers or
    discover candidates.
    """
    text = str(message or "").strip()
    if not text:
        return False

    if any(token in text for token in ("翻译", "英文怎么说", "英语怎么说", "英文如何表达", "英语如何表达")):
        return True
    if any(token in text for token in ("润色一下", "帮我润色", "写一封", "整理成一封", "总结下面这段", "总结这段话")):
        return True
    if any(token in text for token in ("解释一下", "解释成", "解释为", "是什么概念", "这个概念", "是什么意思")):
        return True
    if re.search(r"一般(?:会)?(?:看|关注|包含|有哪些|有什么).{0,12}(?:指标|因素|维度)", text):
        return True
    if any(token in text for token in ("如何制定", "怎么制定", "如何建立", "怎么建立")) and "流程" in text:
        return True
    if any(token in text for token in ("一般性影响", "一般会带来什么影响", "不用查询具体公司", "不针对具体公司")):
        return True
    if any(token in text for token in ("冷笑话", "讲个笑话", "讲一个笑话")) and any(
        token in text for token in ("忽略前面", "忽略之前", "换个话题", "不聊这个", "告诉我", "讲个", "讲一个")
    ):
        return True
    if any(token in text for token in ("不需要寻源", "不需要找供应商", "不用寻源", "不做寻源")) and any(
        token in text for token in ("算一下", "计算", "天气", "笑话", "翻译", "代码")
    ):
        return True
    return False


def is_translation_request(message: str) -> bool:
    """Identify an explicit request to translate user-supplied text."""
    text = str(message or "").strip().lower()
    return any(
        token in text
        for token in ("翻译", "translate", "英文怎么说", "英语怎么说", "英文如何表达", "英语如何表达")
    )


def is_out_of_scope_request(message: str) -> bool:
    """Identify explicit utility or entertainment requests outside procurement."""
    text = str(message or "").strip()
    if not text:
        return False
    return any(
        token in text
        for token in (
            "冷笑话", "讲个笑话", "讲一个笑话", "讲笑话", "来个笑话",
            "算一下", "计算一下", "天气怎么样",
        )
    )


def is_general_supplier_knowledge_question(message: str) -> bool:
    """Identify general supplier-risk method questions that need no business data."""
    text = str(message or "").strip().lower()
    if not text or not any(token in text for token in ("供应商", "供应链", "采购")):
        return False
    if not any(token in text for token in ("风险", "评估", "管理", "指标", "因素", "维度")):
        return False
    return any(token in text for token in (
        "一般", "通常", "通用", "哪些指标", "什么指标", "哪些因素", "哪些维度",
        "看什么", "关注什么", "怎么评估", "如何评估", "评估方法", "评估流程",
        "如何建立", "怎么建立", "评估体系",
    ))


def is_explicit_no_task(intent: object) -> bool:
    """Return whether extraction explicitly classified an empty turn as no task."""
    if isinstance(intent, dict):
        provided_fields = set(intent)
        get_value = intent.get
    else:
        provided_fields = set(getattr(intent, "model_fields_set", set()))
        get_value = lambda key, default=None: getattr(intent, key, default)
    if not {"capability", "task_type"}.issubset(provided_fields):
        return False
    return (
        get_value("capability", "none") == "none"
        and get_value("task_type", "none") == "none"
        and not get_value("target_supplier_names", [])
        and not get_value("analysis_dimensions", [])
        and not get_value("provider_capabilities", [])
        and get_value("sourcing_requirement") is None
        and get_value("requested_action", "none") == "none"
        and not get_value("needs_clarification", False)
        and not get_value("missing_fields", [])
    )


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
    primary_dimension = _CAPABILITY_PRIMARY_DIMENSIONS.get(normalized_capability)
    if primary_dimension:
        # A unique explicit capability is the user's scope. Keep the model's
        # primary dimension, but discard unrequested dimensions that could
        # otherwise launch extra tools (for example business_risk for finance).
        return [primary_dimension]
    return [item for item in normalized_dimensions if item != "risk"]
