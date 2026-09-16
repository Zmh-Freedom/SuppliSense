from app.domains.risk import sentiment


def test_filter_search_articles_excludes_similar_and_unrelated_results():
    articles = [
        {"title": "上海汽车制动系统有限公司经营动态", "body": "公司发布公告"},
        {"title": "上海大陆汽车制动系统如何？", "body": "用户问答"},
        {"title": "汽车制动系统行业规模分析", "body": "行业研究报告"},
    ]

    result = sentiment._filter_search_articles(
        "上海汽车制动系统有限公司",
        articles,
        max_results=12,
    )

    assert [item["title"] for item in result] == ["上海汽车制动系统有限公司经营动态"]
    assert result[0]["relevance_status"] == "exact_name"


def test_filter_search_articles_excludes_results_for_unknown_company():
    articles = [
        {"title": "鏖战星际测试版", "body": "游戏测试资讯"},
        {"title": "星际争霸 II 国服测试启动", "body": "游戏新闻"},
    ]

    result = sentiment._filter_search_articles("星际测试有限公司", articles, max_results=12)

    assert result == []


def test_cached_sentiment_rejects_irrelevant_persisted_articles():
    cached = {
        "articles": [
            {"title": "上海大陆汽车制动系统如何？", "body": "用户问答"},
            {"title": "汽车制动系统行业规模分析", "body": "行业研究"},
        ]
    }

    assert sentiment._cached_sentiment_is_relevant(
        "上海汽车制动系统有限公司",
        cached,
    ) is False


def test_cached_sentiment_accepts_no_data_marker():
    assert sentiment._cached_sentiment_is_relevant(
        "星际测试有限公司",
        {"articles": [], "has_data": False},
    ) is True


def test_empty_sentiment_cache_triggers_realtime_news_search(monkeypatch):
    calls = []
    company = "星际测试有限公司"

    monkeypatch.setattr(sentiment, "get_db", lambda: object())
    monkeypatch.setattr(
        sentiment,
        "_get_cached_sentiment",
        lambda _company: {"articles": [], "has_data": False, "is_stale": False},
    )

    def search(_company, max_results=12):
        calls.append((_company, max_results))
        return [{"title": f"{company}发布经营公告", "body": "公司披露经营信息", "url": "https://example.com/news"}]

    monkeypatch.setattr(sentiment, "_search_news", search)
    monkeypatch.setattr(
        sentiment,
        "_call_llm",
        lambda _prompt: {
            "overall_sentiment": "neutral",
            "sentiment_score": 0,
            "articles": [{"index": 0, "sentiment": "neutral", "confidence": 0.8}],
        },
    )
    monkeypatch.setattr(sentiment, "_save_sentiment", lambda *_args: None)

    result = sentiment.analyze_sentiment(company, emit_alerts=False)

    assert calls == [(company, 12)]
    assert result is not None
    assert result["articles_count"] == 1
