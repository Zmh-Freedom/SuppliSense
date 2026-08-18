"""Local-first, side-effect-free supplier candidate discovery."""

import re
from concurrent.futures import ThreadPoolExecutor
from typing import Any
from urllib.parse import parse_qs, unquote, urljoin, urlparse

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
            web_candidates = web_future.result()
        tyc_candidates = tyc_future.result()

    logger.info(
        "supplier_external_discovery_completed",
        category=category,
        tianyancha_count=len(tyc_candidates),
        web_count=len(web_candidates),
    )

    merged = _merge_external_candidates([*tyc_candidates, *web_candidates])
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
