"""程序化澄清检测 — 检测用户输入是否缺少必要信息，提前返回澄清问题。"""

import re
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

# 这些场景无需企业名：监控清单整体分析、寻源推荐等
_NO_COMPANY_NEEDED_KEYWORDS = [
    # 监控清单整体分析（必须含"清单"或"监控"上下文，避免误伤单企业查询）
    "监控清单", "监控列表", "清单趋势", "清单风险", "清单分析",
    # 寻源场景
    "找供应商", "寻源", "找几家", "推荐供应商", "帮我找", "帮我推荐",
    "有什么推荐", "有没有什么", "有什么合适", "推荐几",
]

# 代词前缀：这些开头的企业名候选应被拒绝（"该公司"/"本公司" 等）
_PRONOUN_PREFIXES = ("该", "本", "贵", "此", "那", "这")

# 分词用的标点和空白
_TOKEN_SEPARATORS = r"[，,。、；;:：\s（）()【】\[\]+和及与/]+"

# 分析动词前后缀，用于从 token 中剥离出可能的企业简称
_PREFIXES = ["那先看一下", "先看一下", "那先看", "先看", "帮我分析", "帮我评估", "帮我查", "帮我看看",
             "分析", "评估", "查询", "查看", "看看", "帮我"]
_TRAILINGS = ["的风险情况", "的风险", "的财务", "的舆情", "的ESG", "的情况", "的数据",
              "怎么样", "如何"]
_FILLERS = ["一下", "下", "的"]

# 这些词出现在 token 中间时也剥离（多角度/多维度等场景词）
_INNER_NOISE = ["多角度", "多维度", "分别", "同时", "全面", "综合", "深度",
                "风险", "舆情", "合规", "财务", "ESG", "制裁", "经营", "司法",
                "宏观", "esg",
                "分析", "评估", "查询", "查看", "看看", "对比", "比较"]


@dataclass
class ClarificationNeeded:
    message: str
    missing: list[str] = field(default_factory=list)


def _has_full_company_name(msg: str) -> bool:
    """消息中是否包含完整企业名（带后缀）。"""
    return any(pat in msg for pat in _COMPANY_PATTERNS)


def _extract_company_candidate(msg: str) -> str | None:
    """从消息中提取看起来像企业简称的 token（3-10 个纯汉字且不含分析关键词）。"""
    tokens = re.split(_TOKEN_SEPARATORS, msg)
    for token in tokens:
        for prefix in _PREFIXES:
            if token.startswith(prefix):
                token = token[len(prefix):]
        for noise in _INNER_NOISE:
            token = token.replace(noise, "")
        for trailing in _TRAILINGS:
            token = token.replace(trailing, "")
        for filler in _FILLERS:
            token = token.replace(filler, "")
        token = token.strip()
        if 3 <= len(token) <= 10 and all('一' <= c <= '鿿' for c in token):
            if not any(kw in token for kw in _ANALYSIS_KEYWORDS):
                # 排除"该公司"/"本公司"等代词性候选
                if token.startswith(_PRONOUN_PREFIXES):
                    continue
                return token
    return None


def detect_clarification_needed(
    message: str,
    known_company_names: list[str] | None = None,
) -> ClarificationNeeded | None:
    """检测用户输入是否缺少必要信息。

    规则：
    - 含风险/分析关键词但无公司名 → 询问公司名称
    - 监控清单整体分析、寻源推荐等场景 → 不需要公司名，跳过
    - 短消息（< 5 字）且非问候语 → 不拦截（让 Agent 处理）
    """
    msg = message.strip()

    # Skip short messages (greetings, simple queries)
    if len(msg) < 5:
        return None

    # Explicitly referenced entities from the current session satisfy the guard.
    if any(name and name in msg for name in (known_company_names or [])) or (
        known_company_names
        and any(
            token in msg
            for token in (
                "它",
                "这家",
                "两家",
                "这两家",
                "两家公司",
                "这两家公司",
                "两家供应商",
                "这两家供应商",
                "该供应商",
                "该企业",
            )
        )
    ):
        return None

    # 白名单：监控清单整体分析、寻源推荐等场景不需要公司名
    if any(kw in msg for kw in _NO_COMPANY_NEEDED_KEYWORDS):
        return None

    # 含完整企业名后缀 → 不拦截
    if _has_full_company_name(msg):
        return None

    # 任意 token 看起来像企业简称 → 不拦截（让 Agent / search_company 处理）
    if _extract_company_candidate(msg) is not None:
        return None

    # Message has analysis intent but no identifiable company name
    has_intent = any(kw in msg for kw in _ANALYSIS_KEYWORDS)
    if has_intent:
        return ClarificationNeeded(
            message="请问您想分析哪家公司？请提供公司全称或简称。",
            missing=["company_name"],
        )

    return None
