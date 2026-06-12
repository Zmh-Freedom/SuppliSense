"""
Risk assessment and alert tasks.
"""

from app.core.celery_app import celery_app


@celery_app.task(bind=True, name="assess_risk_async")
def assess_risk_async(self, company_name: str) -> dict:
    """Asynchronous risk assessment."""
    from app.schemas import RiskAssessRequest
    from app.services.risk_service import assess_risk

    try:
        self.update_state(state="PROGRESS", meta={"status": "评估中", "company_name": company_name})
        result = assess_risk(RiskAssessRequest(company_name=company_name))
        return {
            "status": "success",
            "company_name": company_name,
            "result": result.model_dump(),
        }
    except Exception as e:
        return {
            "status": "error",
            "company_name": company_name,
            "error": str(e),
        }


@celery_app.task(bind=True, name="refresh_company_async")
def refresh_company_async(self, company_name: str) -> dict:
    """Asynchronous Tianyancha refresh (paid)."""
    from app.services.alert_service import detect_changes
    from app.services.tianyancha_client import fetch_company

    try:
        self.update_state(state="PROGRESS", meta={"status": "拉取天眼查数据", "company_name": company_name})
        fetch_company(company_name)

        self.update_state(state="PROGRESS", meta={"status": "检测变化", "company_name": company_name})
        changes = detect_changes(company_name)

        return {
            "status": "success",
            "company_name": company_name,
            "changes": changes,
        }
    except Exception as e:
        return {
            "status": "error",
            "company_name": company_name,
            "error": str(e),
        }


@celery_app.task(bind=True, name="batch_refresh_all")
def batch_refresh_all(self) -> dict:
    """Batch refresh all watched companies."""
    from app.services.alert_service import get_watchlist

    companies = get_watchlist()
    task_ids = []

    for company in companies:
        result = refresh_company_async.delay(company)
        task_ids.append({"company": company, "task_id": result.id})

    return {
        "status": "submitted",
        "total": len(companies),
        "tasks": task_ids,
    }


@celery_app.task(bind=True, name="check_all_async")
def check_all_async(self) -> dict:
    """Run financial check for all watched companies (free)."""
    from app.services.scheduler import run_financial_check

    try:
        self.update_state(state="PROGRESS", meta={"status": "免费巡检中"})
        result = run_financial_check()
        return {"status": "success", "result": result}
    except Exception as e:
        return {"status": "error", "error": str(e)}
