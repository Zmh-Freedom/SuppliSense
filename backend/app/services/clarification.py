"""程序化澄清检测 — 检测用户输入是否缺少必要信息，提前返回澄清问题。"""

from dataclasses import dataclass, field

# Chinese company name pattern
_COMPANY_PATTERNS = [
    "有限公司", "股份", "集团", "有限责任",
]

# Keywords that indicate the user wants analysis/assessment
_ANALYSIS_KEYWORDS = [
    "分析", "评估", "风险", "评分", "查询", "预警",
    "财务", "财报", "舆情", "ESG", "合规", "制裁",
    "对比", "比较", "趋势", "报告",
]


@dataclass
class ClarificationNeeded:
    message: str
    missing: list[str] = field(default_factory=list)


def detect_clarification_needed(message: str) -> ClarificationNeeded | None:
    """检测用户输入是否缺少必要信息。

    规则：
    - 含风险/分析关键词但无公司名 → 询问公司名称
    - 短消息（< 5 字）且非问候语 → 不拦截（让 Agent 处理）
    """
    msg = message.strip()

    # Skip short messages (greetings, simple queries)
    if len(msg) < 5:
        return None

    # Skip if message clearly contains a company name
    has_company = any(pat in msg for pat in _COMPANY_PATTERNS)
    if has_company:
        return None

    # Check if the message contains what looks like a company name
    # Strategy 1: Full company suffix pattern
    # Strategy 2: Short Chinese word (3-10 chars) that's not a keyword
    msg_clean = msg.replace("的", " ").replace("，", " ").replace(",", " ").replace("了", " ")
    # Also remove common company suffixes to extract the core name
    for suffix in ["股份有限公司", "有限公司", "有限责任公司"]:
        msg_clean = msg_clean.replace(suffix, " ")
    for w in msg_clean.split():
        if 3 <= len(w) <= 10 and all('一' <= c <= '鿿' for c in w):
            # Looks like a company name abbreviation
            has_analysis_kw = any(kw in w for kw in _ANALYSIS_KEYWORDS)
            if not has_analysis_kw:
                return None

    # Message has analysis intent but no identifiable company name
    has_intent = any(kw in msg for kw in _ANALYSIS_KEYWORDS)
    if has_intent:
        return ClarificationNeeded(
            message="请问您想分析哪家公司？请提供公司全称或简称。",
            missing=["company_name"],
        )

    return None
