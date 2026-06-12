"""
Sentiment analysis tasks.
"""

from app.core.celery_app import celery_app


@celery_app.task(bind=True, name="analyze_sentiment_async")
def analyze_sentiment_async(self, company_name: str, force_refresh: bool = True) -> dict:
    """Asynchronous sentiment analysis."""
    from app.services.sentiment import analyze_sentiment

    try:
        self.update_state(state="PROGRESS", meta={"status": "搜索新闻", "company_name": company_name})
        result = analyze_sentiment(company_name, force_refresh=force_refresh)

        return {
            "status": "success",
            "company_name": company_name,
            "result": result,
        }
    except Exception as e:
        return {
            "status": "error",
            "company_name": company_name,
            "error": str(e),
        }


@celery_app.task(bind=True, name="analyze_all_sentiment_async")
def analyze_all_sentiment_async(self) -> dict:
    """Analyze sentiment for all watched companies."""
    from app.services.sentiment import analyze_all_sentiment

    try:
        self.update_state(state="PROGRESS", meta={"status": "批量舆情分析中"})
        results = analyze_all_sentiment()

        return {
            "status": "success",
            "analyzed": len(results),
            "results": results,
        }
    except Exception as e:
        return {
            "status": "error",
            "error": str(e),
        }
