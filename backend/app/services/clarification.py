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
    # Step 1: Check full company suffix pattern
    # Step 2: Remove common prefixes (分析/评估/查询) + suffixes and check if remaining text
    #         contains a Chinese word that looks like a company abbreviation
    clean = msg
    for suffix in ["股份有限公司", "有限公司", "有限责任公司"]:
        if suffix in clean:
            return None  # Full company name present
    # Remove prefixes: analysis verbs + helper phrases
    for prefix in ["帮我分析", "帮我评估", "帮我查", "帮我看看", "分析", "评估", "查询", "查看", "看看", "帮我"]:
        if clean.startswith(prefix):
            clean = clean[len(prefix):]
    # Remove common trailing words
    for trailing in ["的风险", "的财务", "的舆情", "的ESG", "的情况", "的数据", "怎么样", "如何"]:
        clean = clean.replace(trailing, "")
    # Remove 一下 and similar filler
    for filler in ["一下", "下", "的"]:
        clean = clean.replace(filler, "")
    # After cleanup, if short and looks like a proper noun (3-10 pure Chinese chars), let LLM handle
    clean = clean.strip()
    if 3 <= len(clean) <= 10 and all('一' <= c <= '鿿' for c in clean):
        if not any(kw in clean for kw in _ANALYSIS_KEYWORDS):
            return None

    # Message has analysis intent but no identifiable company name
    has_intent = any(kw in msg for kw in _ANALYSIS_KEYWORDS)
    if has_intent:
        return ClarificationNeeded(
            message="请问您想分析哪家公司？请提供公司全称或简称。",
            missing=["company_name"],
        )

    return None
