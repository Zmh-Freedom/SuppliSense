"""程序化澄清检测 — 检测用户输入是否缺少必要信息，提前返回澄清问题。"""

import re
from dataclasses import dataclass, field

from app.services.conversation_state import resolve_supplier_target_selection

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


def review_scope_clarification(
    target_names: list[str],
    references: list[dict],
    user_id: str,
    user_role: str,
    message: str,
) -> ClarificationNeeded | None:
    """Keep procurement review inside the user's formal supplier scope.

    External enterprise assessment remains available through an explicit
    ``评估`` request; ``复核`` must refer to a monitored or formal supplier.
    """
    if not target_names:
        return None
    # Procurement review is intentionally fail-closed for every risk-analysis
    # wording.  Only an explicit external-assessment request ("评估") may
    # proceed through the separate identity-search flow below.
    analysis_request = any(
        token in str(message or "")
        for token in ("复核", "分析", "风险", "评分", "财务", "舆情", "合规", "查询")
    )
    if not analysis_request or "评估" in str(message or ""):
        return None

    from app.domains.alert.service import get_watchlist_targets
    from app.domains.supplier.access import (
        can_access_formal_supplier_name,
        formal_supplier_exists_by_name,
    )

    visible_monitor_names = {
        str(item.get("company_name") or item.get("display_name") or "").strip().casefold()
        for item in get_watchlist_targets(user_id=user_id, user_role=user_role)
        if isinstance(item, dict)
    }
    out_of_scope: list[str] = []
    missing: list[str] = []
    for raw_name in target_names:
        name = str(raw_name).strip()
        folded = name.casefold()
        # Conversation references are context for name resolution only. They
        # may originate from an old answer or an external sourcing candidate
        # and therefore cannot authorize a risk assessment by themselves.
        if folded in visible_monitor_names:
            continue
        if formal_supplier_exists_by_name(name):
            access = can_access_formal_supplier_name(name, user_id, user_role)
            if access is True or user_role == "admin":
                continue
            out_of_scope.append(name)
            continue
        missing.append(name)

    if out_of_scope:
        return ClarificationNeeded(
            message=(
                f"“{out_of_scope[0]}”是正式供应商，但不在你当前负责范围内，无法执行采购复核。"
                "如需查看，请联系所属科室经理或管理员。"
            ),
            missing=["supplier_scope"],
        )
    if missing:
        if "复核" not in str(message or ""):
            return ClarificationNeeded(
                message=(
                    f"“{missing[0]}”不在当前责任范围的监控清单或正式供应商库中，"
                    "因此不能直接生成供应商风险评分。若要调查外部企业，请改为“评估该企业风险”，"
                    "系统会先搜索并确认主体。"
                ),
                missing=["formal_supplier_or_monitor_target"],
            )
        return ClarificationNeeded(
            message=(
                f"当前责任范围的监控清单和正式供应商库中均未找到“{missing[0]}”，因此无法按供应商复核流程分析。"
                "如果你想调查外部企业，请改为“评估该企业风险”，系统会先搜索并确认主体。"
            ),
            missing=["formal_supplier_or_monitor_target"],
        )
    return None


def external_assessment_clarification(
    target_names: list[str],
    references: list[dict],
    message: str,
) -> ClarificationNeeded | None:
    """Resolve an external assessment to a concrete company before scoring."""
    if "评估" not in message or not target_names:
        return None
    known_names = {
        str(reference.get("name") or "").strip().casefold()
        for reference in references
        if isinstance(reference, dict) and str(reference.get("name") or "").strip()
    }
    from app.domains.supplier.access import formal_supplier_exists_by_name
    if all(
        str(name).strip().casefold() in known_names
        or formal_supplier_exists_by_name(str(name).strip())
        for name in target_names
    ):
        return None

    from app.domains.risk.tools_search import search_company

    target = str(target_names[0]).strip()
    try:
        result = search_company.invoke({"keyword": target})
    except Exception:
        result = {}
    candidates = result.get("results") if isinstance(result, dict) else []
    candidates = [item for item in candidates if isinstance(item, dict)]
    if not candidates:
        return ClarificationNeeded(
            message=(
                f"暂未找到与“{target}”对应的可确认企业主体，系统已停止风险评分。"
                "当前没有可验证的公开资料，因此不输出风险结论。"
            ),
            missing=["company_identity"],
        )
    preview: list[str] = []
    for item in candidates[:5]:
        name = str(item.get("name") or item.get("company_name") or "").strip()
        if not name:
            continue
        credit_code = str(item.get("credit_code") or item.get("unified_code") or "").strip()
        preview.append(f"{name}{f'（统一社会信用代码：{credit_code}）' if credit_code else ''}")
    return ClarificationNeeded(
        message=(
            f"“{target}”不在当前供应商复核范围内。已找到以下外部主体候选："
            f"{'；'.join(preview)}。请回复要评估的企业全称或统一社会信用代码，确认后系统再查询公开数据并评分。"
        ),
        missing=["company_identity"],
    )


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
    supplier_references: list[dict] | None = None,
    *,
    resolved_target_names: list[str] | None = None,
    has_structured_context: bool = False,
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

    if resolved_target_names:
        return None

    # ConversationState is the authoritative target resolver for a chat turn.
    # Existing structured suppliers are sufficient context for every execution
    # mode; a rule-only preflight must not ask the user to repeat a company name.
    if has_structured_context:
        return None

    references = supplier_references or [
        {"name": name}
        for name in known_company_names or []
        if name
    ]
    resolution = resolve_supplier_target_selection(msg, references)
    if resolution.target_supplier_names:
        return None
    if resolution.needs_clarification:
        return ClarificationNeeded(
            message="请问您想分析哪家公司？请先提供企业名称或先完成供应商寻源。",
            missing=["target_supplier_name"],
        )
    if any(name and name in msg for name in (known_company_names or [])):
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
            missing=["target_supplier_name"],
        )

    return None
