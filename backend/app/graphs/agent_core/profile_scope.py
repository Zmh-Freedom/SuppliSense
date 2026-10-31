"""Resolve which company-profile data domains a user explicitly asked about."""

from __future__ import annotations

PROFILE_DOMAINS = ("baseinfo", "holder", "invest", "change", "branch")

_GENERAL_PROFILE_TERMS = (
    "工商资料",
    "工商信息",
    "企业资料",
    "公司资料",
    "企业信息",
    "公司信息",
)

_DOMAIN_TERMS = {
    "baseinfo": (
        "法定代表人",
        "法人",
        "注册资本",
        "注册地址",
        "登记状态",
        "统一社会信用代码",
        "信用代码",
        "所属行业",
        "成立日期",
        "成立时间",
        "企业名称",
        "公司名称",
        "联系方式",
        "联系人",
        "电话",
        "官网",
        "邮箱",
    ),
    "holder": ("股东", "持股", "股权结构", "出资"),
    "invest": ("对外投资", "投资企业", "被投资"),
    "change": ("历史变更", "变更记录", "工商变更", "法人变更"),
    "branch": ("分支机构", "分公司"),
}

_COLLECTION_DOMAINS = {
    "baseinfo": "baseinfo",
    "holder": "holder",
    "holder_names": "holder",
    "invest": "invest",
    "changeinfo": "change",
    "branch": "branch",
}


def requested_profile_domains(user_message: str) -> set[str]:
    """Return explicitly requested domains; an empty set means no narrower scope."""
    text = str(user_message or "").strip().casefold()
    if not text:
        return set()
    if any(term.casefold() in text for term in _GENERAL_PROFILE_TERMS):
        return set(PROFILE_DOMAINS)
    return mentioned_profile_domains(text)


def mentioned_profile_domains(text: str) -> set[str]:
    """Return profile domains named in text without treating general profile terms as all domains."""
    normalized = str(text or "").strip().casefold()
    if not normalized:
        return set()
    return {
        domain
        for domain, terms in _DOMAIN_TERMS.items()
        if any(term.casefold() in normalized for term in terms)
    }


def profile_collection_domain(collection: str) -> str | None:
    """Map a Tianyancha profile collection key to the stable UI domain key."""
    return _COLLECTION_DOMAINS.get(str(collection or "").strip().casefold())


def profile_claim_domain(fact_path: str | None) -> str | None:
    """Map a validated profile Claim fact path to its data domain."""
    path = str(fact_path or "").strip().casefold()
    if path.startswith("profile."):
        collection = path.split(".", 2)[1] if path.count(".") >= 1 else ""
        return profile_collection_domain(collection)
    if path.startswith("counts."):
        collection = path.split(".", 2)[1] if path.count(".") >= 1 else ""
        return profile_collection_domain(collection)
    return None


__all__ = [
    "PROFILE_DOMAINS",
    "mentioned_profile_domains",
    "profile_claim_domain",
    "profile_collection_domain",
    "requested_profile_domains",
]
