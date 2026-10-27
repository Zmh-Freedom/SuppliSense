"""Real server PG/Mongo checks, confined to assessment_stage2_test."""

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import uuid4

import pytest

from app.core.config import settings
from app.core.errors import DomainError
from app.db.init_pg import ensure_pg_schema
from app.db.postgres import get_cursor
from app.domains.agent_run.coordinator import (
    RunCoordinator,
    execution_scope,
    ExecutionLost,
)
from app.domains.agent_run.state_store import SessionStateStore
from app.domains.risk import assessment_repo as repo
from app.domains.risk.assessment_policy import current_policy

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def ledger_schema():
    if settings.PG_DB != "assessment_stage2_test":
        pytest.skip("requires isolated remote assessment_stage2_test database")
    ensure_pg_schema()


@pytest.fixture
def assessment_factory(ledger_schema):
    actor, session = str(uuid4()), str(uuid4())
    companies = [str(uuid4()), str(uuid4())]
    with get_cursor() as (_, cur):
        cur.execute(
            "INSERT INTO users(id,username,email,password_hash,role) VALUES(%s,%s,%s,'test','purchaser')",
            (actor, actor, actor + "@test.invalid"),
        )
        for company in companies:
            cur.execute(
                "INSERT INTO companies(id,legal_name,normalized_name,verification_status,identity_source,source_reference) VALUES(%s,'同名公司','同名公司','verified','import',%s)",
                (company, "fixture:" + company),
            )
    run = str(SessionStateStore().start_harness_turn(session, actor, "评估")["run"].id)
    permit = RunCoordinator().claim(run, "assessment-test")
    yield {"actor": actor, "companies": companies, "run": run, "permit": permit}
    with get_cursor() as (_, cur):
        cur.execute(
            "DELETE FROM outbox_events WHERE aggregate_id IN (SELECT id FROM assessment_history WHERE actor_id=%s)",
            (actor,),
        )
        cur.execute("DELETE FROM assessment_history WHERE actor_id=%s", (actor,))
        cur.execute("DELETE FROM agent_sessions WHERE id=%s", (session,))
        for company in companies:
            cur.execute("DELETE FROM companies WHERE id=%s", (company,))
        cur.execute("DELETE FROM users WHERE id=%s", (actor,))


def identity(fixture, index=0, **extra):
    return {
        "company_id": fixture["companies"][index],
        "company_name": "同名公司",
        "dimensions": ["financial"],
        "purchase_scope": "category:a",
        **extra,
    }


def begin(fixture, index=0, **extra):
    return repo.begin_assessment(
        identity(fixture, index),
        actor_id=fixture["actor"],
        request_key="request:" + str(index),
        policy=current_policy(["financial"]),
        **extra,
    )


def test_retry_preserves_id_and_original_policy(assessment_factory):
    f = assessment_factory
    first = begin(f)
    changed = {
        **current_policy(["financial"]),
        "version": "future",
        "weights": {"financial": 99},
    }
    second = repo.begin_assessment(
        identity(f), actor_id=f["actor"], request_key="request:0", policy=changed
    )
    assert first["id"] == second["id"]
    assert first["policy_snapshot"] == second["policy_snapshot"]
    assert first["assessment_version"] == second["assessment_version"]


def test_same_idempotency_key_rejects_changed_company_or_scope(assessment_factory):
    f = assessment_factory
    begin(f)
    with pytest.raises(DomainError):
        repo.begin_assessment(
            identity(f, 1),
            actor_id=f["actor"],
            request_key="request:0",
            policy=current_policy(["financial"]),
        )
    with pytest.raises(DomainError):
        repo.begin_assessment(
            identity(f, purchase_scope="category:b"),
            actor_id=f["actor"],
            request_key="request:0",
            policy=current_policy(["financial"]),
        )


def test_one_run_supports_two_companies_and_keeps_scope_isolation(assessment_factory):
    f = assessment_factory
    with execution_scope(f["permit"]):
        a = repo.begin_assessment(
            identity(f),
            actor_id=f["actor"],
            run_id=f["run"],
            logical_key="task:a",
            policy=current_policy(["financial"]),
        )
        b = repo.begin_assessment(
            identity(f, 1),
            actor_id=f["actor"],
            run_id=f["run"],
            logical_key="task:b",
            policy=current_policy(["financial"]),
        )
        again = repo.begin_assessment(
            identity(f),
            actor_id=f["actor"],
            run_id=f["run"],
            logical_key="task:a",
            policy=current_policy(["financial"]),
        )
    assert a["id"] != b["id"]
    assert again["id"] == a["id"]
    assert repo.get_assessment(a["id"], str(uuid4())) is None


def test_concurrent_begin_allocates_distinct_versions_and_one_predecessor_chain(
    assessment_factory,
):
    f = assessment_factory
    barrier = Barrier(2)

    def create(index):
        barrier.wait()
        return repo.begin_assessment(
            identity(f),
            actor_id=f["actor"],
            request_key="parallel:" + str(index),
            policy=current_policy(["financial"]),
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        rows = list(pool.map(create, [0, 1]))
    rows.sort(key=lambda row: row["assessment_version"])
    assert rows[1]["assessment_version"] == rows[0]["assessment_version"] + 1
    assert rows[1]["previous_assessment_id"] == rows[0]["id"]


def test_freeze_cannot_replace_committed_evidence(assessment_factory):
    row = begin(assessment_factory)
    first = repo.freeze_assessment(
        row["id"], row["collector_token"], inputs={"request": {"value": 1}}, manifest=[]
    )
    again = repo.freeze_assessment(
        row["id"], row["collector_token"], inputs={"request": {"value": 1}}, manifest=[]
    )
    assert first["input_digest"] == again["input_digest"]
    with pytest.raises(DomainError):
        repo.freeze_assessment(
            row["id"],
            row["collector_token"],
            inputs={"request": {"value": 2}},
            manifest=[],
        )


def test_cancelled_attempt_cannot_commit_assessment(assessment_factory):
    f = assessment_factory
    with execution_scope(f["permit"]):
        row = repo.begin_assessment(
            identity(f),
            actor_id=f["actor"],
            run_id=f["run"],
            logical_key="task:risk",
            policy=current_policy(["financial"]),
        )
        repo.freeze_assessment(
            row["id"], row["collector_token"], inputs={}, manifest=[]
        )
        RunCoordinator().cancel(f["run"], f["actor"])
        with pytest.raises(ExecutionLost):
            repo.commit_assessment(
                row["id"],
                row["collector_token"],
                result={"risk_score": 70, "risk_level": "低风险"},
                quality="partial",
            )


def test_legacy_uuid_and_unknown_evidence_are_preserved(assessment_factory):
    legacy_id = str(uuid4())
    with get_cursor() as (_, cur):
        cur.execute(
            "INSERT INTO assessment_history(id,actor_id,company_name,risk_score,risk_level) VALUES(%s,%s,'旧记录',88,'低风险')",
            (legacy_id, assessment_factory["actor"]),
        )
    row = repo.get_assessment(legacy_id, assessment_factory["actor"])
    assert row["id"] == legacy_id and row["risk_score"] == 88
    assert row["assessment_status"] == "legacy_unverified"
    assert row["policy_hash"] is None and row["input_digest"] is None
    assert row["evidence_manifest"] == []


def test_same_policy_version_cannot_be_redefined_for_new_requests(assessment_factory):
    f = assessment_factory
    begin(f)
    policy = {**current_policy(["financial"]), "missing_policy": "zero_risk"}
    with pytest.raises(DomainError, match="政策版本"):
        repo.begin_assessment(
            identity(f),
            actor_id=f["actor"],
            request_key="new-policy-conflict",
            policy=policy,
        )


def test_concurrent_same_request_has_one_identity_and_collector(assessment_factory):
    f = assessment_factory
    barrier = Barrier(2)

    def create(_):
        barrier.wait()
        return begin(f)

    with ThreadPoolExecutor(max_workers=2) as pool:
        rows = list(pool.map(create, [0, 1]))
    assert rows[0]["id"] == rows[1]["id"]
    assert sum(row["acquired"] for row in rows) == 1


def test_result_and_outbox_commit_once_without_overwriting_original(assessment_factory):
    row = begin(assessment_factory)
    repo.freeze_assessment(
        row["id"], row["collector_token"], inputs={"data": "frozen"}, manifest=[]
    )
    result = {"risk_score": 72, "risk_level": "低风险", "score_breakdown": {}}
    a = repo.commit_assessment(
        row["id"], row["collector_token"], result=result, quality="partial"
    )
    b = repo.commit_assessment(
        row["id"], row["collector_token"], result=result, quality="partial"
    )
    assert a["assessed_at"] == b["assessed_at"]
    with pytest.raises(DomainError):
        repo.commit_assessment(
            row["id"],
            row["collector_token"],
            result={**result, "risk_score": 99},
            quality="partial",
        )
    with get_cursor() as (_, cur):
        cur.execute(
            "SELECT COUNT(*) FROM outbox_events WHERE aggregate_id=%s AND event_type='risk.assessment.committed'",
            (row["id"],),
        )
        assert cur.fetchone()[0] == 1


def test_outbox_insert_failure_rolls_back_assessment_result(
    assessment_factory, monkeypatch
):
    row = begin(assessment_factory)
    repo.freeze_assessment(row["id"], row["collector_token"], inputs={}, manifest=[])
    monkeypatch.setattr(
        repo,
        "enqueue_event",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("outbox failed")),
    )
    with pytest.raises(RuntimeError, match="outbox failed"):
        repo.commit_assessment(
            row["id"],
            row["collector_token"],
            result={"risk_score": 80},
            quality="partial",
        )
    current = repo.get_assessment(row["id"], assessment_factory["actor"])
    assert current["assessment_status"] == "frozen" and current["result"] is None


@pytest.mark.parametrize("failure_point", ["projection_down", "response_lost"])
def test_application_retry_reuses_business_result_after_pg_commit(
    assessment_factory, monkeypatch, failure_point
):
    from app.domains.risk import assessment_service as app
    from app.domains.risk.assessment_evidence import make_evidence
    from app.domains.risk.assessment_projection import project_assessment
    from app.schemas import RiskCalculateRequest
    from app.schemas.company import CompanyProfile
    from app.schemas.financial import FinancialMetrics
    from app.schemas.risk import RiskInfo
    from app.db.mongo import get_db

    f = assessment_factory
    calls = []
    profile = CompanyProfile(
        company_name="同名公司",
        legal_person="",
        registered_capital="",
        establish_time="",
        is_listed=True,
        industry="制造业",
    )
    request = RiskCalculateRequest(
        company=profile,
        risk=RiskInfo(
            lawsuit_count=0,
            executed_count=0,
            abnormal_operation_count=0,
            administrative_penalty_count=0,
        ),
        financial=FinancialMetrics(
            debt_ratio=0.8, revenue_growth=0, net_profit_growth=0, cash_flow=0
        ),
    )

    def collect(row, **kwargs):
        calls.append(row["id"])
        entry, raw = make_evidence(
            row,
            "financial",
            {"financial": request.financial.model_dump(mode="json")},
            status="available",
            source="fixture",
            policy=row["policy_snapshot"],
            observed_at="2026-10-19T00:00:00+00:00",
            query_receipt={"status": "success", "record_count": 1},
        )
        return (
            {
                "request": request.model_dump(mode="json"),
                "recent_lawsuits": 0,
                "soft": {},
            },
            [entry],
            [raw],
        )

    monkeypatch.setattr(app, "_collect_new_inputs", collect)
    try:
        row = app.assess_company(
            "同名公司",
            company_id=f["companies"][0],
            actor_id=f["actor"],
            actor_role="purchaser",
            request_key="business-retry",
            dimensions=["financial"],
        )
        assert row["assessment_status"] == "committed"
        if failure_point == "projection_down":
            with monkeypatch.context() as m:
                m.setattr(
                    "app.domains.risk.assessment_projection.get_db",
                    lambda: (_ for _ in ()).throw(
                        RuntimeError("Mongo projection unavailable")
                    ),
                )
                with pytest.raises(RuntimeError):
                    project_assessment({"payload": {"assessment_id": row["id"]}})
        again = app.assess_company(
            "同名公司",
            company_id=f["companies"][0],
            actor_id=f["actor"],
            actor_role="purchaser",
            request_key="business-retry",
            dimensions=["financial"],
        )
        assert again["id"] == row["id"] and again["assessed_at"] == row["assessed_at"]
        assert calls == [row["id"]]
        project_assessment({"payload": {"assessment_id": row["id"]}})
        project_assessment({"payload": {"assessment_id": row["id"]}})
        assert (
            get_db()["assessment_snapshots"].count_documents(
                {"assessment_id": row["id"]}
            )
            == 1
        )
    finally:
        for name in (
            "agent_evidence_payloads",
            "assessment_snapshots",
            "alert_snapshots",
        ):
            get_db()[name].delete_many({"company_id": f["companies"][0]})


def test_reviews_are_append_only_and_idempotent(assessment_factory):
    f = assessment_factory
    row = begin(f)
    token = str(row["collector_token"])
    repo.freeze_assessment(row["id"], token, inputs={}, manifest=[])
    repo.commit_assessment(
        row["id"],
        token,
        result={"risk_score": 37, "risk_level": "高风险"},
        quality="partial",
    )
    review = repo.append_review(
        row["id"],
        f["actor"],
        reason="核对原始材料",
        decision="needs_review",
        request_key="review:1",
    )
    assert (
        repo.append_review(
            row["id"],
            f["actor"],
            reason="核对原始材料",
            decision="needs_review",
            request_key="review:1",
        )["id"]
        == review["id"]
    )
    with pytest.raises(DomainError):
        repo.append_review(
            row["id"],
            f["actor"],
            reason="不同材料",
            decision="confirmed",
            request_key="review:1",
        )
    detail = repo.get_assessment(row["id"], f["actor"])
    assert detail["result"]["risk_score"] == 37
    assert len(repo.get_reviews(row["id"])) == 1


def test_segmented_history_isolates_actor_company_scope_policy(assessment_factory):
    f = assessment_factory
    for scope, version in [
        ("category:a", "one"),
        ("category:a", "two"),
        ("category:b", "one"),
    ]:
        row = repo.begin_assessment(
            identity(f, purchase_scope=scope),
            actor_id=f["actor"],
            request_key=scope + version,
            policy={**current_policy(["financial"]), "version": version},
        )
        repo.freeze_assessment(
            row["id"], str(row["collector_token"]), inputs={}, manifest=[]
        )
        repo.commit_assessment(
            row["id"],
            str(row["collector_token"]),
            result={"risk_score": 37},
            quality="partial",
        )
    rows = repo.list_assessments(f["companies"][0], f["actor"], days=90)
    assert len(rows) == 3
    assert len({(r["scope_key"], r["policy_hash"]) for r in rows}) == 3
    assert repo.list_assessments(f["companies"][1], f["actor"], days=90) == []
    assert repo.list_assessments(f["companies"][0], str(uuid4()), days=90) == []


def test_frozen_retry_ignores_new_provider_data_and_policy(
    assessment_factory, monkeypatch
):
    from app.domains.risk import assessment_service as app
    from app.domains.risk.assessment_evidence import make_evidence
    from app.domains.risk.assessment_scoring import score_frozen_inputs
    from app.schemas import RiskCalculateRequest, FinancialMetrics
    from app.schemas.company import CompanyProfile
    from app.schemas.risk import RiskInfo
    from app.db.mongo import get_db

    f = assessment_factory
    request = RiskCalculateRequest(
        company=CompanyProfile(
            company_name="同名公司",
            legal_person="",
            registered_capital="",
            establish_time="",
            industry="制造业",
            is_listed=False,
        ),
        risk=RiskInfo(
            lawsuit_count=0,
            executed_count=0,
            abnormal_operation_count=0,
            administrative_penalty_count=0,
        ),
        financial=FinancialMetrics(
            debt_ratio=0.8, revenue_growth=0, net_profit_growth=0, cash_flow=0
        ),
    )

    def collect(row, **kwargs):
        entry, raw = make_evidence(
            row,
            "financial",
            {"metrics": request.financial.model_dump()},
            status="available",
            source="fixture",
            policy=row["policy_snapshot"],
            observed_at=__import__("datetime").datetime.now(
                __import__("datetime").timezone.utc
            ),
        )
        return (
            {
                "request": request.model_dump(mode="json"),
                "recent_lawsuits": 0,
                "soft": {},
            },
            [entry],
            [raw],
        )

    monkeypatch.setattr(app, "_collect_new_inputs", collect)

    def lost(*args):
        raise RuntimeError("crash after frozen")

    monkeypatch.setattr(app, "score_frozen_inputs", lost)
    args = {
        "company_id": f["companies"][0],
        "actor_id": f["actor"],
        "request_key": "frozen-retry",
        "dimensions": ["financial"],
        "refresh_sources": False,
    }
    with pytest.raises(RuntimeError, match="crash after frozen"):
        app.assess_company("同名公司", **args)
    with get_cursor() as (_, cur):
        cur.execute(
            "SELECT id FROM assessment_history WHERE actor_id=%s AND request_key=%s",
            (f["actor"], "frozen-retry"),
        )
        assessment_id = str(cur.fetchone()[0])
    original = repo.get_assessment(assessment_id, f["actor"])
    monkeypatch.setattr(
        app,
        "_collect_new_inputs",
        lambda *a, **k: pytest.fail("cannot recollect frozen evidence"),
    )
    monkeypatch.setattr(
        app,
        "current_policy",
        lambda dimensions: {**current_policy(dimensions), "version": "future-policy"},
    )
    monkeypatch.setattr(app, "score_frozen_inputs", score_frozen_inputs)
    completed = app.assess_company("同名公司", **args)
    assert (
        completed["id"] == assessment_id
        and completed["policy_hash"] == original["policy_hash"]
    )
    assert completed["input_digest"] == original["input_digest"]
    get_db()["agent_evidence_payloads"].delete_many({"assessment_id": assessment_id})


def test_run_result_receipt_is_atomic_and_old_attempt_cannot_commit(assessment_factory):
    f = assessment_factory
    with execution_scope(f["permit"]):
        row = repo.begin_assessment(
            identity(f),
            actor_id=f["actor"],
            run_id=f["run"],
            logical_key="risk-task",
            policy=current_policy(["financial"]),
        )
        repo.freeze_assessment(
            row["id"], str(row["collector_token"]), inputs={}, manifest=[]
        )
        repo.commit_assessment(
            row["id"],
            str(row["collector_token"]),
            result={"risk_score": 80},
            quality="partial",
        )
    with get_cursor() as (_, cur):
        cur.execute(
            "SELECT assessment_id,receipt FROM assessment_task_results WHERE run_id=%s AND logical_key=%s",
            (f["run"], "risk-task"),
        )
        assessment_id, receipt = cur.fetchone()
        assert str(assessment_id) == row["id"] and receipt["assessment_id"] == row["id"]
    RunCoordinator().cancel(f["run"], f["actor"])
    with execution_scope(f["permit"]), pytest.raises(ExecutionLost):
        repo.commit_assessment(
            row["id"],
            str(row["collector_token"]),
            result={"risk_score": 80},
            quality="partial",
        )


def test_display_name_change_keeps_same_business_identity(assessment_factory):
    f = assessment_factory
    first = begin(f)
    again = repo.begin_assessment(
        identity(f, company_name="企业新名称"),
        actor_id=f["actor"],
        request_key="request:0",
        policy=current_policy(["financial"]),
    )
    assert again["id"] == first["id"]
    assert again["company_name"] == first["company_name"]


def test_partial_mongo_write_is_reconciled_from_pg_intent(
    assessment_factory, monkeypatch
):
    from app.domains.risk import assessment_service as app
    from app.domains.risk.assessment_evidence import make_evidence
    from app.domains.risk.assessment_projection import reconcile_assessment_payloads
    from app.domains.sourcing_risk.evidence_service import stage_raw_payloads
    from app.db.mongo import get_db

    f = assessment_factory

    def collect(row, **kwargs):
        entry, raw = make_evidence(
            row,
            "financial",
            {"known": "fixture"},
            status="unavailable",
            source="fixture",
            policy=row["policy_snapshot"],
        )
        return {}, [entry], [raw]

    def uncertain(payloads, **kwargs):
        stage_raw_payloads(payloads, **kwargs)
        raise RuntimeError("lost Mongo write acknowledgement")

    monkeypatch.setattr(app, "_collect_new_inputs", collect)
    monkeypatch.setattr(app, "stage_raw_payloads", uncertain)
    with pytest.raises(RuntimeError, match="acknowledgement"):
        app.assess_company(
            "同名公司",
            company_id=f["companies"][0],
            actor_id=f["actor"],
            request_key="uncertain",
            dimensions=["financial"],
        )
    with get_cursor() as (_, cur):
        cur.execute(
            "SELECT id,assessment_status FROM assessment_history WHERE actor_id=%s AND request_key=%s",
            (f["actor"], "uncertain"),
        )
        assessment_id, status = cur.fetchone()
    assert status == "needs_review"
    assessment_id = str(assessment_id)
    assert (
        get_db()["agent_evidence_payloads"].count_documents(
            {"assessment_id": assessment_id}
        )
        == 1
    )
    event = {"payload": {"assessment_id": assessment_id}}
    reconcile_assessment_payloads(event)
    reconcile_assessment_payloads(event)
    assert (
        get_db()["agent_evidence_payloads"].count_documents(
            {"assessment_id": assessment_id}
        )
        == 0
    )
    assert repo.list_payload_staging(assessment_id)[0]["status"] == "compensated"


def test_expired_nonchat_collection_becomes_reviewable(assessment_factory):
    f = assessment_factory
    row = begin(f)
    with get_cursor() as (_, cur):
        cur.execute(
            "UPDATE assessment_history SET collection_lease_until=NOW()-INTERVAL '1 second' WHERE id=%s",
            (row["id"],),
        )
    again = begin(f)
    assert again["id"] == row["id"]
    assert again["assessment_status"] == "needs_review"


def test_new_policy_first_version_is_latest_trend(assessment_factory, monkeypatch):
    from app.domains.risk import assessment_service as app

    f = assessment_factory
    subject = identity(f)
    rows = []
    for version, score in [("old", 20), ("new", 80)]:
        row = repo.begin_assessment(
            subject,
            actor_id=f["actor"],
            request_key="policy:" + version,
            policy={**current_policy(["financial"]), "version": version},
        )
        repo.freeze_assessment(
            row["id"], str(row["collector_token"]), inputs={}, manifest=[]
        )
        repo.commit_assessment(
            row["id"],
            str(row["collector_token"]),
            result={"risk_score": score, "risk_level": "fixture"},
            quality="partial",
        )
        rows.append(row)
    with get_cursor() as (_, cur):
        cur.execute(
            "UPDATE assessment_history SET assessment_version=100,assessed_at=NOW()-INTERVAL '1 day' WHERE id=%s",
            (rows[0]["id"],),
        )
    monkeypatch.setattr(app, "resolve_identity", lambda *a, **k: subject)
    monkeypatch.setattr(app, "current_policy", lambda *a: current_policy(["financial"]))
    data = app.comparable_trend("同名公司", actor_id=f["actor"], actor_role="purchaser")
    assert len(data) == 1 and data[0]["risk_score"] == 80
