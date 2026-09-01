"""Local-first, side-effect-free supplier candidate discovery."""

import re
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Any
from urllib.parse import parse_qs, unquote, urljoin, urlparse

import httpx
from bs4 import BeautifulSoup

from app.core.config import settings
from app.core.logging import get_logger
from app.domains.sourcing.supplier_repo import search_for_sourcing_v2
from app.graphs.agent_core.contracts import LoopState
from app.graphs.agent_core.loop import build_tool_fingerprint, evaluate_loop

logger = get_logger()

_TIANYANCHA_CATEGORY_CODES: dict[str, tuple[str, ...]] = {
    "steel": ("311", "312", "313", "331"),  # 炼铁、炼钢、钢压延加工、结构性金属制品
    "motor": ("381",),  # 电机制造
    "display": ("395", "397", "398", "399"),  # 视听设备、电子器件/元件及其他电子设备
}
_SEARCH_PROVIDER_HOSTS = {"bing.com", "www.bing.com", "duckduckgo.com", "html.duckduckgo.com"}
_DIRECTORY_HOSTS = {
    "tianyancha.com", "www.tianyancha.com", "qcc.com", "www.qcc.com", "aiqicha.baidu.com",
    "baike.baidu.com", "1688.com", "www.1688.com", "alibaba.com", "www.alibaba.com",
}
_CONTACT_PAGE_TERMS = ("contact", "contact-us", "联系我们", "联系方式", "关于我们", "about")


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
            try:
                web_candidates = web_future.result()
            except Exception as exc:
                logger.warning("supplier_web_discovery_failed", error=type(exc).__name__)
        try:
            tyc_candidates = tyc_future.result()
        except Exception as exc:
            logger.warning("supplier_tianyancha_discovery_failed", error=type(exc).__name__)

    logger.info(
        "supplier_external_discovery_completed",
        category=category,
        tianyancha_count=len(tyc_candidates),
        web_count=len(web_candidates),
    )

    merged = _merge_external_candidates([*tyc_candidates, *web_candidates])
    merged = _verify_web_candidates_with_tianyancha(merged)
    limited = merged[: settings.SUPPLIER_DISCOVERY_WEB_MAX_RESULTS]
    return _enrich_external_contacts(limited)


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
        company_name = _extract_company_name(title) or _extract_company_name(snippet)
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
        company_name = _extract_company_name(title) or _extract_company_name(snippet)
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
    r"(?:股份有限公司|有限责任公司|有限公司|股份公司))"
)

_COMPANY_NAME_NOISE = re.compile(r"(?:推荐|包括|例如|名单|网站首页|首页|关于我们|产品中心)")


def _extract_company_name(text: str) -> str | None:
    """Extract a legal company name after removing common search-title prefixes."""
    if not text:
        return None
    segments = re.split(r"[|｜—–:：,，。；;!?！？/／<>《》【】\[\]]|(?<![A-Za-z0-9])[-_]", text)
    for segment in reversed(segments):
        cleaned = _COMPANY_NAME_NOISE.split(segment)[-1].strip()
        matches = list(_COMPANY_NAME_PATTERN.finditer(cleaned))
        if matches:
            return matches[-1].group(1).strip(" -_")
    return None


def _normalise_result_url(url: str) -> str:
    """Unwrap DuckDuckGo redirect URLs while keeping source traceability."""
    if "uddg=" not in url:
        return url
    parsed = urlparse(url)
    return unquote(parse_qs(parsed.query).get("uddg", [url])[0])


def _merge_external_candidates(candidates: list[dict]) -> list[dict]:
    """Merge duplicate leads without losing richer web contact data."""
    merged: list[dict] = []
    by_name: dict[str, dict] = {}
    for candidate in candidates:
        name = str(candidate.get("supplier_name") or "").strip()
        if not name:
            continue
        existing = by_name.get(name)
        if not existing:
            item = dict(candidate)
            by_name[name] = item
            merged.append(item)
            continue
        for field in ("website_url", "contact_phone", "contact_email"):
            if not existing.get(field) and candidate.get(field):
                existing[field] = candidate[field]
        references = [value for value in (existing.get("source_reference"), candidate.get("source_reference")) if value]
        if references:
            existing["source_references"] = list(dict.fromkeys(references))
    return merged


def _verify_web_candidates_with_tianyancha(candidates: list[dict]) -> list[dict]:
    """Verify web-discovered identities through Tianyancha without writing data.

    Web search discovers leads, while Tianyancha is used as the read-only
    identity evidence source.  No supplier/company document is created here;
    the result remains a review-only candidate until a human approves it.
    """
    verifiable_candidates = [
        candidate
        for candidate in candidates
        if candidate.get("source") in {"web_search", "tianyancha_search"}
        and candidate.get("supplier_name")
    ]
    if not verifiable_candidates:
        return candidates

    def verify(index: int, candidate: dict) -> tuple[int, dict]:
        return index, _verify_web_candidate_with_tianyancha(candidate)

    verified = list(candidates)
    with ThreadPoolExecutor(max_workers=min(4, len(verifiable_candidates))) as executor:
        futures = [
            executor.submit(verify, index, candidate)
            for index, candidate in enumerate(candidates)
            if candidate.get("source") in {"web_search", "tianyancha_search"}
            and candidate.get("supplier_name")
        ]
        for future in futures:
            try:
                index, candidate = future.result()
            except Exception as exc:
                logger.warning("supplier_tianyancha_verification_failed", error=type(exc).__name__)
                continue
            verified[index] = candidate

    logger.info(
        "supplier_tianyancha_verification_completed",
        candidate_count=len(verifiable_candidates),
        exact_count=sum(item.get("identity_status") == "exact" for item in verified),
        probable_count=sum(item.get("identity_status") == "probable" for item in verified),
    )
    return verified


def _verify_web_candidate_with_tianyancha(candidate: dict) -> dict:
    """Attach bounded Tianyancha identity evidence to one web lead."""
    from app.services.tianyancha_client import search_companies

    result = dict(candidate)
    name = str(result.get("supplier_name") or "").strip()
    result["identity_status"] = "unavailable"
    result["identity_confidence"] = 0.0
    result["tianyancha_verified"] = False
    result["verification_reasons"] = []
    if not name:
        result["identity_status"] = "not_found"
        return result

    response = search_companies(keyword=name, page_size=5)
    if not response:
        result["verification_reasons"] = ["天眼查查询不可用或未返回结果"]
        return result

    items = [item for item in response.get("items", []) if isinstance(item, dict)]
    exact_matches = [
        item for item in items
        if _normalise_company_name(item.get("name")) == _normalise_company_name(name)
    ]
    if len(exact_matches) == 1:
        return _apply_tianyancha_identity(result, exact_matches[0], "exact")
    if len(exact_matches) > 1:
        result["identity_status"] = "ambiguous"
        result["verification_reasons"] = ["天眼查返回多个同名企业"]
        return result

    probable_matches = [
        item for item in items
        if _company_name_contains(item.get("name"), name)
    ]
    if len(probable_matches) == 1:
        return _apply_tianyancha_identity(result, probable_matches[0], "probable")
    if len(probable_matches) > 1:
        result["identity_status"] = "ambiguous"
        result["verification_reasons"] = ["天眼查返回多个相近企业，无法唯一确认"]
        return result

    result["identity_status"] = "not_found"
    result["verification_reasons"] = ["天眼查未找到名称匹配的企业"]
    return result


def _apply_tianyancha_identity(candidate: dict, item: dict, status: str) -> dict:
    """Copy only identity evidence; never persist or promote a supplier."""
    result = dict(candidate)
    unified_code = _first_candidate_value(
        item, "unifiedSocialCreditCode", "regNumber", "creditCode"
    )
    result.update({
        "identity_status": status,
        "identity_confidence": 0.98 if status == "exact" else 0.82,
        "tianyancha_verified": status == "exact",
        "tianyancha_company_name": str(item.get("name") or "").strip(),
        "tianyancha_unified_social_credit_code": unified_code,
        "tianyancha_legal_person": _first_candidate_value(item, "legalPersonName", "legalPerson"),
        "tianyancha_registration_status": _first_candidate_value(item, "regStatus", "status"),
        "tianyancha_registered_capital": _first_candidate_value(item, "regCapital", "registeredCapital"),
        "tianyancha_registered_address": _first_candidate_value(item, "regLocation", "address"),
        "verification_reasons": [
            "天眼查企业名称精确匹配" if status == "exact" else "天眼查企业名称近似匹配",
            "已获取天眼查企业身份信息" if unified_code else "天眼查未返回统一社会信用代码",
        ],
    })
    if unified_code:
        result["tianyancha_source_reference"] = f"tyc:{unified_code}"
    return result


def _normalise_company_name(value: Any) -> str:
    """Normalize legal names for conservative identity comparison."""
    return re.sub(r"[\s（）()·\-—_，,。]", "", str(value or "")).casefold()


def _company_name_contains(left: Any, right: Any) -> bool:
    normalized_left = _normalise_company_name(left)
    normalized_right = _normalise_company_name(right)
    return bool(normalized_left and normalized_right and (
        normalized_left in normalized_right or normalized_right in normalized_left
    ))


def _first_candidate_value(item: dict, *keys: str) -> str | None:
    for key in keys:
        value = str(item.get(key) or "").strip()
        if value:
            return value
    return None


def _enrich_external_contacts(candidates: list[dict]) -> list[dict]:
    """Enrich a bounded number of staged leads with read-only provider and web details."""
    limit = min(len(candidates), settings.SUPPLIER_DISCOVERY_CONTACT_ENRICHMENT_MAX_CANDIDATES)
    if limit <= 0:
        return candidates

    def enrich(index: int, candidate: dict) -> tuple[int, dict]:
        return index, _enrich_external_candidate(candidate)

    enriched = list(candidates)
    with ThreadPoolExecutor(max_workers=min(4, limit)) as executor:
        futures = [executor.submit(enrich, index, candidate) for index, candidate in enumerate(candidates[:limit])]
        for future in futures:
            try:
                index, candidate = future.result()
            except Exception as exc:
                logger.warning("supplier_contact_enrichment_failed", error=type(exc).__name__)
                continue
            enriched[index] = candidate
    logger.info(
        "supplier_contact_enrichment_completed",
        candidate_count=limit,
        website_count=sum(bool(candidate.get("website_url")) for candidate in enriched[:limit]),
        phone_count=sum(bool(candidate.get("contact_phone")) for candidate in enriched[:limit]),
        email_count=sum(bool(candidate.get("contact_email")) for candidate in enriched[:limit]),
    )
    return enriched


def _enrich_external_candidate(candidate: dict) -> dict:
    """Add public contact evidence without changing staged supplier identity or status."""
    result = dict(candidate)
    name = str(result.get("supplier_name") or "")
    source = str(result.get("source") or "")

    if source == "tianyancha_search" and name:
        from app.services.tianyancha_client import get_company_contact

        result = _apply_contact_fields(result, get_company_contact(name), "tianyancha_baseinfo")

    website_url = str(result.get("website_url") or "")
    if not _is_direct_web_url(website_url):
        website_url = _find_company_website(name)
    if website_url:
        result = _apply_contact_fields(result, {"website_url": website_url}, "company_website_search")
        result = _apply_contact_fields(result, _fetch_website_contact_details(website_url), "company_website")

    has_contact = bool(result.get("contact_phone") or result.get("contact_email"))
    result["contact_status"] = "unverified" if has_contact else "not_found"
    result["website_status"] = "unverified" if result.get("website_url") else "not_found"
    result["contact_enrichment_status"] = "partial" if result.get("website_url") or has_contact else "not_found"
    return result


def _apply_contact_fields(candidate: dict, fields: dict[str, str], source: str) -> dict:
    """Keep the first available field and attach a traceable source label."""
    result = dict(candidate)
    for field in ("website_url", "contact_phone", "contact_email"):
        value = str(fields.get(field) or "").strip()
        if value and not result.get(field):
            result[field] = value
            result[f"{field}_source"] = source
    return result


def _find_company_website(company_name: str) -> str:
    """Find a likely public company website; it remains unverified until human review."""
    if not company_name:
        return ""
    try:
        response = httpx.get(
            "https://www.bing.com/search",
            params={"q": f"{company_name} 官网", "setlang": "zh-Hans", "count": 5},
            headers={"User-Agent": "SuppliSense supplier contact enrichment/1.0"},
            timeout=settings.SUPPLIER_DISCOVERY_CONTACT_FETCH_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
    except (httpx.HTTPError, httpx.TimeoutException):
        return ""

    soup = BeautifulSoup(response.text, "html.parser")
    for link in soup.select("li.b_algo h2 a"):
        title = link.get_text(" ", strip=True)
        url = _normalise_result_url(str(link.get("href") or ""))
        if company_name in title and _is_likely_company_website(url):
            return url
    return ""


def _fetch_website_contact_details(website_url: str) -> dict[str, str]:
    """Read homepage and at most two same-site contact pages for public contact details."""
    page = _fetch_public_html(website_url)
    if not page:
        return {}
    final_url, html = page
    soup = BeautifulSoup(html, "html.parser")
    text_parts = [soup.get_text(" ", strip=True)]
    base_host = (urlparse(final_url).hostname or "").casefold()
    contact_urls: list[str] = []
    for link in soup.select("a[href]"):
        label = f"{link.get_text(' ', strip=True)} {link.get('href', '')}".casefold()
        if not any(term in label for term in _CONTACT_PAGE_TERMS):
            continue
        target = urljoin(final_url, str(link.get("href") or ""))
        if _is_same_site_url(target, base_host) and target not in contact_urls:
            contact_urls.append(target)
        if len(contact_urls) == 2:
            break
    for contact_url in contact_urls:
        contact_page = _fetch_public_html(contact_url)
        if contact_page:
            text_parts.append(BeautifulSoup(contact_page[1], "html.parser").get_text(" ", strip=True))
    return _extract_contact_fields(final_url, " ".join(text_parts))


def _fetch_public_html(url: str) -> tuple[str, str] | None:
    if not _is_likely_company_website(url):
        return None
    try:
        response = httpx.get(
            url,
            headers={"User-Agent": "SuppliSense supplier contact enrichment/1.0"},
            timeout=settings.SUPPLIER_DISCOVERY_CONTACT_FETCH_TIMEOUT_SECONDS,
            follow_redirects=True,
        )
        response.raise_for_status()
    except (httpx.HTTPError, httpx.TimeoutException):
        return None
    final_url = str(response.url)
    content_type = response.headers.get("content-type", "").casefold()
    if not _is_likely_company_website(final_url) or "html" not in content_type:
        return None
    return final_url, response.text[:200_000]


def _is_likely_company_website(url: str) -> bool:
    if not _is_direct_web_url(url):
        return False
    host = (urlparse(url).hostname or "").casefold()
    return host not in _DIRECTORY_HOSTS and host not in _SEARCH_PROVIDER_HOSTS and host not in {"localhost", "127.0.0.1", "::1"}


def _is_same_site_url(url: str, base_host: str) -> bool:
    parsed = urlparse(url)
    return _is_likely_company_website(url) and (parsed.hostname or "").casefold() == base_host


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
    return host not in _SEARCH_PROVIDER_HOSTS


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
            "candidate_id": str(uuid.uuid5(
                uuid.NAMESPACE_URL,
                f"supplisense:external:{candidate.get('source', 'unknown')}:{candidate.get('supplier_name', '')}",
            )),
            **({"run_id": run_id} if run_id else {}),
            "status": "staged_candidate",
            "candidate_type": "external",
            "verification_status": candidate.get("verification_status", "unverified"),
            "supplier_id": None,
            "company_id": None,
        }
        for candidate in candidates
    ]


def discover_candidates(requirement: dict, policy: dict) -> dict:
    """Prefer sufficient local results and preserve them when external fallback is used."""
    local_status = "ok"
    local_failure_reason: str | None = None
    try:
        local_candidates = discover_local_candidates(requirement, policy)
        if not isinstance(local_candidates, list):
            raise TypeError("本地供应商库返回格式无效")
    except Exception as exc:
        local_candidates = []
        local_status = "failed"
        local_failure_reason = f"{type(exc).__name__}: 本地供应商库不可用"
        logger.warning("supplier_local_discovery_failed", error=type(exc).__name__)
    if is_candidate_supply_sufficient(local_candidates, requirement, policy):
        return {
            "source": "local",
            "local_candidates": local_candidates,
            "local_status": local_status,
            "local_failure_reason": local_failure_reason,
            "external_candidates": [],
            "external_status": "not_required",
            "external_stop_reason": "local_supply_sufficient",
            "external_failure_reasons": [],
            "external_loop": _external_loop_summary(0, []),
        }

    external_result = _discover_external_candidates_in_loop(requirement, policy, local_candidates)
    external_candidates = stage_external_candidates(
        str(requirement.get("run_id", "")), external_result["candidates"]
    )
    return {
        "source": "local_and_external" if external_candidates else "local",
        "local_candidates": local_candidates,
        "local_status": local_status,
        "local_failure_reason": local_failure_reason,
        "external_candidates": external_candidates,
        "external_status": external_result["status"],
        "external_stop_reason": external_result["stop_reason"],
        "external_failure_reasons": external_result["failure_reasons"],
        "external_loop": external_result["loop"],
    }


def _discover_external_candidates_in_loop(
    requirement: dict,
    policy: dict,
    local_candidates: list[dict],
) -> dict[str, Any]:
    """Run a read-only, three-stage fallback without changing supplier master data."""
    category = _first_requirement_value(requirement.get("category"))
    specification = _first_requirement_value(requirement.get("specification") or requirement.get("spec"))
    region = _first_requirement_value(requirement.get("region") or requirement.get("region_required"))
    started_at = datetime.now(timezone.utc)
    candidates: list[dict] = []
    failed_stages: list[str] = []
    failure_reasons: list[dict[str, str]] = []
    iterations = 0
    loop_stop_reason: str | None = None

    def run_stage(stage: str, action: Any, *, merge_candidates: bool = True) -> bool:
        nonlocal candidates, iterations, loop_stop_reason
        loop_state = LoopState(
            loop_type="sourcing",
            iteration=iterations,
            max_iterations=3,
            tool_call_count=iterations,
            max_tool_calls=3,
            started_at=started_at,
            timeout_seconds=60,
            evidence_count_before=len(local_candidates) + len(candidates),
        )
        preflight = evaluate_loop(
            loop_state,
            current_fingerprint=build_tool_fingerprint(stage, requirement),
            evidence_count=loop_state.evidence_count_before + 1,
        )
        if preflight.status != "continue":
            loop_stop_reason = preflight.stop_reason
            return False
        iterations += 1
        try:
            stage_candidates = action()
        except Exception as exc:
            failed_stages.append(stage)
            failure_reasons.append({"stage": stage, "reason": f"{type(exc).__name__}: 阶段调用失败"})
            logger.warning("supplier_discovery_stage_failed", stage=stage, error=type(exc).__name__)
            return True
        if not isinstance(stage_candidates, list):
            failed_stages.append(stage)
            failure_reasons.append({"stage": stage, "reason": "阶段返回格式无效，已保留此前结果"})
            return True
        if merge_candidates:
            candidates = _merge_external_candidates([*candidates, *stage_candidates])
        elif stage_candidates:
            candidates = stage_candidates
        elif candidates:
            failed_stages.append(stage)
            failure_reasons.append({"stage": stage, "reason": "阶段未返回补充数据，已保留此前候选"})
        return True

    run_stage("tianyancha", lambda: _search_tianyancha_candidates(category, specification, region))
    if not is_candidate_supply_sufficient([*local_candidates, *candidates], requirement, policy):
        run_stage("web_search", lambda: _search_web_candidates(category, specification, region))

    if candidates:
        candidates = _verify_web_candidates_with_tianyancha(candidates)
        # Contact enrichment does not change a lead's identity or verification status.
        run_stage(
            "contact_enrichment",
            lambda: _enrich_external_contacts(candidates),
            merge_candidates=False,
        )

    is_sufficient = is_candidate_supply_sufficient([*local_candidates, *candidates], requirement, policy)
    if loop_stop_reason:
        status = "partial" if candidates else "blocked"
        stop_reason = loop_stop_reason
    elif candidates and is_sufficient:
        status, stop_reason = "staged", "candidate_supply_sufficient"
    elif candidates:
        status, stop_reason = "partial", "external_sources_exhausted"
    elif failed_stages:
        status, stop_reason = "failed", "external_sources_failed"
    else:
        status, stop_reason = "not_found", "external_sources_exhausted"
    return {
        "candidates": candidates,
        "status": status,
        "stop_reason": stop_reason,
        "failure_reasons": failure_reasons,
        "loop": _external_loop_summary(iterations, failed_stages, failure_reasons),
    }


def _external_loop_summary(
    iterations: int,
    failed_stages: list[str],
    failure_reasons: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    return {
        "loop_type": "sourcing",
        "iterations": iterations,
        "max_iterations": 3,
        "max_tool_calls": 3,
        "failed_stages": failed_stages,
        "failure_reasons": failure_reasons or [],
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
