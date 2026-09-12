"""公开新闻来源适配器。

这些适配器只读取公开页面，不绕过登录、验证码或访问控制。每个来源都
返回相同的文章字段，供舆情服务统一去重和后续 LLM 分析使用。
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from collections.abc import Iterable
from datetime import datetime, timezone
from functools import lru_cache
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

_USER_AGENT = "SuppliSenseNewsCollector/1.0 (+public-pages-only)"
_DEFAULT_TIMEOUT = 15
_DATE_PATTERN = re.compile(r"20\d{2}[-/.年]\d{1,2}[-/.月]\d{1,2}")
_DAY_FIRST_DATE_PATTERN = re.compile(r"\d{1,2}/\d{1,2}/20\d{2}")
_NEWS_LINK_HINTS = ("新闻", "公告", "动态", "资讯", "消息", "投资者", "news", "press", "ir")
_GASGOO_LISTING_URLS = (
    "https://auto.gasgoo.com/",
    "https://auto.gasgoo.com/parts-news/C-103/1",
    "https://auto.gasgoo.com/parts-news/C-103/2",
    "https://auto.gasgoo.com/parts-news/C-103/3",
    "https://auto.gasgoo.com/parts-news/C-103/4",
    "https://auto.gasgoo.com/parts-news/C-103/5",
)
_CAAM_HOME_URL = "https://www.caam.org.cn/"
_CAAM_HOME_FALLBACK_URL = "http://www.caam.org.cn/"
_CNINFO_QUERY_URL = "https://www.cninfo.com.cn/new/hisAnnouncement/query"
_CNINFO_PDF_BASE_URL = "https://static.cninfo.com.cn/"
_SSE_STATIC_CODES_URL = "https://www.sse.com.cn/js/common/ssesuggestdataAll.js"
_SSE_QUERY_URL = "https://query.sse.com.cn/security/stock/queryCompanyBulletinNew.do"
_BSE_QUERY_URL = "https://www.bse.cn/disclosureInfoController/companyAnnouncement.do"
_HKEX_STOCKS_URL = "https://www1.hkexnews.hk/ncms/script/eds/activestock_sehk_c.json"
_HKEX_TITLE_SEARCH_URL = "https://www1.hkexnews.hk/search/titlesearch.xhtml"
_CREDIT_CHINA_URL = "https://www.creditchina.gov.cn/"
_COURT_EXECUTION_SEARCH_URL = "https://zxgk.court.gov.cn/gkw/html/zhzxgk/index.html"
_SAMR_NOTICE_URL = "https://www.samr.gov.cn/jzxts/tzgg/"
_CCGP_PENALTY_URL = "https://www.ccgp.gov.cn/jdjc/jdcf/"

# 交易所名称通常使用繁体或简称。这里只保留常见公开名称的轻量转换，
# 找不到映射时适配器会安全返回 no_results，不会把未匹配误报成无风险。
_TRADITIONAL_MAP = str.maketrans(
    {
        "亚": "亞",
        "湾": "灣",
        "万": "萬",
        "东": "東",
        "业": "業",
        "丰": "豐",
        "乐": "樂",
        "云": "雲",
        "亿": "億",
        "众": "眾",
        "优": "優",
        "会": "會",
        "体": "體",
        "债": "債",
        "储": "儲",
        "儿": "兒",
        "兴": "興",
        "军": "軍",
        "农": "農",
        "凤": "鳳",
        "创": "創",
        "别": "別",
        "华": "華",
        "协": "協",
        "单": "單",
        "卖": "賣",
        "卫": "衛",
        "历": "歷",
        "厂": "廠",
        "厅": "廳",
        "县": "縣",
        "参": "參",
        "发": "發",
        "变": "變",
        "叶": "葉",
        "后": "後",
        "启": "啟",
        "员": "員",
        "园": "園",
        "国": "國",
        "圣": "聖",
        "场": "場",
        "块": "塊",
        "坚": "堅",
        "壮": "壯",
        "备": "備",
        "复": "復",
        "够": "夠",
        "头": "頭",
        "奖": "獎",
        "妆": "妝",
        "娱": "娛",
        "宁": "寧",
        "宝": "寶",
        "实": "實",
        "审": "審",
        "宽": "寬",
        "对": "對",
        "导": "導",
        "尘": "塵",
        "属": "屬",
        "岁": "歲",
        "岛": "島",
        "币": "幣",
        "帮": "幫",
        "广": "廣",
        "庆": "慶",
        "库": "庫",
        "应": "應",
        "张": "張",
        "归": "歸",
        "录": "錄",
        "总": "總",
        "恆": "恆",
        "惯": "慣",
        "战": "戰",
        "户": "戶",
        "报": "報",
        "择": "擇",
        "拥": "擁",
        "换": "換",
        "据": "據",
        "损": "損",
        "携": "攜",
        "摄": "攝",
        "收": "收",
        "敌": "敵",
        "数": "數",
        "断": "斷",
        "无": "無",
        "旧": "舊",
        "时": "時",
        "显": "顯",
        "暂": "暫",
        "术": "術",
        "杂": "雜",
        "权": "權",
        "条": "條",
        "来": "來",
        "标": "標",
        "样": "樣",
        "检": "檢",
        "楼": "樓",
        "欢": "歡",
        "欧": "歐",
        "汉": "漢",
        "汇": "匯",
        "沟": "溝",
        "没": "沒",
        "洁": "潔",
        "济": "濟",
        "浓": "濃",
        "湾": "灣",
        "点": "點",
        "炼": "煉",
        "热": "熱",
        "环": "環",
        "现": "現",
        "产": "產",
        "电": "電",
        "画": "畫",
        "畅": "暢",
        "疗": "療",
        "矿": "礦",
        "码": "碼",
        "确": "確",
        "礼": "禮",
        "祉": "祉",
        "离": "離",
        "种": "種",
        "积": "積",
        "税": "稅",
        "稳": "穩",
        "窝": "窩",
        "竞": "競",
        "笔": "筆",
        "签": "簽",
        "简": "簡",
        "类": "類",
        "练": "練",
        "组": "組",
        "经": "經",
        "结": "結",
        "统": "統",
        "继": "繼",
        "续": "續",
        "绩": "績",
        "维": "維",
        "绿": "綠",
        "网": "網",
        "罗": "羅",
        "联": "聯",
        "职": "職",
        "胜": "勝",
        "能": "能",
        "腾": "騰",
        "讯": "訊",
        "舆": "輿",
        "车": "車",
        "转": "轉",
        "软": "軟",
        "较": "較",
        "边": "邊",
        "达": "達",
        "过": "過",
        "还": "還",
        "进": "進",
        "运": "運",
        "这": "這",
        "连": "連",
        "选": "選",
        "逻": "邏",
        "遗": "遺",
        "邮": "郵",
        "邻": "鄰",
        "钱": "錢",
        "长": "長",
        "门": "門",
        "闻": "聞",
        "际": "際",
        "陆": "陸",
        "陈": "陳",
        "随": "隨",
        "难": "難",
        "顶": "頂",
        "项": "項",
        "预": "預",
        "领": "領",
        "频": "頻",
        "验": "驗",
        "髮": "髮",
        "鱼": "魚",
        "鸟": "鳥",
        "鹏": "鵬",
        "黄": "黃",
        "齐": "齊",
        "龙": "龍",
        "龟": "龜",
    }
)


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
    tokens = [full, full.translate(_TRADITIONAL_MAP)]
    if short and short != full and len(short) >= 3:
        tokens.extend((short, short.translate(_TRADITIONAL_MAP)))
    return tuple(dict.fromkeys(tokens))


def _matches_company(company_name: str, text: str) -> bool:
    haystack = _normalise_text(text).lower()
    return any(token.lower() in haystack for token in _company_tokens(company_name))


def _parse_date(text: str) -> str | None:
    match = _DATE_PATTERN.search(text)
    if not match:
        day_first = _DAY_FIRST_DATE_PATTERN.search(text)
        if not day_first:
            return None
        day, month, year = day_first.group(0).split("/")
        return f"{int(year):04d}-{int(month):02d}-{int(day):02d}"
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


def _get(
    url: str,
    *,
    timeout: int = _DEFAULT_TIMEOUT,
    headers: dict[str, str] | None = None,
) -> requests.Response | None:
    try:
        request_headers = {"User-Agent": _USER_AGENT}
        if headers:
            request_headers.update(headers)
        response = requests.get(
            url,
            headers=request_headers,
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


def _post(
    url: str,
    *,
    data: dict[str, str],
    timeout: int = _DEFAULT_TIMEOUT,
    headers: dict[str, str] | None = None,
) -> requests.Response | None:
    try:
        request_headers = {"User-Agent": _USER_AGENT}
        if headers:
            request_headers.update(headers)
        response = requests.post(
            url,
            data=data,
            headers=request_headers,
            timeout=timeout,
            allow_redirects=True,
        )
    except requests.RequestException as exc:
        logger.warning("news_source_post_failed", extra={"url": url, "error": str(exc)})
        return None
    if response.status_code != 200:
        logger.warning(
            "news_source_post_http_error",
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
    response = _get(_CAAM_HOME_URL) or _get(_CAAM_HOME_FALLBACK_URL)
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


def _parse_jsonp(text: str) -> dict | list | None:
    """解析交易所返回的 JSON 或 JSONP。"""
    payload = text.strip().rstrip(";")
    if payload.startswith("null(") and payload.endswith(")"):
        payload = payload[5:-1]
    elif "(" in payload and payload.endswith(")"):
        payload = payload[payload.find("(") + 1 : -1]
    try:
        value = json.loads(payload)
    except (TypeError, ValueError):
        return None
    return value if isinstance(value, (dict, list)) else None


def _date_window() -> tuple[str, str]:
    now = datetime.now(timezone.utc)
    start = now.replace(year=now.year - 1)
    return start.strftime("%Y-%m-%d"), now.strftime("%Y-%m-%d")


def fetch_cninfo_news(company_name: str, max_results: int = 12) -> dict:
    """查询巨潮资讯公开历史公告，支持按公司名称检索。"""
    start_date, end_date = _date_window()
    payload = {
        "pageNum": "1",
        "pageSize": str(min(max_results, 30)),
        "tabName": "fulltext",
        "column": "",
        "category": "",
        "plate": "",
        "seDate": f"{start_date}~{end_date}",
        "searchkey": company_name,
        "secid": "",
        "sortName": "",
        "sortType": "",
        "isHLtitle": "true",
        "stock": "",
        "trade": "",
    }
    try:
        response = requests.post(
            _CNINFO_QUERY_URL,
            data=payload,
            headers={
                "User-Agent": _USER_AGENT,
                "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
                "X-Requested-With": "XMLHttpRequest",
                "Referer": "https://www.cninfo.com.cn/new/disclosure",
            },
            timeout=_DEFAULT_TIMEOUT,
        )
    except requests.RequestException as exc:
        return _result("巨潮资讯", [], error=f"公开公告接口不可访问: {exc}")
    if response.status_code != 200:
        return _result("巨潮资讯", [], error=f"公开公告接口返回 HTTP {response.status_code}")
    try:
        data = response.json()
    except ValueError:
        return _result("巨潮资讯", [], error="公开公告接口返回格式无法解析")

    articles: list[dict] = []
    for item in data.get("announcements", []) or []:
        title = BeautifulSoup(str(item.get("announcementTitle") or ""), "html.parser").get_text(" ", strip=True)
        if not title:
            continue
        timestamp = item.get("announcementTime")
        published_at = None
        if timestamp:
            try:
                published_at = datetime.fromtimestamp(float(timestamp) / 1000, tz=timezone.utc).strftime("%Y-%m-%d")
            except (TypeError, ValueError, OSError):
                published_at = _parse_date(str(timestamp))
        adjunct_url = str(item.get("adjunctUrl") or "")
        url = urljoin(_CNINFO_PDF_BASE_URL, adjunct_url) if adjunct_url else (
            "https://www.cninfo.com.cn/new/disclosure/detail?announcementId="
            + str(item.get("announcementId") or "")
        )
        articles.append(
            _article(
                title=title,
                body=f"{item.get('secName') or ''} {title}".strip(),
                url=url,
                source_name="巨潮资讯",
                source_type="exchange_official",
                published_at=published_at,
                publisher="深圳证券信息有限公司",
            )
        )
    return _result("巨潮资讯", _deduplicate(articles, max_results))


@lru_cache(maxsize=1)
def _sse_code_map() -> tuple[tuple[str, str], ...]:
    response = _get(_SSE_STATIC_CODES_URL)
    if response is None:
        return ()
    pairs = re.findall(r'val:"(\d{6})",val2:"([^"]+)"', response.text)
    return tuple(pairs)


def _lookup_sse_code(company_name: str) -> str | None:
    tokens = _company_tokens(company_name)
    for code, name in _sse_code_map():
        if any(token.lower() in name.lower() or name.lower() in token.lower() for token in tokens):
            return code
    return None


def fetch_sse_news(company_name: str, max_results: int = 12) -> dict:
    """查询上海证券交易所公开公告。"""
    code = _lookup_sse_code(company_name)
    if not code:
        return _result("上海证券交易所公告", [], attempted=False)
    start_date, end_date = _date_window()
    params = {
        "isPagination": "true",
        "pageHelp.pageSize": str(min(max_results, 25)),
        "pageHelp.cacheSize": "1",
        "START_DATE": start_date,
        "END_DATE": end_date,
        "SECURITY_CODE": code,
        "TITLE": "",
        "BULLETIN_TYPE": "",
        "stockType": "",
        "jsonCallBack": "suppliSenseSseCallback",
    }
    response = _get(
        _SSE_QUERY_URL + "?" + requests.compat.urlencode(params),
        headers={
            "Referer": "https://star.sse.com.cn/disclosure/listedinfo/announcement/",
            "Accept": "application/javascript, */*;q=0.8",
        },
    )
    if response is None:
        return _result("上海证券交易所公告", [], error="上交所公开公告接口不可访问")
    data = _parse_jsonp(response.text)
    if not isinstance(data, dict):
        return _result("上海证券交易所公告", [], error="上交所公开公告接口返回格式无法解析")
    rows = data.get("pageHelp", {}).get("data", []) or []
    articles: list[dict] = []
    for group in rows:
        for item in group if isinstance(group, list) else []:
            title = _normalise_text(item.get("TITLE"))
            href = urljoin("https://www.sse.com.cn", str(item.get("URL") or ""))
            if not title or not href:
                continue
            articles.append(
                _article(
                    title=title,
                    body=f"{item.get('SECURITY_NAME') or company_name} {title}".strip(),
                    url=href,
                    source_name="上海证券交易所公告",
                    source_type="exchange_official",
                    published_at=_parse_date(str(item.get("SSEDATE") or "")),
                    publisher="上海证券交易所",
                )
            )
    return _result("上海证券交易所公告", _deduplicate(articles, max_results))


def fetch_bse_news(company_name: str, max_results: int = 12) -> dict:
    """查询北京证券交易所公开上市公司公告。"""
    start_date, end_date = _date_window()
    params = {
        "disclosureType": "",
        "disclosureSubtype": "",
        "page": "",
        "companyCd": "",
        "isNewThree": "1",
        "startTime": start_date,
        "endTime": end_date,
        "keyword": _short_name(company_name),
        "xxfcbj": "2",
        "hyType": "",
        "needFields": "companyCd,companyName,disclosureTitle,disclosurePostTitle,destFilePath,publishDate,xxfcbj,fileExt,xxzrlx",
        "callback": "suppliSenseBseCallback",
    }
    response = _get(_BSE_QUERY_URL + "?" + requests.compat.urlencode(params))
    if response is None:
        return _result("北京证券交易所公告", [], error="北交所公开公告接口不可访问")
    data = _parse_jsonp(response.text)
    if not isinstance(data, list) or not data or not isinstance(data[0], dict):
        return _result("北京证券交易所公告", [], error="北交所公开公告接口返回格式无法解析")
    rows = data[0].get("listInfo", {}).get("content", []) or []
    articles: list[dict] = []
    for item in rows:
        title = _normalise_text(f"{item.get('disclosureTitle') or ''}{item.get('disclosurePostTitle') or ''}")
        if not title or not _matches_company(company_name, f"{title} {item.get('companyName') or ''}"):
            continue
        href = urljoin("https://www.bse.cn", str(item.get("destFilePath") or ""))
        articles.append(
            _article(
                title=title,
                body=f"{item.get('companyName') or company_name} {title}".strip(),
                url=href,
                source_name="北京证券交易所公告",
                source_type="exchange_official",
                published_at=_parse_date(str(item.get("publishDate") or "")),
                publisher="北京证券交易所",
            )
        )
    return _result("北京证券交易所公告", _deduplicate(articles, max_results))


@lru_cache(maxsize=1)
def _hkex_stock_map() -> tuple[tuple[str, str, str], ...]:
    response = _get(_HKEX_STOCKS_URL)
    if response is None:
        return ()
    try:
        values = response.json()
    except ValueError:
        return ()
    if not isinstance(values, list):
        return ()
    return tuple(
        (str(item.get("c") or ""), str(item.get("n") or ""), str(item.get("i") or ""))
        for item in values
        if isinstance(item, dict) and item.get("c") and item.get("i")
    )


def _lookup_hkex_stock(company_name: str) -> tuple[str, str] | None:
    tokens = _company_tokens(company_name)
    for code, name, stock_id in _hkex_stock_map():
        if any(token.lower() in name.lower() or name.lower() in token.lower() for token in tokens):
            return code, stock_id
    return None


def fetch_hkex_news(company_name: str, max_results: int = 12) -> dict:
    """查询香港交易所披露易公开上市公司公告。"""
    stock = _lookup_hkex_stock(company_name)
    if not stock:
        return _result("香港交易所披露易", [], attempted=False)
    code, stock_id = stock
    url = f"{_HKEX_TITLE_SEARCH_URL}?category=0&lang=ZH&market=SEHK&stockId={stock_id}"
    response = _get(url)
    if response is None:
        return _result("香港交易所披露易", [], error="披露易公开公告页面不可访问")
    soup = BeautifulSoup(response.text, "html.parser")
    articles: list[dict] = []
    for row in soup.select("table tr"):
        cells = row.find_all("td")
        link = row.find("a", href=True)
        if len(cells) < 4 or not link:
            continue
        title = _normalise_text(link.get_text(" ", strip=True))
        row_text = _normalise_text(row.get_text(" ", strip=True))
        if not title:
            continue
        articles.append(
            _article(
                title=title,
                body=f"{code} {row_text}".strip(),
                url=urljoin("https://www1.hkexnews.hk", str(link["href"])),
                source_name="香港交易所披露易",
                source_type="exchange_official",
                published_at=_parse_date(row_text),
                publisher="香港交易所",
            )
        )
    return _result("香港交易所披露易", _deduplicate(articles, max_results))


def _listing_articles(
    company_name: str,
    response: requests.Response,
    *,
    source_name: str,
    source_type: str,
    publisher: str,
    max_results: int,
    href_prefix: str | None = None,
) -> list[dict]:
    """从公开列表页提取与公司名称相关的公告链接。"""
    soup = BeautifulSoup(response.text, "html.parser")
    articles: list[dict] = []
    for link in soup.find_all("a", href=True):
        title = _normalise_text(link.get_text(" ", strip=True))
        if not title:
            continue
        href = urljoin(response.url, str(link["href"]))
        if href_prefix and href_prefix not in href:
            continue
        context_node = link.find_parent(["li", "tr", "dd"]) or link.parent
        context = _normalise_text(context_node.get_text(" ", strip=True))
        if not _matches_company(company_name, f"{title} {context}"):
            continue
        articles.append(
            _article(
                title=title,
                body=context,
                url=href,
                source_name=source_name,
                source_type=source_type,
                published_at=_parse_date(context) or _parse_date(title),
                publisher=publisher,
            )
        )
    return articles


def fetch_credit_china_news(company_name: str, max_results: int = 12) -> dict:
    """读取信用中国公开页面中的主体信用公告和失信信息。"""
    response = _get(_CREDIT_CHINA_URL)
    if response is None:
        return _result("信用中国", [], error="信用中国公开页面不可访问或需要实名验证")
    articles = _listing_articles(
        company_name,
        response,
        source_name="信用中国",
        source_type="credit_official",
        publisher="国家公共信用信息中心",
        max_results=max_results,
    )
    return _result("信用中国", _deduplicate(articles, max_results))


def fetch_court_execution_news(company_name: str, max_results: int = 12) -> dict:
    """查询中国执行信息公开网的公开执行信息入口。

    该站点可能要求验证码或实名校验；遇到校验页时返回 ``fetch_failed``，
    不将无法检索解释为明确无执行记录。
    """
    landing = _get(_COURT_EXECUTION_SEARCH_URL)
    if landing is None:
        return _result("中国执行信息公开网", [], error="执行信息公开网入口不可访问")
    soup = BeautifulSoup(landing.text, "html.parser")
    form = soup.select_one("form#zhcx-search-form") or soup.find("form", action=True)
    action = urljoin(landing.url, str(form.get("action") if form else ""))
    if not action or action == landing.url:
        return _result("中国执行信息公开网", [], error="执行信息公开查询入口未找到")
    result = _post(
        action,
        data={
            "pName": company_name,
            "pCardNum": "",
            "selectCourtId": "0",
            "currentPage": "1",
        },
        headers={"Referer": landing.url, "X-Requested-With": "XMLHttpRequest"},
    )
    if result is None:
        return _result("中国执行信息公开网", [], error="执行信息公开查询需要验证码或暂不可用")
    articles = _listing_articles(
        company_name,
        result,
        source_name="中国执行信息公开网",
        source_type="judicial_official",
        publisher="最高人民法院",
        max_results=max_results,
    )
    return _result("中国执行信息公开网", _deduplicate(articles, max_results))


def fetch_samr_news(company_name: str, max_results: int = 12) -> dict:
    """抓取国家市场监督管理总局公开行政处罚公告。"""
    response = _get(_SAMR_NOTICE_URL)
    if response is None:
        return _result("国家市场监督管理总局", [], error="市场监管总局公告页面不可访问")
    articles = _listing_articles(
        company_name,
        response,
        source_name="国家市场监督管理总局",
        source_type="regulatory_official",
        publisher="国家市场监督管理总局",
        max_results=max_results,
        href_prefix="samr.gov.cn",
    )
    return _result("国家市场监督管理总局", _deduplicate(articles, max_results))


def fetch_ccgp_news(company_name: str, max_results: int = 12) -> dict:
    """抓取中国政府采购网公开监督处罚和投诉处理公告。"""
    response = _get(_CCGP_PENALTY_URL)
    if response is None:
        return _result("中国政府采购网", [], error="政府采购监督处罚页面不可访问")
    articles = _listing_articles(
        company_name,
        response,
        source_name="中国政府采购网",
        source_type="procurement_official",
        publisher="财政部",
        max_results=max_results,
        href_prefix="ccgp.gov.cn",
    )
    return _result("中国政府采购网", _deduplicate(articles, max_results))


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
    """统一采集活动公开来源并返回合并后的文章和各来源状态。

    ``website_url`` 暂时保留为兼容参数，但当前版本不会主动访问企业官网。
    """
    source_results = [
        fetch_gasgoo_public_news(company_name, max_results=max_results),
        fetch_caam_news(company_name, max_results=max_results),
        fetch_cninfo_news(company_name, max_results=max_results),
        fetch_sse_news(company_name, max_results=max_results),
        fetch_bse_news(company_name, max_results=max_results),
        fetch_hkex_news(company_name, max_results=max_results),
        fetch_credit_china_news(company_name, max_results=max_results),
        fetch_court_execution_news(company_name, max_results=max_results),
        fetch_samr_news(company_name, max_results=max_results),
        fetch_ccgp_news(company_name, max_results=max_results),
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
