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

    def fake_get(url: str):
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
    monkeypatch.setattr(
        news_sources,
        "fetch_company_website_news",
        lambda *_args, **_kwargs: news_sources._result("企业官网公告", []),
    )

    result = news_sources.collect_public_news("示例公司")

    assert result["article_count"] == 1
    assert len(result["sources"]) == 3
