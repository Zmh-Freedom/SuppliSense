"""
Sentiment analysis background tasks (plain functions, no Celery).
"""

import logging

logger = logging.getLogger(__name__)


def analyze_sentiment_async(company_name: str, force_refresh: bool = True) -> dict:
    """Run sentiment analysis in background."""
    from app.services.sentiment import analyze_sentiment

    try:
        result = analyze_sentiment(company_name, force_refresh=force_refresh)
        return {
            "status": "success",
            "company_name": company_name,
            "result": result,
        }
    except Exception as e:
        logger.error("analyze_sentiment_async_failed company=%s error=%s", company_name, e)
        return {
            "status": "error",
            "company_name": company_name,
            "error": str(e),
        }


def analyze_all_sentiment_async() -> dict:
    """Analyze sentiment for all watched companies."""
    from app.services.sentiment import analyze_all_sentiment

    try:
        results = analyze_all_sentiment()
        return {
            "status": "success",
            "analyzed": len(results),
            "results": results,
        }
    except Exception as e:
        logger.error("analyze_all_sentiment_async_failed error=%s", e)
        return {
            "status": "error",
            "error": str(e),
        }
