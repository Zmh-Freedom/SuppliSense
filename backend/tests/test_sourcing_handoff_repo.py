"""Offline persistence contract tests for sourcing handoffs."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any

import pytest

from app.domains.sourcing_risk import handoff_repo

USER_ID = "00000000-0000-0000-0000-000000000011"
RUN_ID = "00000000-0000-4000-8000-000000000001"
CANDIDATE_ID = "00000000-0000-4000-8000-000000000002"
SESSION_ID = "00000000-0000-4000-8000-000000000006"
HANDOFF_ID = "00000000-0000-4000-8000-000000000007"


class FakeCursor:
    def __init__(self, respond):
        self.respond = respond
        self.description = []
        self.one = None
        self.many: list[tuple[Any, ...]] = []
        self.rowcount = 0
        self.statements: list[tuple[str, tuple[Any, ...]]] = []

    def execute(self, query: str, params: tuple[Any, ...] | None = None) -> None:
        self.statements.append((query, tuple(params or ())))
        names, self.one, self.many, self.rowcount = self.respond(query, tuple(params or ()))
        self.description = [(name,) for name in names]

    def fetchone(self):
        return self.one

    def fetchall(self):
        return self.many


def _columns(*names: str):
    return names


def test_create_handoff_transaction_creates_session_and_binding_on_one_cursor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = datetime.now(timezone.utc)
    candidate_snapshot = {"supplier_name": "候选工厂", "identity_status": "ambiguous"}
    binding_columns = _columns(
        "id", "actor_id", "session_id", "source_run_id", "source_run_version", "candidate_id",
        "company_id", "candidate_snapshot", "requirement_snapshot", "evidence_refs", "intent",
        "status", "request_key", "request_hash", "continue_message", "consumed_turn_id",
        "consumed_message_hash", "run_id", "created_at", "updated_at", "consumed_at",
    )

    def respond(query: str, _params: tuple[Any, ...]):
        if "SELECT * FROM sourcing_handoffs WHERE actor_id" in query:
            return [], None, [], 0
        if "FROM agent_runs WHERE id=%s AND user_id=%s" in query:
            return _columns("id", "run_type", "user_id", "status", "version", "requirement"), (
                RUN_ID, "sourcing_risk_v2", USER_ID, "COMPLETED", 7, {"requirement_text": "采购工业相机"},
            ), [], 1
        if "FROM agent_run_candidates AS candidate" in query:
            return _columns(
                "id", "company_id", "source", "status", "candidate_snapshot",
                "disposition_action", "disposition_company_id",
            ), (CANDIDATE_ID, None, "staged_external", "identity_review", candidate_snapshot, None, None), [], 1
        if "FROM agent_evidence" in query:
            return _columns("id", "evidence_type", "source", "source_reference", "evidence_snapshot"), None, [], 0
        if "INSERT INTO agent_sessions" in query:
            return _columns("id", "user_id", "status", "version", "state", "created_at", "updated_at"), (
                SESSION_ID, USER_ID, "active", 1, {}, now, now,
            ), [], 1
        if "INSERT INTO sourcing_handoffs" in query:
            return binding_columns, (
                HANDOFF_ID, USER_ID, SESSION_ID, RUN_ID, 7, CANDIDATE_ID, None,
                {"candidate_key": CANDIDATE_ID, **candidate_snapshot},
                {"requirement_text": "采购工业相机"}, [], "risk_review", "ready",
                "handoff-key", "a" * 64, "请核验候选工厂", None, None, None, now, now, None,
            ), [], 1
        return [], None, [], 0

    cursor = FakeCursor(respond)
    cursor_context_count = 0

    @contextmanager
    def fake_get_cursor():
        nonlocal cursor_context_count
        cursor_context_count += 1
        yield object(), cursor

    monkeypatch.setattr(handoff_repo, "get_cursor", fake_get_cursor)
    source = {
        "run": {"id": RUN_ID, "run_type": "sourcing_risk_v2", "user_id": USER_ID, "version": 7},
        "candidate": {"candidate_key": CANDIDATE_ID, "supplier_name": "候选工厂"},
        "evidence_refs": [],
    }

    result = handoff_repo.create_handoff_transaction(
        source,
        {"run_id": RUN_ID, "candidate_key": CANDIDATE_ID, "expected_version": 7, "intent": "risk_review"},
        USER_ID,
        "purchaser",
        "handoff-key",
        "a" * 64,
    )

    statements = [query for query, _params in cursor.statements]
    assert cursor_context_count == 1
    assert result["id"] == HANDOFF_ID
    assert result["session_id"] == SESSION_ID
    assert next(i for i, query in enumerate(statements) if "INSERT INTO agent_sessions" in query) < next(
        i for i, query in enumerate(statements) if "INSERT INTO sourcing_handoffs" in query
    )


def test_get_handoff_by_session_maps_database_columns_to_service_contract(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    names = _columns(
        "id", "actor_id", "session_id", "source_run_id", "source_run_version", "candidate_id",
        "candidate_snapshot", "requirement_snapshot", "evidence_refs", "intent", "status",
        "continue_message", "consumed_turn_id", "run_id",
    )
    row = (
        HANDOFF_ID, USER_ID, SESSION_ID, RUN_ID, 7, CANDIDATE_ID,
        {"supplier_name": "候选工厂"}, {"requirement_text": "采购工业相机"}, [], "risk_review",
        "ready", "请继续", None, None,
    )
    cursor = FakeCursor(lambda _query, _params: (names, row, [], 1))

    @contextmanager
    def fake_get_cursor():
        yield object(), cursor

    monkeypatch.setattr(handoff_repo, "get_cursor", fake_get_cursor)

    binding = handoff_repo.get_handoff_by_session(SESSION_ID)

    assert binding is not None
    assert binding["handoff_id"] == HANDOFF_ID
    assert binding["user_id"] == USER_ID
    assert binding["candidate_key"] == CANDIDATE_ID
    assert binding["candidate"]["supplier_name"] == "候选工厂"
    assert binding["requirement"]["requirement_text"] == "采购工业相机"


def test_read_handoff_source_persists_evidence_references_without_facts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = datetime.now(timezone.utc)
    evidence_snapshot = {
        "dimension": "quality",
        "source_type": "supplier_registry",
        "source_reference": "evidence://quality/1",
        "freshness_status": "fresh",
        "observed_at": "2026-10-20T00:00:00+00:00",
        "claim_code": "quality_certificate",
        "value": "private provider fact",
        "payload": {"raw": "private provider response"},
    }

    def respond(query: str, _params: tuple[Any, ...]):
        if "FROM sourcing_policy_snapshots" in query:
            return _columns("policy"), ({"freshness_days": {"quality": 90}},), [], 1
        if "FROM agent_runs WHERE id=%s AND user_id=%s" in query:
            return _columns("id", "run_type", "user_id", "status", "version", "requirement"), (
                RUN_ID, "sourcing_risk_v2", USER_ID, "COMPLETED", 7, {"requirement_text": "采购工业相机"},
            ), [], 1
        if "FROM agent_run_candidates AS candidate" in query:
            return _columns(
                "id", "company_id", "source", "status", "candidate_snapshot",
                "disposition_action", "disposition_company_id",
            ), (CANDIDATE_ID, None, "staged_external", "identity_review", {"supplier_name": "候选工厂"}, None, None), [], 1
        if "FROM agent_evidence" in query:
            return _columns("id", "evidence_type", "source", "source_reference", "evidence_snapshot", "created_at"), None, [(
                "evidence-1", "quality", "supplier_registry", "evidence://quality/1", evidence_snapshot, now,
            )], 1
        return [], None, [], 0

    cursor = FakeCursor(respond)

    @contextmanager
    def fake_get_cursor():
        yield object(), cursor

    monkeypatch.setattr(handoff_repo, "get_cursor", fake_get_cursor)

    source = handoff_repo.read_handoff_source(RUN_ID, CANDIDATE_ID, USER_ID, "purchaser")

    assert source is not None
    assert source["run"]["policy"]["freshness_days"]["quality"] == 90
    evidence_ref = source["evidence_refs"][0]
    assert evidence_ref == {
        "evidence_id": "evidence-1",
        "dimension": "quality",
        "source_type": "supplier_registry",
        "source_reference": "evidence://quality/1",
        "freshness_status": "fresh",
        "observed_at": "2026-10-20T00:00:00+00:00",
        "collected_at": now.isoformat(),
        "claim_code": "quality_certificate",
    }
    assert "payload" not in evidence_ref
    assert "value" not in evidence_ref
