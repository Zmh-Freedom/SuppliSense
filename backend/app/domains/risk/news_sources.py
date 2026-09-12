"""公开新闻来源适配器。

这些适配器只读取公开页面，不绕过登录、验证码或访问控制。每个来源都
返回相同的文章字段，供舆情服务统一去重和后续 LLM 分析使用。
"""

from __future__ import annotations

import hashlib
import logging
import re
from collections.abc import Iterable
from datetime import datetime, timezone
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

_USER_AGENT = "SuppliSenseNewsCollector/1.0 (+public-pages-only)"
_DEFAULT_TIMEOUT = 15
_DATE_PATTERN = re.compile(r"20\d{2}[-/.年]\d{1,2}[-/.月]\d{1,2}")
_NEWS_LINK_HINTS = ("新闻", "公告", "动态", "资讯", "消息", "投资者", "news", "press", "ir")
_GASGOO_LISTING_URLS = (
    "https://auto.gasgoo.com/",
    "https://auto.gasgoo.com/parts-news/C-103/1",
    "https://auto.gasgoo.com/parts-news/C-103/2",
    "https://auto.gasgoo.com/parts-news/C-103/3",
    "https://auto.gasgoo.com/parts-news/C-103/4",
    "https://auto.gasgoo.com/parts-news/C-103/5",
)
_CAAM_HOME_URL = "http://www.caam.org.cn/"


def _normalise_text(value: object) -> str:
    return " ".join(str(value or "").split())


def _short_name(company_name: str) -> str:
    for suffix in ("股份有限公司", "有限责任公司", "有限公司", "股份公司", "集团公司"):
        if company_name.endswith(suffix):
            return company_name[: -len(suffix)]
    return company_name


def _company_tokens(company_name: str) -> tuple[str, ...]:
    full = _normalise_text(company_name)
    short = _short_name(full)
    tokens = [full]
    if short and short != full and len(short) >= 3:
        tokens.append(short)
    return tuple(dict.fromkeys(tokens))


def _matches_company(company_name: str, text: str) -> bool:
    haystack = _normalise_text(text).lower()
    return any(token.lower() in haystack for token in _company_tokens(company_name))


def _parse_date(text: str) -> str | None:
    match = _DATE_PATTERN.search(text)
    if not match:
        return None
    value = match.group(0).replace("年", "-").replace("月", "-").replace("日", "")
    value = value.replace("/", "-").replace(".", "-")
    parts = value.split("-")
    if len(parts) == 3:
        return f"{int(parts[0]):04d}-{int(parts[1]):02d}-{int(parts[2]):02d}"
    return value


def _article_id(article: dict) -> str:
    raw = "|".join(
        str(article.get(key) or "")
        for key in ("source_name", "url", "title", "published_at")
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


def _article(
    *,
    title: str,
    body: str,
    url: str,
    source_name: str,
    source_type: str,
    published_at: str | None,
    publisher: str | None = None,
) -> dict:
    item = {
        "title": _normalise_text(title),
        "body": _normalise_text(body)[:1000],
        "url": url,
        "source": source_name,
        "source_name": source_name,
        "source_type": source_type,
        "publisher": publisher or source_name,
        "date": published_at or "",
        "published_at": published_at,
        "retrieved_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    item["article_id"] = _article_id(item)
    return item


def _get(url: str, *, timeout: int = _DEFAULT_TIMEOUT) -> requests.Response | None:
    try:
        response = requests.get(
            url,
            headers={"User-Agent": _USER_AGENT},
            timeout=timeout,
            allow_redirects=True,
        )
    except requests.RequestException as exc:
        logger.warning("news_source_request_failed", extra={"url": url, "error": str(exc)})
        return None
    if response.status_code != 200:
        logger.warning(
            "news_source_http_error",
            extra={"url": url, "status_code": response.status_code},
        )
        return None
    response.encoding = response.apparent_encoding or response.encoding
    return response


def _result(source_name: str, articles: list[dict], *, attempted: bool = True, error: str | None = None) -> dict:
    if articles:
        status = "ok"
    elif error:
        status = "fetch_failed"
    elif attempted:
        status = "no_results"
    else:
        status = "not_configured"
    return {
        "source_name": source_name,
        "source_type": "public_web",
        "status": status,
        "articles": articles,
        "article_count": len(articles),
        "error": error,
    }


def _deduplicate(articles: Iterable[dict], limit: int) -> list[dict]:
    seen: set[str] = set()
    unique: list[dict] = []
    for item in articles:
        key = str(item.get("article_id") or item.get("url") or item.get("title"))
        if not item.get("title") or key in seen:
            continue
        seen.add(key)
        unique.append(item)
        if len(unique) >= limit:
            break
    return unique


def fetch_gasgoo_public_news(company_name: str, max_results: int = 12) -> dict:
    """抓取盖世汽车公开资讯列表，不调用需要验证码的站内搜索入口。"""
    articles: list[dict] = []
    successful_pages = 0
    for listing_url in _GASGOO_LISTING_URLS:
        response = _get(listing_url)
        if response is None:
            continue
        successful_pages += 1
        soup = BeautifulSoup(response.text, "html.parser")
        for node in soup.select("div.listArticle dl, div.contentList dl"):
            link = node.select_one("h2.bigtitle a[href]") or next(
                (candidate for candidate in node.find_all("a", href=True) if _normalise_text(candidate.get_text(" ", strip=True))),
                None,
            )
            if not link:
                continue
            title = _normalise_text(link.get_text(" ", strip=True))
            body = _normalise_text(node.get_text(" ", strip=True))
            if not _matches_company(company_name, f"{title} {body}"):
                continue
            url = urljoin(response.url, str(link["href"]))
            articles.append(
                _article(
                    title=title,
                    body=body,
                    url=url,
                    source_name="盖世汽车公开资讯",
                    source_type="industry",
                    published_at=_parse_date(body),
                    publisher="盖世汽车",
                )
            )
    unique = _deduplicate(articles, max_results)
    if unique:
        return _result("盖世汽车公开资讯", unique)
    if successful_pages == 0:
        return _result("盖世汽车公开资讯", [], error="公开资讯列表页不可访问")
    return _result("盖世汽车公开资讯", [])


def fetch_caam_news(company_name: str, max_results: int = 12) -> dict:
    """抓取中国汽车工业协会公开行业资讯。"""
    response = _get(_CAAM_HOME_URL)
    if response is None:
        return _result("中国汽车工业协会", [], error="官网公开页面不可访问")

    soup = BeautifulSoup(response.text, "html.parser")
    articles: list[dict] = []
    for link in soup.find_all("a", href=True):
        href = urljoin(response.url, str(link["href"]))
        title = _normalise_text(link.get_text(" ", strip=True))
        if "/con_" not in href or not title or not _matches_company(company_name, title):
            continue
        parent_text = _normalise_text(link.parent.get_text(" ", strip=True))
        detail = _get(href)
        detail_title = title
        detail_body = parent_text
        if detail is not None:
            detail_soup = BeautifulSoup(detail.text, "html.parser")
            title_node = detail_soup.find("h1") or detail_soup.find("title")
            body_node = detail_soup.find("article") or detail_soup.find("main") or detail_soup.body
            detail_title = _normalise_text(title_node.get_text(" ", strip=True) if title_node else title)
            detail_body = _normalise_text(body_node.get_text(" ", strip=True) if body_node else parent_text)
        articles.append(
            _article(
                title=detail_title,
                body=detail_body,
                url=href,
                source_name="中国汽车工业协会",
                source_type="industry_official",
                published_at=_parse_date(detail_body),
                publisher="中国汽车工业协会",
            )
        )
    return _result("中国汽车工业协会", _deduplicate(articles, max_results))


def fetch_company_website_news(
    company_name: str,
    website_url: str | None,
    max_results: int = 12,
) -> dict:
    """从供应商已登记的官网抓取公开新闻/公告链接。"""
    if not website_url:
        return _result("企业官网公告", [], attempted=False)
    parsed = urlparse(website_url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return _result("企业官网公告", [], error="官网地址格式无效")

    response = _get(website_url)
    if response is None:
        return _result("企业官网公告", [], error="企业官网不可访问")

    soup = BeautifulSoup(response.text, "html.parser")
    base_host = parsed.netloc.lower()
    candidate_links: list[tuple[str, str]] = []
    for link in soup.find_all("a", href=True):
        title = _normalise_text(link.get_text(" ", strip=True))
        href = urljoin(response.url, str(link["href"]))
        if not title or not any(hint.lower() in f"{title} {href}".lower() for hint in _NEWS_LINK_HINTS):
            continue
        if urlparse(href).netloc.lower() != base_host:
            continue
        candidate_links.append((title, href))

    articles: list[dict] = []
    for link_title, href in candidate_links[: max_results * 2]:
        detail = _get(href)
        if detail is None:
            continue
        detail_soup = BeautifulSoup(detail.text, "html.parser")
        title_node = detail_soup.find("h1") or detail_soup.find("title")
        title = _normalise_text(title_node.get_text(" ", strip=True) if title_node else link_title)
        body_node = detail_soup.find("article") or detail_soup.find("main") or detail_soup.body
        body = _normalise_text(body_node.get_text(" ", strip=True) if body_node else "")
        date = _parse_date(body) or _parse_date(title)
        articles.append(
            _article(
                title=title,
                body=body,
                url=href,
                source_name="企业官网公告",
                source_type="company_official",
                published_at=date,
                publisher=base_host,
            )
        )
    return _result("企业官网公告", _deduplicate(articles, max_results))


def collect_public_news(
    company_name: str,
    *,
    website_url: str | None = None,
    max_results: int = 12,
) -> dict:
    """统一采集公开来源并返回合并后的文章和各来源状态。"""
    source_results = [
        fetch_gasgoo_public_news(company_name, max_results=max_results),
        fetch_caam_news(company_name, max_results=max_results),
        fetch_company_website_news(company_name, website_url, max_results=max_results),
    ]
    articles = _deduplicate(
        (article for result in source_results for article in result["articles"]),
        max_results,
    )
    return {
        "company_name": company_name,
        "articles": articles,
        "article_count": len(articles),
        "sources": source_results,
        "status": "ok" if articles else "no_results",
    }
