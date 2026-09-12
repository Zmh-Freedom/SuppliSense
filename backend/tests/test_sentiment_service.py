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
