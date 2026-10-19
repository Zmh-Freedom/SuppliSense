"""V2 transport contract with real authentication dependency and no app lifespan."""

from types import SimpleNamespace
from uuid import uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from app.core.deps import get_current_user
from app.core.errors import DomainError, domain_error_handler
from app.domains.risk import assessment_api as api


@pytest.fixture
def transport(monkeypatch):
    app = FastAPI()
    app.include_router(api.router)
    app.add_exception_handler(DomainError, domain_error_handler)
    user = SimpleNamespace(id=str(uuid4()), role="purchaser")
    app.dependency_overrides[get_current_user] = lambda: user
    monkeypatch.setattr(
        api, "resolve_identity", lambda *a, **k: {"company_id": str(uuid4())}
    )
    with TestClient(app) as client:
        yield client, user


def test_detail_rechecks_scope_and_hides_internal_permits(transport, monkeypatch):
    client, user = transport
    assessment, company = str(uuid4()), str(uuid4())
    row = {
        "id": assessment,
        "actor_id": user.id,
        "company_id": company,
        "company_name": "公司",
        "identity": {"dimensions": ["financial"]},
        "policy_snapshot": {"version": "frozen"},
        "evidence_manifest": [],
        "result": {"risk_score": 37},
        "collector_token": "private",
        "input_snapshot": {"private": True},
    }
    monkeypatch.setattr(api.repo, "get_assessment", lambda *a, **k: row)
    monkeypatch.setattr(api.repo, "get_reviews", lambda *a: [])
    resp = client.get("/api/v2/assessments/" + assessment)
    assert resp.status_code == 200
    assert resp.json()["result"]["risk_score"] == 37
    assert "collector_token" not in resp.json() and "input_snapshot" not in resp.json()

    def denied(*a, **k):
        raise DomainError("ASSESSMENT_FORBIDDEN", "责任范围已改变", 403)

    monkeypatch.setattr(api, "resolve_identity", denied)
    assert client.get("/api/v2/assessments/" + assessment).status_code == 403


def test_detail_does_not_disclose_other_actor_record(transport, monkeypatch):
    client, user = transport
    monkeypatch.setattr(api.repo, "get_assessment", lambda *a, **k: None)
    assert client.get("/api/v2/assessments/" + str(uuid4())).status_code == 404


def test_v2_create_returns_stable_id_with_unavailable_result(transport, monkeypatch):
    client, user = transport
    assessment = str(uuid4())
    calls = []

    def assess(name, **kwargs):
        calls.append(kwargs)
        return {
            "id": assessment,
            "quality": "unavailable",
            "result": {"risk_score": None},
        }

    monkeypatch.setattr(api, "assess_company", assess)
    body = {"company_name": "公司", "company_id": str(uuid4()), "request_key": "stable"}
    assert client.post("/api/v2/assessments", json=body).json()["id"] == assessment
    assert calls[0]["actor_id"] == user.id and calls[0]["request_key"] == "stable"
    body["dimensions"] = ["invalid"]
    assert client.post("/api/v2/assessments", json=body).status_code == 422


def test_legacy_unavailable_is_explicit_error():
    from app.domains.risk.assessment_service import legacy_response

    with pytest.raises(DomainError) as error:
        legacy_response({"quality": "unavailable", "result": {"risk_score": None}})
    assert error.value.status_code == 503


def test_company_history_filters_revoked_monitor_scope(transport, monkeypatch):
    client, user = transport
    company = str(uuid4())
    rows = [
        {
            "id": str(uuid4()),
            "company_id": company,
            "company_name": "公司",
            "actor_id": user.id,
            "monitor_target_id": target,
            "identity": {"dimensions": ["financial"]},
            "scope_key": target,
            "policy_hash": "p",
            "policy_snapshot": {},
        }
        for target in ["visible", "revoked"]
    ]
    monkeypatch.setattr(api.repo, "list_assessments", lambda *a, **k: rows)

    def resolve(*a, **kwargs):
        if kwargs.get("monitor_target_id") == "revoked":
            raise DomainError("ASSESSMENT_FORBIDDEN", "权限已撤回", 403)
        return {"company_id": company}

    monkeypatch.setattr(api, "resolve_identity", resolve)
    result = client.get("/api/v2/assessments/company/" + company)
    assert result.status_code == 200
    assert [segment["scope_key"] for segment in result.json()["segments"]] == [
        "visible"
    ]
