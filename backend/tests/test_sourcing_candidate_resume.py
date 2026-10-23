import asyncio
from typing import Any

import pytest
from langgraph.types import Command

from app.domains.agent_run import service
from app.domains.agent_run.models import AgentRunStatus
from app.graphs.sourcing_risk_v2 import nodes, runner
from app.workers import agent_worker


RUN_ID = "00000000-0000-4000-8000-000000000001"
BATCH_ID = "00000000-0000-4000-8000-000000000002"
PARENT_CANDIDATE_ID = "00000000-0000-4000-8000-000000000003"
CHILD_RUN_ID = "00000000-0000-4000-8000-000000000004"
USER_ID = "00000000-0000-0000-0000-000000000011"


def _candidate(key: str, company_id: str | None, **extra: Any) -> dict[str, Any]:
    return {"candidate_key": key, "company_id": company_id, "identity_status": "exact", **extra}


def test_investigate_parallel_only_calls_providers_for_eligible_group(monkeypatch: pytest.MonkeyPatch):
    eligible = _candidate("candidate-a", "company-a")
    deferred = _candidate("candidate-b", None, disposition="defer")
    excluded = _candidate("candidate-c", None, disposition="exclude")
    provider_calls: list[str] = []
    snapshots: list[dict[str, Any]] = []

    async def provider(candidate: dict[str, Any]):
        provider_calls.append(str(candidate["candidate_key"]))
        return {"claim_code": "clear", "source_type": "test_provider", "summary": ""}

    async def normalize(_run_id: str, _company_id: str, evidence: list[dict[str, Any]], _policy: dict):
        return evidence, []

    async def snapshot(_run_id: str, _status: str, _event_type: str, _payload: dict, **collections: Any):
        snapshots.append(collections)

    monkeypatch.setattr(nodes, "fetch_financial", provider)
    monkeypatch.setattr(nodes, "fetch_judicial", provider)
    monkeypatch.setattr(nodes, "fetch_sentiment", provider)
    monkeypatch.setattr(nodes, "fetch_sanctions", provider)
    monkeypatch.setattr(nodes, "fetch_esg", provider)
    monkeypatch.setattr(nodes, "fetch_continuity", provider)
    monkeypatch.setattr(nodes, "_normalize_candidate_evidence", normalize)
    monkeypatch.setattr(nodes, "_snapshot_event", snapshot)

    result = asyncio.run(nodes.investigate_parallel({
        "run_id": RUN_ID,
        "candidates": [eligible, deferred, excluded],
        "eligible_candidates": [eligible],
        "deferred_candidates": [deferred],
        "excluded_candidates": [excluded],
        "policy_snapshot": {},
    }))

    assert provider_calls == ["candidate-a"] * len(nodes.PROVIDER_DIMENSIONS)
    assert result["candidates"] == [eligible]
    assert snapshots[0]["candidates"] == [eligible]


def test_scoring_only_receives_eligible_candidates(monkeypatch: pytest.MonkeyPatch):
    company_id = "00000000-0000-4000-8000-000000000088"
    eligible = _candidate(
        "candidate-a",
        company_id,
        identity_company_id=company_id,
        score_eligible=True,
        active=True,
        categories=["摄像头"],
        dimension_scores={
            "match": 90,
            "capacity": 90,
            "performance": 90,
            "quality": 90,
            "risk": 90,
            "commercial": 90,
        },
    )
    deferred = _candidate("candidate-b", None, disposition="defer")
    monkeypatch.setattr(nodes, "_snapshot_event", lambda *_args, **_kwargs: asyncio.sleep(0))

    result = asyncio.run(nodes.score_candidates({
        "run_id": RUN_ID,
        "requirement": {"category": "摄像头"},
        "policy_snapshot": {
            "weights": {"match": 0.25, "capacity": 0.2, "performance": 0.15, "quality": 0.15, "risk": 0.15, "commercial": 0.1},
            "missing_penalties": {key: 0 for key in ("match", "capacity", "performance", "quality", "risk", "commercial")},
            "stale_penalties": {key: 0 for key in ("match", "capacity", "performance", "quality", "risk", "commercial")},
            "required_evidence": ["sanctions"],
            "freshness_days": {"sanctions": 30},
            "hard_gates": {
                "unmatched_category": "rejected",
                "supplier_not_active": "rejected",
                "mandatory_qualification_missing": "rejected",
                "identity_unverified": "needs_review",
                "sanctions_hit": "rejected",
                "sanctions_available": "needs_review",
                "key_evidence_conflict": "needs_review",
            },
        },
        "candidates": [eligible, deferred],
        "eligible_candidates": [eligible],
        "deferred_candidates": [deferred],
        "evidence_by_company_id": {company_id: [{"dimension": "sanctions", "claim_code": "clear", "freshness_status": "fresh", "conflict_status": "none"}]},
    }))

    assert [item["company_id"] for item in result["decisions"]] == [company_id]


def test_identity_review_routes_pending_result_to_end_instead_of_investigation():
    result = nodes.route_after_identity_review({
        "status": "IDENTITY_REVIEW",
        "candidates": [_candidate("candidate-unresolved", None)],
        "eligible_candidates": [],
        "pending_review_ids": ["candidate-unresolved"],
    })

    assert result == "end"


def test_identity_review_rebuilds_persisted_candidate_groups_on_batch_resume(
    monkeypatch: pytest.MonkeyPatch,
):
    eligible = _candidate("candidate-a", "company-a", candidate_id="persisted-a")
    deferred = _candidate("candidate-b", None, candidate_id="persisted-b", disposition="defer")
    excluded = _candidate("candidate-c", None, candidate_id="persisted-c", disposition="exclude")
    persisted: list[dict[str, Any]] = []

    monkeypatch.setattr(nodes, "interrupt", lambda *_: {"candidate_review_batch_id": BATCH_ID})
    monkeypatch.setattr(
        nodes.agent_run_service,
        "get_candidate_review_groups_for_batch",
        lambda _batch_id: {
            "eligible": [eligible],
            "deferred": [deferred],
            "excluded": [excluded],
            "all_candidates": [eligible, deferred, excluded],
        },
        raising=False,
    )
    monkeypatch.setattr(
        nodes,
        "_snapshot_event",
        lambda *_args, **kwargs: asyncio.sleep(0, result=persisted.append(kwargs) or {}),
    )

    result = asyncio.run(nodes.identity_review({
        "run_id": RUN_ID,
        "candidates": [_candidate("candidate-a", "company-a")],
        "pending_review_ids": ["candidate-b", "candidate-c"],
    }))

    assert result["eligible_candidates"] == [eligible]
    assert result["candidates"] == [eligible]
    assert result["deferred_candidates"] == [deferred]
    assert result["excluded_candidates"] == [excluded]
    assert persisted[0]["candidates"] == [eligible, deferred, excluded]


def test_worker_claims_candidate_review_before_harness_jobs(monkeypatch: pytest.MonkeyPatch):
    calls: list[str] = []
    monkeypatch.setattr(
        agent_worker.execution_jobs,
        "claim_candidate_review_batch",
        lambda *_args, **_kwargs: calls.append("candidate_batch") or {
            "batch_id": BATCH_ID,
            "run_id": RUN_ID,
            "fencing_token": 3,
            "status": "processing",
        },
        raising=False,
    )
    monkeypatch.setattr(
        agent_worker.execution_jobs,
        "claim_job",
        lambda *_args, **_kwargs: pytest.fail("candidate batch must be handled before Harness jobs"),
    )
    monkeypatch.setattr(
        "app.graphs.sourcing_risk_v2.runner.resume_candidate_review_batch",
        lambda batch_id: calls.append(batch_id) or asyncio.sleep(0, result={"status": "consumed"}),
        raising=False,
    )

    result = asyncio.run(agent_worker.run_worker_once_async("worker-a"))

    assert result["candidate_batch"]["status"] == "consumed"
    assert calls == ["candidate_batch", BATCH_ID]


def test_followup_reuses_parent_policy_and_skips_discovery(monkeypatch: pytest.MonkeyPatch):
    parent_policy = {"checksum": "parent-policy", "weights": {"risk": 0.7}}
    candidate = _candidate("followup:parent:candidate-a", None, candidate_id="child-candidate")
    context = {
        "child_candidate_ids": ["child-candidate"],
        "policy_snapshot_id": "parent-policy-id",
        "candidates": [candidate],
        "policy_snapshot": parent_policy,
    }
    monkeypatch.setattr(nodes, "freeze_policy_snapshot", lambda *_: pytest.fail("follow-up must not refreeze policy"))
    monkeypatch.setattr(
        nodes.agent_run_service,
        "persist_sourcing_policy_snapshot",
        lambda *_: pytest.fail("follow-up already references the parent's immutable policy"),
    )
    monkeypatch.setattr(nodes, "_event", lambda *_args, **_kwargs: asyncio.sleep(0))

    policy_update = asyncio.run(nodes.lock_policy({
        "run_id": CHILD_RUN_ID,
        "requirement": {"category": "摄像头"},
        "candidate_followup_context": context,
    }))

    monkeypatch.setattr(nodes, "discover_local_candidates", lambda *_: pytest.fail("follow-up cannot rediscover"))
    monkeypatch.setattr(nodes, "is_candidate_supply_sufficient", lambda *_: pytest.fail("follow-up does not need discovery supply checks"))
    monkeypatch.setattr(nodes, "_snapshot_event", lambda *_args, **_kwargs: asyncio.sleep(0, result={}))

    discovery_update = asyncio.run(nodes.local_discovery({
        "run_id": CHILD_RUN_ID,
        "requirement": {"category": "摄像头"},
        "policy_snapshot": parent_policy,
        "candidate_followup_context": context,
        "candidates": [candidate],
    }))

    assert policy_update["policy_snapshot_id"] == "parent-policy-id"
    assert policy_update["policy_snapshot"] == parent_policy
    assert discovery_update["candidates"] == [candidate]
    assert discovery_update["next_action"] is None


def test_followup_context_loads_only_child_candidates_and_parent_policy(monkeypatch: pytest.MonkeyPatch):
    selected = {"id": "child-candidate", "candidate_id": "child-candidate", "candidate_key": "followup:a:b"}
    ignored = {"id": "other-candidate", "candidate_id": "other-candidate", "candidate_key": "other"}

    class CursorContext:
        def __enter__(self):
            return None, object()

        def __exit__(self, *_):
            return False

    monkeypatch.setattr(service, "get_candidate_followup_context", lambda _run_id: {
        "parent_run_id": RUN_ID,
        "child_candidate_ids": ["child-candidate"],
        "policy_snapshot_id": "parent-policy-id",
    })
    monkeypatch.setattr(service, "get_sourcing_policy_snapshot", lambda _snapshot_id: {
        "id": "parent-policy-id", "policy": {"checksum": "parent-policy"},
    })
    monkeypatch.setattr(service, "get_cursor", CursorContext)
    monkeypatch.setattr(service, "list_candidates_with_cursor", lambda *_: [selected, ignored])

    result = service.get_sourcing_candidate_followup_context(CHILD_RUN_ID)

    assert result["parent_run_id"] == RUN_ID
    assert result["candidates"] == [selected]
    assert result["policy_snapshot_id"] == "parent-policy-id"
    assert result["policy_snapshot"] == {"checksum": "parent-policy"}


def test_partial_candidate_review_finishes_run_as_partial_and_keeps_paused_groups(
    monkeypatch: pytest.MonkeyPatch,
):
    events: list[tuple[str, dict[str, Any]]] = []
    monkeypatch.setattr(nodes, "_event", lambda _run_id, event, payload, status: events.append((status, payload)) or asyncio.sleep(0))
    deferred = _candidate("candidate-b", None, disposition="defer", reason="待补资料")
    excluded = _candidate("candidate-c", None, disposition="exclude", reason="不符合范围")

    result = asyncio.run(nodes.ready_for_review({
        "run_id": RUN_ID,
        "decisions": [{"candidate_key": "candidate-a", "group": "recommended"}],
        "deferred_candidates": [deferred],
        "excluded_candidates": [excluded],
    }))

    assert result["status"] == "PARTIAL"
    assert result["deferred_candidates"] == [deferred]
    assert result["excluded_candidates"] == [excluded]
    assert events[0][0] == "PARTIAL"


def test_candidate_review_does_not_add_public_run_statuses():
    assert set(AgentRunStatus.__members__) == {
        "CREATED", "CLARIFYING", "POLICY_LOCKED", "LOCAL_SEARCHING", "EXTERNAL_REVIEW",
        "IDENTITY_RESOLVING", "IDENTITY_REVIEW", "INVESTIGATING", "EVIDENCE_REVIEW", "SCORING",
        "READY_FOR_REVIEW", "ACTION_PENDING", "ACTION_EXECUTING", "COMPLETED", "PARTIAL",
        "NEEDS_REVIEW", "ACTION_FAILED", "FAILED", "CANCELLED", "ROLLBACK_FROZEN",
    }


def test_cancelled_review_batch_is_not_resumed(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(
        runner.agent_run_service,
        "get_candidate_review_batch_for_execution",
        lambda _batch_id: {"batch_id": BATCH_ID, "dispatch_status": "cancelled", "run_status": "CANCELLED"},
        raising=False,
    )
    monkeypatch.setattr(runner, "_resume", lambda *_: pytest.fail("cancelled batch must not resume"), raising=False)

    result = asyncio.run(runner.resume_candidate_review_batch(BATCH_ID))

    assert result["status"] == "cancelled"


def test_consumed_review_batch_is_idempotent(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(
        runner.agent_run_service,
        "get_candidate_review_batch_for_execution",
        lambda _batch_id: {"batch_id": BATCH_ID, "dispatch_status": "consumed", "run_status": "PARTIAL"},
        raising=False,
    )
    monkeypatch.setattr(runner, "_resume", lambda *_: pytest.fail("consumed batch must not progress again"), raising=False)

    result = asyncio.run(runner.resume_candidate_review_batch(BATCH_ID))

    assert result["status"] == "already_consumed"


def test_resume_candidate_review_batch_passes_a_langgraph_command(monkeypatch: pytest.MonkeyPatch):
    resumed: list[tuple[str, Any]] = []
    monkeypatch.setattr(
        runner.agent_run_service,
        "get_candidate_review_batch_for_execution",
        lambda _batch_id: {
            "batch_id": BATCH_ID,
            "run_id": RUN_ID,
            "dispatch_status": "processing",
            "run_status": "IDENTITY_RESOLVING",
            "run_version": 4,
            "lease_token": 7,
        },
        raising=False,
    )
    monkeypatch.setattr(runner, "_resume", lambda run_id, payload: resumed.append((run_id, payload)) or asyncio.sleep(0), raising=False)
    monkeypatch.setattr(
        runner.agent_run_service,
        "mark_candidate_review_batch_consumed",
        lambda *_args: True,
        raising=False,
    )

    result = asyncio.run(runner.resume_candidate_review_batch(BATCH_ID))

    assert result["status"] == "consumed"
    assert resumed == [(RUN_ID, {"candidate_review_batch_id": BATCH_ID})]


def test_terminal_followup_creates_child_without_mutating_parent(monkeypatch: pytest.MonkeyPatch):
    parent = {
        "id": RUN_ID,
        "run_type": "sourcing_risk_v2",
        "user_id": USER_ID,
        "status": "PARTIAL",
        "version": 9,
        "requirement": {"requirement_text": "采购工业摄像头", "category": "摄像头"},
        "policy_snapshot_id": "00000000-0000-4000-8000-000000000099",
    }
    candidates = [{
        "id": PARENT_CANDIDATE_ID,
        "candidate_id": PARENT_CANDIDATE_ID,
        "candidate_key": "local:vendor-a",
        "company_id": None,
        "supplier_name": "供应商甲",
        "identity_status": "pending_verification",
        "identity_candidates": [{"company_id": "00000000-0000-4000-8000-000000000088"}],
    }]
    dispositions = [{"candidate_id": PARENT_CANDIDATE_ID, "action": "defer", "company_id": None, "reason": "待补资料"}]
    created: list[dict[str, Any]] = []
    cursor = object()

    class CursorContext:
        def __enter__(self):
            return None, cursor

        def __exit__(self, *_):
            return False

    monkeypatch.setattr(service, "get_cursor", CursorContext)
    monkeypatch.setattr(service, "get_run_for_update", lambda *_: parent)
    monkeypatch.setattr(service, "lock_candidate_followup_request_with_cursor", lambda *_: None)
    monkeypatch.setattr(service, "lock_candidate_review_request_with_cursor", lambda *_: None)
    monkeypatch.setattr(service, "get_candidate_followup_event_by_request_with_cursor", lambda *_: None, raising=False)
    monkeypatch.setattr(service, "list_candidates_with_cursor", lambda *_: candidates)
    monkeypatch.setattr(service, "list_sourcing_candidate_dispositions_with_cursor", lambda *_: dispositions)
    monkeypatch.setattr(service, "get_policy_snapshot_by_id_with_cursor", lambda *_: {"id": parent["policy_snapshot_id"], "policy": {"checksum": "policy-a"}})
    monkeypatch.setattr(service, "update_run_policy_snapshot_with_cursor", lambda *_: None)
    monkeypatch.setattr(service, "insert_run", lambda **kwargs: created.append({"run": kwargs}) or {"id": CHILD_RUN_ID, "version": 1, "status": "CREATED"})
    monkeypatch.setattr(service, "persist_run_snapshot", lambda *_args, **kwargs: created.append({"snapshot": kwargs}) or {"candidates": []})
    monkeypatch.setattr(service, "insert_candidate_followup_with_cursor", lambda **kwargs: created.append({"followup": kwargs}), raising=False)
    monkeypatch.setattr(service, "insert_candidate_followup_event_with_cursor", lambda **kwargs: created.append({"event": kwargs}), raising=False)
    monkeypatch.setattr(service, "append_event", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(service, "update_run_status", lambda *_args, **_kwargs: pytest.fail("parent run must stay terminal"))

    result = service.derive_candidate_followup(
        RUN_ID, [PARENT_CANDIDATE_ID], USER_ID, "purchaser", "followup-key"
    )

    assert {key: value for key, value in result.items() if not key.startswith("_")} == {
        "parent_run_id": RUN_ID,
        "run_id": CHILD_RUN_ID,
        "resume_url": f"/sourcing?run={CHILD_RUN_ID}",
    }
    assert result["_start_required"] is True
    assert created[0]["run"]["requirement"] == parent["requirement"]
    assert created[0]["run"]["cur"] is cursor
    assert created[1]["snapshot"]["candidates"][0]["parent_candidate_id"] == PARENT_CANDIDATE_ID


def test_terminal_followup_rejects_excluded_candidates(monkeypatch: pytest.MonkeyPatch):
    parent = {
        "id": RUN_ID,
        "run_type": "sourcing_risk_v2",
        "user_id": USER_ID,
        "status": "NEEDS_REVIEW",
        "version": 9,
        "requirement": {},
        "policy_snapshot_id": "00000000-0000-4000-8000-000000000099",
    }

    class CursorContext:
        def __enter__(self):
            return None, object()

        def __exit__(self, *_):
            return False

    monkeypatch.setattr(service, "get_cursor", CursorContext)
    monkeypatch.setattr(service, "get_run_for_update", lambda *_: parent)
    monkeypatch.setattr(service, "lock_candidate_followup_request_with_cursor", lambda *_: None)
    monkeypatch.setattr(service, "lock_candidate_review_request_with_cursor", lambda *_: None)
    monkeypatch.setattr(service, "get_candidate_followup_event_by_request_with_cursor", lambda *_: None, raising=False)
    monkeypatch.setattr(service, "get_policy_snapshot_by_id_with_cursor", lambda *_: {"id": parent["policy_snapshot_id"], "policy": {}})
    monkeypatch.setattr(service, "list_candidates_with_cursor", lambda *_: [{"id": PARENT_CANDIDATE_ID, "candidate_id": PARENT_CANDIDATE_ID}])
    monkeypatch.setattr(service, "list_sourcing_candidate_dispositions_with_cursor", lambda *_: [{"candidate_id": PARENT_CANDIDATE_ID, "action": "exclude"}])

    with pytest.raises(Exception, match="排除"):
        service.derive_candidate_followup(RUN_ID, [PARENT_CANDIDATE_ID], USER_ID, "purchaser", "excluded-key")


def test_terminal_excluded_candidate_can_be_explicitly_changed_to_deferred(
    monkeypatch: pytest.MonkeyPatch,
):
    run = {
        "id": RUN_ID,
        "run_type": "sourcing_risk_v2",
        "user_id": USER_ID,
        "status": "PARTIAL",
        "version": 9,
    }
    candidate = {
        "id": PARENT_CANDIDATE_ID,
        "candidate_id": PARENT_CANDIDATE_ID,
        "candidate_key": "persisted-candidate",
        "identity_status": "pending_verification",
        "identity_candidates": [],
    }
    untouched_excluded = {
        "id": "00000000-0000-4000-8000-000000000005",
        "candidate_id": "00000000-0000-4000-8000-000000000005",
        "candidate_key": "another-excluded",
        "identity_status": "pending_verification",
        "identity_candidates": [],
    }
    cursor = object()
    disposition_writes: list[dict[str, Any]] = []

    class CursorContext:
        def __enter__(self):
            return None, cursor

        def __exit__(self, *_):
            return False

    monkeypatch.setattr(service, "get_cursor", CursorContext)
    monkeypatch.setattr(service, "get_run_for_update", lambda *_: run)
    monkeypatch.setattr(service, "_authorize_locked_sourcing_run", lambda *_: None)
    monkeypatch.setattr(service, "lock_candidate_review_request_with_cursor", lambda *_: None)
    monkeypatch.setattr(service, "get_candidate_review_event_by_request_with_cursor", lambda *_: None)
    monkeypatch.setattr(service, "list_candidates_with_cursor", lambda *_: [candidate, untouched_excluded])
    monkeypatch.setattr(service, "list_sourcing_candidate_dispositions_with_cursor", lambda *_: [
        {"candidate_id": PARENT_CANDIDATE_ID, "action": "exclude", "reason": "先前排除"},
        {"candidate_id": untouched_excluded["id"], "action": "exclude", "reason": "保持排除"},
    ])
    monkeypatch.setattr(service, "upsert_sourcing_candidate_disposition_with_cursor", lambda **kwargs: disposition_writes.append(kwargs))
    monkeypatch.setattr(service, "touch_terminal_run_with_cursor", lambda _cursor, _run_id, expected, status: {**run, "version": expected + 1, "status": status})
    monkeypatch.setattr(service, "update_run_status", lambda *_args, **_kwargs: pytest.fail("terminal reclassification must preserve terminal state"))
    monkeypatch.setattr(service, "append_event", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(service, "insert_candidate_review_event_with_cursor", lambda **kwargs: None)

    response = service.submit_candidate_decisions(
        RUN_ID,
        {
            "expected_version": 9,
            "decisions": [{
                "candidate_key": PARENT_CANDIDATE_ID,
                "action": "defer",
                "reason": "补齐资料后再核验",
            }],
        },
        USER_ID,
        "purchaser",
        "reclassify-key",
    )

    assert disposition_writes[0]["action"] == "defer"
    assert disposition_writes[0]["reason"] == "补齐资料后再核验"
    assert len(disposition_writes) == 1
    assert response["resume_status"] == "followup_available"
    assert run["status"] == "PARTIAL"
