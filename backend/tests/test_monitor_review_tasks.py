from types import SimpleNamespace

from app.domains.alert import review_tasks


def _task(task_type: str = "review") -> dict:
    return {
        "id": "task-1",
        "monitor_target_id": "monitor-1",
        "task_type": task_type,
        "payload": {},
    }


def test_review_task_evidence_keeps_monitor_target_and_source_contract():
    evidence = review_tasks._evidence(
        _task(),
        dimension="risk_monitoring",
        provider="monitoring_workbench",
        source_type="risk_snapshot",
        status="partial",
        data_mode="formal",
        facts={"reason": "待补充资料"},
    )

    assert evidence["monitor_target_id"] == "monitor-1"
    assert evidence["entity_id"] == "monitor-1"
    assert evidence["source_type"] == "risk_snapshot"
    assert evidence["facts"]["reason"] == "待补充资料"


def test_verify_identity_task_fails_closed_to_needs_review(monkeypatch):
    target = {
        "company_name": "外部候选有限公司",
        "identity_status": "candidate",
        "target_type": "external_candidate",
    }
    status, result, evidence = review_tasks._execute_action(_task("verify_identity"), target)

    assert status == "needs_review"
    assert result["status"] == "needs_review"
    assert evidence[0]["dimension"] == "identity"
    assert evidence[0]["facts"]["identity_status"] == "candidate"


def test_assess_task_persists_snapshot_and_returns_supported_evidence(monkeypatch):
    assessment = SimpleNamespace(
        risk_score=23,
        risk_level="低风险",
        model_dump=lambda mode=None: {"risk_score": 23, "risk_level": "低风险"},
    )
    saved = []
    monkeypatch.setattr(
        "app.domains.risk.service.calculate_company_risk_preview",
        lambda company_name: assessment,
    )
    monkeypatch.setattr(
        "app.domains.alert.service.save_snapshot",
        lambda *args, **kwargs: saved.append((args, kwargs)),
    )

    status, result, evidence = review_tasks._execute_action(
        _task("assess"),
        {
            "company_name": "正式供应商有限公司",
            "target_type": "formal_supplier",
            "supplier_id": "supplier-1",
        },
    )

    assert status == "completed"
    assert result["risk_score"] == 23
    assert saved[0][1]["monitor_target_id"] == "monitor-1"
    assert evidence[0]["status"] == "supported"
