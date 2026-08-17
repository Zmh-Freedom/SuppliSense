"""Local-first, side-effect-free supplier candidate discovery."""

import re
from concurrent.futures import ThreadPoolExecutor
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

import httpx
from bs4 import BeautifulSoup

from app.core.config import settings
from app.core.logging import get_logger
from app.domains.sourcing.supplier_repo import search_for_sourcing_v2

logger = get_logger()

_TIANYANCHA_CATEGORY_CODES: dict[str, tuple[str, ...]] = {
    "steel": ("311", "312", "313", "331"),  # 炼铁、炼钢、钢压延加工、结构性金属制品
    "motor": ("381",),  # 电机制造
    "display": ("395", "397", "398", "399"),  # 视听设备、电子器件/元件及其他电子设备
}


def search_local_suppliers(requirement: dict, policy: dict) -> list[dict]:
    """Search the local library; policy is accepted for a stable orchestration seam."""
    del policy
    return search_for_sourcing_v2(requirement)


def search_external_provider(requirement: dict) -> list[dict]:
    """Discover unverified supplier leads from Tianyancha and public web search.

    Both providers are read-only and queried after local discovery is
    insufficient. Results are staged and must pass identity and human review
    before import.
    """
    if not settings.SUPPLIER_DISCOVERY_WEB_ENABLED:
        web_candidates: list[dict] = []
    else:
        web_candidates = []

    category = _first_requirement_value(requirement.get("category"))
    specification = _first_requirement_value(requirement.get("specification") or requirement.get("spec"))
    region = _first_requirement_value(requirement.get("region") or requirement.get("region_required"))
    if not category and not specification:
        return []

    def web_search() -> list[dict]:
        return _search_web_candidates(category, specification, region)

    with ThreadPoolExecutor(max_workers=2) as executor:
        web_future = executor.submit(web_search) if settings.SUPPLIER_DISCOVERY_WEB_ENABLED else None
        tyc_future = executor.submit(_search_tianyancha_candidates, category, specification, region)
        if web_future:
            web_candidates = web_future.result()
        tyc_candidates = tyc_future.result()

    logger.info(
        "supplier_external_discovery_completed",
        category=category,
        tianyancha_count=len(tyc_candidates),
        web_count=len(web_candidates),
    )

    merged: list[dict] = []
    seen_names: set[str] = set()
    for candidate in [*tyc_candidates, *web_candidates]:
        name = candidate.get("supplier_name", "")
        if name and name not in seen_names:
            seen_names.add(name)
            merged.append(candidate)
    return merged[: settings.SUPPLIER_DISCOVERY_WEB_MAX_RESULTS]


def _search_web_candidates(category: str, specification: str, region: str) -> list[dict]:
    query = " ".join(part for part in (category, specification, region, "供应商") if part)
    try:
        response = httpx.get(
            "https://www.bing.com/search",
            params={"q": query, "setlang": "zh-Hans", "count": settings.SUPPLIER_DISCOVERY_WEB_MAX_RESULTS},
            headers={"User-Agent": "SuppliSense supplier discovery/1.0"},
            timeout=15.0,
        )
        response.raise_for_status()
    except (httpx.HTTPError, httpx.TimeoutException):
        return []

    soup = BeautifulSoup(response.text, "html.parser")
    candidates: list[dict] = []
    for item in soup.select("li.b_algo"):
        title_node = item.select_one("h2 a")
        if not title_node:
            continue
        title = title_node.get_text(" ", strip=True)
        url = str(title_node.get("href") or "")
        snippet_node = item.select_one(".b_caption p")
        snippet = snippet_node.get_text(" ", strip=True) if snippet_node else ""
        if not _search_result_is_relevant(" ".join((title, snippet)), category, specification):
            continue
        company_name = _extract_company_name(" ".join((title, snippet)))
        if not company_name:
            continue
        contact = _extract_contact_fields(url, snippet)
        candidates.append({
            "supplier_name": company_name,
            "categories": [category] if category else [],
            "regions": [region] if region else [],
            "source": "web_search",
            "source_type": "public_web_search",
            "source_reference": url,
            **contact,
            "source_title": title,
            "source_snippet": snippet[:500],
            "verification_status": "unverified",
            "match_reasons": [f"web_search:{query}"],
        })
        if len(candidates) >= settings.SUPPLIER_DISCOVERY_WEB_MAX_RESULTS:
            break
    if candidates:
        return candidates
    return _search_duckduckgo_candidates(category, specification, region)


def _search_duckduckgo_candidates(category: str, specification: str, region: str) -> list[dict]:
    query = " ".join(part for part in (category, specification, region, "供应商") if part)
    try:
        response = httpx.get(
            "https://html.duckduckgo.com/html/",
            params={"q": query},
            headers={"User-Agent": "SuppliSense supplier discovery/1.0"},
            timeout=15.0,
        )
        response.raise_for_status()
    except (httpx.HTTPError, httpx.TimeoutException):
        return []

    soup = BeautifulSoup(response.text, "html.parser")
    candidates: list[dict] = []
    for item in soup.select(".result"):
        title_node = item.select_one(".result__title a")
        if not title_node:
            continue
        title = title_node.get_text(" ", strip=True)
        snippet_node = item.select_one(".result__snippet")
        snippet = snippet_node.get_text(" ", strip=True) if snippet_node else ""
        if not _search_result_is_relevant(" ".join((title, snippet)), category, specification):
            continue
        company_name = _extract_company_name(" ".join((title, snippet)))
        if not company_name:
            continue
        url = _normalise_result_url(str(title_node.get("href") or ""))
        contact = _extract_contact_fields(url, snippet)
        candidates.append({
            "supplier_name": company_name,
            "categories": [category] if category else [],
            "regions": [region] if region else [],
            "source": "web_search",
            "source_type": "duckduckgo_html",
            "source_reference": url,
            **contact,
            "source_title": title,
            "source_snippet": snippet[:500],
            "verification_status": "unverified",
            "match_reasons": [f"web_search:{query}"],
        })
        if len(candidates) >= settings.SUPPLIER_DISCOVERY_WEB_MAX_RESULTS:
            break
    return candidates


def _search_result_is_relevant(text: str, category: str, specification: str) -> bool:
    normalized = text.casefold().replace(" ", "")
    terms = [term.casefold().replace(" ", "") for term in (category, specification) if term]
    return any(term and term in normalized for term in terms)


def _search_tianyancha_candidates(category: str, specification: str, region: str) -> list[dict]:
    """Search Tianyancha without importing or enriching any supplier record."""
    from app.services.tianyancha_client import search_companies

    keyword = " ".join(part for part in (category, specification, region if not region.isdigit() else "") if part)
    # 天眼查的 areaCode 不是自然语言地区；自然语言地区放进关键词，避免把“华东”当成编码。
    area_code = region if region.isdigit() else ""
    industry_codes = _industry_codes_for_category(category)
    if not industry_codes:
        industry_codes = ("",)

    def search_by_code(industry_code: str) -> tuple[str, dict | None]:
        return industry_code, search_companies(
            keyword=keyword,
            industry=industry_code,
            region=area_code,
            page_size=settings.SUPPLIER_DISCOVERY_WEB_MAX_RESULTS,
        )

    with ThreadPoolExecutor(max_workers=min(4, len(industry_codes))) as executor:
        responses = list(executor.map(search_by_code, industry_codes))

    candidates: list[dict] = []
    seen_names: set[str] = set()
    for industry_code, response in responses:
        if not response:
            logger.info(
                "supplier_tianyancha_discovery_empty",
                category=category,
                specification=specification,
                industry_code=industry_code or "keyword_only",
            )
            continue
        for item in response.get("items", []):
            name = str(item.get("name") or "").strip()
            if not name or name in seen_names:
                continue
            seen_names.add(name)
            unified_code = item.get("regNumber") or item.get("unifiedSocialCreditCode")
            contact = _extract_contact_fields(
                str(item.get("website") or item.get("webSite") or item.get("url") or ""),
                str(item.get("base") or item.get("regLocation") or ""),
                phone=str(item.get("phone") or item.get("phoneNumber") or ""),
                email=str(item.get("email") or ""),
            )
            candidates.append({
                "supplier_name": name,
                "categories": [category] if category else [],
                "regions": [region] if region else [],
                "source": "tianyancha_search",
                "source_type": "tianyancha",
                "source_reference": f"tyc:{unified_code}" if unified_code else "tyc:search",
                **contact,
                "source_title": "天眼查企业搜索",
                "source_snippet": str(item.get("base") or item.get("regLocation") or "")[:500],
                "verification_status": "unverified",
                "match_reasons": [
                    f"tianyancha:{category or specification}",
                    f"industry_code:{industry_code}" if industry_code else "keyword_only",
                ],
            })
            if len(candidates) >= settings.SUPPLIER_DISCOVERY_WEB_MAX_RESULTS:
                return candidates
    return candidates


def _industry_codes_for_category(category: str) -> tuple[str, ...]:
    normalized = category.casefold().replace(" ", "")
    if any(term in normalized for term in ("钢", "金属材料")):
        return _TIANYANCHA_CATEGORY_CODES["steel"]
    if any(term in normalized for term in ("电机", "马达")):
        return _TIANYANCHA_CATEGORY_CODES["motor"]
    if any(term in normalized for term in ("显示屏", "led屏", "液晶屏", "屏幕")):
        return _TIANYANCHA_CATEGORY_CODES["display"]
    return ()


_COMPANY_NAME_PATTERN = re.compile(
    r"([\u4e00-\u9fa5A-Za-z0-9（）()·&\-]{2,40}"
    r"(?:有限公司|股份有限公司|有限责任公司|股份公司))"
)


def _extract_company_name(text: str) -> str | None:
    """Extract only names with a legal-company suffix; never treat a page title as a company."""
    match = _COMPANY_NAME_PATTERN.search(text)
    return match.group(1).strip() if match else None


def _normalise_result_url(url: str) -> str:
    """Unwrap DuckDuckGo redirect URLs while keeping source traceability."""
    if "uddg=" not in url:
        return url
    parsed = urlparse(url)
    return unquote(parse_qs(parsed.query).get("uddg", [url])[0])


def _extract_contact_fields(
    url: str,
    text: str,
    *,
    phone: str = "",
    email: str = "",
) -> dict[str, str]:
    """Return contact fields with explicit verification semantics."""
    phone_match = re.search(r"(?:\+86[- ]?)?1[3-9]\d{9}|0\d{2,3}[- ]?\d{7,8}", phone or text)
    email_match = re.search(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", email or text)
    website_url = url if _is_direct_web_url(url) else ""
    return {
        "website_url": website_url,
        "website_status": "unverified" if website_url else "not_found",
        "contact_phone": phone_match.group(0) if phone_match else "",
        "contact_email": email_match.group(0) if email_match else "",
        "contact_status": "unverified" if phone_match or email_match else "not_found",
    }


def _is_direct_web_url(url: str) -> bool:
    if not url.startswith(("http://", "https://")):
        return False
    host = (urlparse(url).hostname or "").casefold()
    return host not in {"bing.com", "www.bing.com", "duckduckgo.com", "html.duckduckgo.com"}


def _first_requirement_value(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    return next((item.strip() for item in value.replace("，", ",").split(",") if item.strip()), "")


def discover_local_candidates(requirement: dict, policy: dict) -> list[dict]:
    """Return local candidates only for callers that must never query providers."""
    return search_local_suppliers(requirement, policy)


def is_candidate_supply_sufficient(
    candidates: list[dict], requirement: dict, policy: dict
) -> bool:
    """Require both the configured count and representation of each constraint."""
    if len(candidates) < policy["minimum_candidate_count"]:
        return False

    for field, candidate_field in (
        ("category", "categories"),
        ("specification", "specifications"),
        ("region", "regions"),
        ("qualifications", "qualifications"),
    ):
        for value in _requirement_values(requirement.get(field)):
            if not any(_contains_value(candidate.get(candidate_field), value) for candidate in candidates):
                return False
    return True


def stage_external_candidates(run_id: str, candidates: list[dict]) -> list[dict]:
    """Mark provider findings as review-only without resolving or creating identities."""
    return [
        {
            **candidate,
            **({"run_id": run_id} if run_id else {}),
            "status": "staged_candidate",
            "supplier_id": None,
            "company_id": None,
        }
        for candidate in candidates
    ]


def discover_candidates(requirement: dict, policy: dict) -> dict:
    """Prefer sufficient local results and preserve them when external fallback is used."""
    local_candidates = discover_local_candidates(requirement, policy)
    if is_candidate_supply_sufficient(local_candidates, requirement, policy):
        return {
            "source": "local",
            "local_candidates": local_candidates,
            "external_candidates": [],
            "external_status": "not_required",
        }

    try:
        external_candidates = stage_external_candidates(
            str(requirement.get("run_id", "")), search_external_provider(requirement)
        )
    except Exception as exc:
        return {
            "source": "local",
            "local_candidates": local_candidates,
            "external_candidates": [],
            "external_status": f"failed:{exc.__class__.__name__}",
        }
    return {
        "source": "local_and_external",
        "local_candidates": local_candidates,
        "external_candidates": external_candidates,
        "external_status": "staged",
    }


def _requirement_values(value: Any) -> list[str]:
    if not isinstance(value, str):
        return []
    return [item.strip() for item in value.replace("，", ",").split(",") if item.strip()]


def _contains_value(values: Any, requested: str) -> bool:
    if isinstance(values, str):
        values = [values]
    if not isinstance(values, (list, tuple)):
        return False
    normalized = requested.casefold()
    return any(isinstance(value, str) and normalized in value.casefold() for value in values)
