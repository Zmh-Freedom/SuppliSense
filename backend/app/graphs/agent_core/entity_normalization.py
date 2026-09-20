"""Shared normalization for company mentions extracted from chat turns."""

from __future__ import annotations

import re
import unicodedata


# Keep the longest conversational prefixes first.  These are user-facing
# fillers, not part of a legal entity name; using one shared list prevents the
# conversation-state and entity-memory resolvers from drifting apart.
_COMPANY_MENTION_PREFIXES = tuple(sorted(
    {
        "请帮我分析一下", "帮我分析一下", "请复核一下", "复核一下", "请分析一下", "分析一下",
        "请评估一下", "评估一下", "请查询一下", "查询一下", "请查看一下", "查看一下",
        "请核查一下", "核查一下", "请查找一下", "查找一下", "请预测一下", "预测一下",
        "请确认一下", "确认一下", "请生成一下", "生成一下", "请对比一下", "对比一下",
        "比较一下", "监控一下", "看看一下", "那先看一下", "先看一下", "那先看", "先看",
        "请看一下", "看一下", "看看", "先查一下", "先查", "展示一下", "展示", "显示一下", "显示",
        "请每周生成一次", "每周生成一次", "请每月生成一次", "每月生成一次",
        "请每日生成一次", "每日生成一次", "定期生成一次", "定期生成",
        "每周生成", "每月生成", "每日生成",
        "请复核", "复核", "请分析", "分析", "请评估", "评估", "请查询", "查询", "请查看", "查看",
        "请核查", "核查", "请查找", "查找", "请预测", "预测", "请确认", "确认", "请生成", "生成",
        "请对比", "对比", "比较", "请对", "对", "将", "把", "比", "请", "帮我",
        "是", "给", "为", "替",
    },
    key=len,
    reverse=True,
))

_LEADING_FILLER_RE = re.compile(
    r"^(?:(?:请|帮我|麻烦)\s*)?(?:查看|查询|查找|分析|评估|复核|监控|看看|先查|展示|显示|核查|预测|确认|生成|对比|比较|处理)\s*(?:一下|下)?\s*"
    r"|^(?:一下|下)\s*"
)


def normalize_company_mention(value: str) -> str:
    """Normalize a company mention while preserving the legal name itself."""
    text = unicodedata.normalize("NFKC", str(value or ""))
    # Stored supplier names use full-width Chinese punctuation.  NFKC is still
    # useful for matching, but keep the display form stable for downstream
    # exact-name lookups and evidence labels.
    text = text.replace("(", "（").replace(")", "）")
    text = re.sub(r"[\s\u3000]+", "", text).strip()
    previous = None
    while text and text != previous:
        previous = text
        text = _LEADING_FILLER_RE.sub("", text, count=1).strip()
        for prefix in _COMPANY_MENTION_PREFIXES:
            if text.startswith(prefix):
                text = text[len(prefix):].strip()
                break
    return text.strip(" ：:，,。？！!?\t")
