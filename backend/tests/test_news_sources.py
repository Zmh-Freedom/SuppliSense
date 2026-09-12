from __future__ import annotations

from dataclasses import dataclass

from app.domains.risk import news_sources


@dataclass
class FakeResponse:
    text: str
    url: str
    status_code: int = 200
    apparent_encoding: str = "utf-8"
    encoding: str = "utf-8"

    def json(self):
        raise ValueError("fake response has no JSON payload")


def test_fetch_gasgoo_public_news_parses_public_listing(monkeypatch) -> None:
    html = """
    <div class="listArticle"><dl>
      <dt><h2 class="bigtitle"><a href="/news/202609/11I1C103.shtml">青岛三祥科技股份有限公司扩建汽车零部件项目</a></h2></dt>
      <dd>盖世汽车获悉，供应链项目进展 2026-09-11 09:37:04</dd>
    </dl></div>
    """
    monkeypatch.setattr(
        news_sources,
        "_get",
        lambda url: FakeResponse(html, url),
    )

    result = news_sources.fetch_gasgoo_public_news("青岛三祥科技股份有限公司")

    assert result["status"] == "ok"
    assert result["article_count"] == 1
    article = result["articles"][0]
    assert article["source_name"] == "盖世汽车公开资讯"
    assert article["published_at"] == "2026-09-11"
    assert article["url"].endswith("11I1C103.shtml")


def test_fetch_caam_news_only_returns_company_related_public_articles(monkeypatch) -> None:
    html = """
    <a href="/chn/1/cate_2/con_1.html">青岛三祥科技股份有限公司参与汽车行业会议</a>
    <a href="/chn/1/cate_2/con_2.html">汽车行业产销情况简析</a>
    """
    monkeypatch.setattr(
        news_sources,
        "_get",
        lambda url: FakeResponse(html, url),
    )

    result = news_sources.fetch_caam_news("青岛三祥科技股份有限公司")

    assert result["status"] == "ok"
    assert result["article_count"] == 1
    assert "三祥" in result["articles"][0]["title"]


def test_fetch_company_website_news_reads_same_host_news_link(monkeypatch) -> None:
    homepage = """
    <a href="/news/1.html">公司新闻</a>
    <a href="https://other.example.com/news/2.html">外部链接</a>
    """
    detail = "<html><head><title>青岛三祥科技股份有限公司公告</title></head><body><main>公司公告 2026-09-10</main></body></html>"

    def fake_get(url: str, **_kwargs):
        return FakeResponse(detail if url.endswith("/news/1.html") else homepage, url)

    monkeypatch.setattr(news_sources, "_get", fake_get)

    result = news_sources.fetch_company_website_news(
        "青岛三祥科技股份有限公司",
        "https://supplier.example.com/",
    )

    assert result["status"] == "ok"
    assert result["article_count"] == 1
    assert result["articles"][0]["source_type"] == "company_official"
    assert result["articles"][0]["published_at"] == "2026-09-10"


def test_collect_public_news_merges_and_deduplicates(monkeypatch) -> None:
    article = {
        "article_id": "same",
        "title": "同一新闻",
        "source": "盖世汽车公开资讯",
    }
    monkeypatch.setattr(
        news_sources,
        "fetch_gasgoo_public_news",
        lambda *_args, **_kwargs: news_sources._result("盖世汽车公开资讯", [article]),
    )
    monkeypatch.setattr(
        news_sources,
        "fetch_caam_news",
        lambda *_args, **_kwargs: news_sources._result("中国汽车工业协会", [article]),
    )
    for name, source_name in (
        ("fetch_cninfo_news", "巨潮资讯"),
        ("fetch_sse_news", "上海证券交易所公告"),
        ("fetch_bse_news", "北京证券交易所公告"),
        ("fetch_hkex_news", "香港交易所披露易"),
    ):
        monkeypatch.setattr(
            news_sources,
            name,
            lambda *_args, _name=source_name, **_kwargs: news_sources._result(_name, []),
        )
    result = news_sources.collect_public_news("示例公司")

    assert result["article_count"] == 1
    assert len(result["sources"]) == 6
    assert {source["source_name"] for source in result["sources"]} == {
        "盖世汽车公开资讯",
        "中国汽车工业协会",
        "巨潮资讯",
        "上海证券交易所公告",
        "北京证券交易所公告",
        "香港交易所披露易",
    }


def test_fetch_cninfo_news_parses_public_announcement_api(monkeypatch) -> None:
    class JsonResponse(FakeResponse):
        def json(self):
            return {
                "announcements": [
                    {
                        "secName": "三祥科技",
                        "announcementTitle": "<em>三</em>祥科技业绩预告",
                        "announcementTime": 1783766400000,
                        "announcementId": "123",
                        "adjunctUrl": "finalpage/2026-07-11/123.PDF",
                    }
                ]
            }

    monkeypatch.setattr(news_sources.requests, "post", lambda *args, **kwargs: JsonResponse("", "https://www.cninfo.com.cn"))
    result = news_sources.fetch_cninfo_news("青岛三祥科技股份有限公司")

    assert result["status"] == "ok"
    assert result["article_count"] == 1
    assert result["articles"][0]["source_name"] == "巨潮资讯"
    assert result["articles"][0]["url"].endswith("123.PDF")


def test_fetch_sse_news_uses_code_directory_and_jsonp(monkeypatch) -> None:
    news_sources._sse_code_map.cache_clear()
    code_html = 'var x = {val:"600519",val2:"贵州茅台",val3:"gzmt"};'
    query_jsonp = 'suppliSenseSseCallback({"pageHelp":{"data":[[{"TITLE":"贵州茅台半年度报告","URL":"/disclosure/a.pdf","SSEDATE":"2026-08-15","SECURITY_NAME":"贵州茅台"}]]}})'

    def fake_get(url: str, **_kwargs):
        return FakeResponse(query_jsonp if "queryCompanyBulletinNew" in url else code_html, url)

    monkeypatch.setattr(news_sources, "_get", fake_get)
    result = news_sources.fetch_sse_news("贵州茅台股份有限公司")

    assert result["status"] == "ok"
    assert result["article_count"] == 1
    assert result["articles"][0]["source_name"] == "上海证券交易所公告"
    assert result["articles"][0]["published_at"] == "2026-08-15"


def test_fetch_bse_news_parses_jsonp_listing(monkeypatch) -> None:
    payload = 'suppliSenseBseCallback([{"listInfo":{"content":[{"companyName":"青岛三祥科技股份有限公司","disclosureTitle":"三祥科技重大事项公告","destFilePath":"/disclosure/a.pdf","publishDate":"2026-08-15"}]}}])'
    monkeypatch.setattr(news_sources, "_get", lambda url: FakeResponse(payload, url))
    result = news_sources.fetch_bse_news("青岛三祥科技股份有限公司")

    assert result["status"] == "ok"
    assert result["article_count"] == 1
    assert result["articles"][0]["source_name"] == "北京证券交易所公告"


def test_fetch_hkex_news_parses_title_search(monkeypatch) -> None:
    news_sources._hkex_stock_map.cache_clear()
    stock_json = '[{"c":"00700","n":"騰訊控股","s":42}]'
    html = """
    <table><tr><th>header</th></tr>
      <tr><td>08/09/2026 16:00</td><td>00700</td><td>騰訊控股</td>
        <td><a href="/listedco/listconews/sehk/2026/0908/2026090800001_c.pdf">中期業績公告</a></td></tr>
    </table>
    """

    class JsonResponse(FakeResponse):
        def json(self):
            return [{"i": 42, "c": "00700", "n": "騰訊控股", "s": 17472}]

    def fake_get(url: str, **_kwargs):
        return JsonResponse(stock_json, url) if url.endswith("activestock_sehk_c.json") else FakeResponse(html, url)

    monkeypatch.setattr(news_sources, "_get", fake_get)
    result = news_sources.fetch_hkex_news("腾讯控股有限公司")

    assert result["status"] == "ok"
    assert result["article_count"] == 1
    assert result["articles"][0]["source_name"] == "香港交易所披露易"
    assert result["articles"][0]["published_at"] == "2026-09-08"
