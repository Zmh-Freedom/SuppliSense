"""Isolated end-to-end procurement case workflow through the real API/service/repo code."""

from __future__ import annotations

import copy
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from psycopg2.extras import Json

from app.core.deps import get_current_user
from app.domains.alert import api_cases, case_access, case_repo, case_service, review_tasks
from app.schemas.user import UserInDB, UserRole


pytestmark = pytest.mark.agent_e2e


def _json_value(value: Any) -> Any:
    return value.adapted if isinstance(value, Json) else value


class _CaseStore:
    def __init__(self) -> None:
        self.cases: dict[str, dict[str, Any]] = {}
        self.case_events: list[dict[str, Any]] = []
        self.case_requests: dict[tuple[str, str], dict[str, Any]] = {}
        self.case_tasks: list[dict[str, Any]] = []
        self.tasks: dict[str, dict[str, Any]] = {}
        self.task_events: list[dict[str, Any]] = []
        self.task_evidence: list[dict[str, Any]] = []

    def snapshot(self) -> dict[str, Any]:
        return copy.deepcopy(self.__dict__)

    def restore(self, snapshot: dict[str, Any]) -> None:
        self.__dict__.clear()
        self.__dict__.update(snapshot)


class _MemoryCursor:
    """Small transactional SQL adapter; production repo functions stay under test."""

    def __init__(self, store: _CaseStore) -> None:
        self.store = store
        self.description: list[tuple[str]] = []
        self._rows: list[tuple[Any, ...]] = []
        self.rowcount = 0

    def _set_rows(self, rows: list[dict[str, Any]], columns: list[str] | None = None) -> None:
        if columns is None:
            columns = list(rows[0]) if rows else []
        self.description = [(column,) for column in columns]
        self._rows = [tuple(row.get(column) for column in columns) for row in rows]
        self.rowcount = len(rows)

    def execute(self, query: str, params: tuple[Any, ...] = ()) -> None:
        sql = " ".join(query.lower().split())
        values = tuple(_json_value(value) for value in params)
        self._rows = []
        self.rowcount = 0

        if sql.startswith("insert into procurement_case_requests"):
            request_id, actor_id, request_key, request_hash = values
            key = (str(actor_id), str(request_key))
            if key not in self.store.case_requests:
                self.store.case_requests[key] = {
                    "id": str(request_id), "actor_id": str(actor_id), "request_key": str(request_key),
                    "request_hash": str(request_hash), "case_id": None, "response": None,
                }
                self._set_rows([{"id": str(request_id)}], ["id"])
            else:
                self._set_rows([], ["id"])
            return

        if sql.startswith("select id, request_hash, response from procurement_case_requests"):
            actor_id, request_key = values
            request = self.store.case_requests.get((str(actor_id), str(request_key)))
            self._set_rows([request] if request else [], ["id", "request_hash", "response"])
            return

        if sql.startswith("select id from procurement_cases where monitor_target_id"):
            target_id, case_type, scope_key, trigger_key = values
            rows = [
                row for row in self.store.cases.values()
                if row["monitor_target_id"] == str(target_id)
                and row["case_type"] == str(case_type)
                and row["scope_key"] == str(scope_key)
                and row["trigger_key"] == str(trigger_key)
                and row["status"] == "closed"
            ]
            rows.sort(key=lambda row: (row["updated_at"], row["id"]), reverse=True)
            self._set_rows(rows[:1], ["id"])
            return

        if sql.startswith("update procurement_case_requests"):
            case_id, response, request_id = values
            request = next((item for item in self.store.case_requests.values() if item["id"] == str(request_id)), None)
            if request:
                request["case_id"] = str(case_id)
                request["response"] = response
                self.rowcount = 1
            return

        if sql.startswith("select * from procurement_cases where monitor_target_id"):
            target_id, case_type, scope_key, trigger_key = values
            closed_only = "status = 'closed'" in sql
            rows = [
                row for row in self.store.cases.values()
                if row["monitor_target_id"] == str(target_id)
                and row["case_type"] == str(case_type)
                and row["scope_key"] == str(scope_key)
                and row["trigger_key"] == str(trigger_key)
                and ((row["status"] == "closed") if closed_only else (row["status"] != "closed"))
            ]
            rows.sort(key=lambda row: (row["updated_at"], row["id"]), reverse=True)
            self._set_rows(rows[:1], list(rows[0]) if rows else list(self._case_columns()))
            return

        if sql.startswith("select * from procurement_cases where id = %s"):
            row = self.store.cases.get(str(values[0]))
            self._set_rows([row] if row else [], list(row) if row else list(self._case_columns()))
            return

        if sql.startswith("insert into procurement_cases"):
            (case_id, target_id, company_id, department, case_type, scope_key, trigger_key,
             title, priority, created_by, owner_id, due_at, next_review_at, previous_id) = values
            active = next((
                row for row in self.store.cases.values()
                if row["monitor_target_id"] == str(target_id)
                and row["case_type"] == str(case_type)
                and row["scope_key"] == str(scope_key)
                and row["trigger_key"] == str(trigger_key)
                and row["status"] != "closed"
            ), None)
            if active:
                self._set_rows([], list(self._case_columns()))
                return
            now = datetime.now(timezone.utc)
            row = {
                "id": str(case_id), "monitor_target_id": str(target_id), "company_id": company_id,
                "department_code": department, "case_type": str(case_type), "scope_key": str(scope_key),
                "trigger_key": str(trigger_key), "title": str(title), "priority": str(priority),
                "status": "open", "created_by": str(created_by), "owner_user_id": owner_id,
                "due_at": due_at, "next_review_at": next_review_at, "version": 1,
                "reopened_from_case_id": previous_id, "closure_request": None, "closed_at": None,
                "created_at": now, "updated_at": now,
            }
            self.store.cases[row["id"]] = row
            self._set_rows([row], list(row))
            return

        if sql.startswith("insert into procurement_case_events"):
            event_id, case_id, actor_id, event_type, from_status, to_status, payload, request_id, version = values
            row = {
                "id": str(event_id), "case_id": str(case_id), "actor_id": actor_id,
                "event_type": str(event_type), "from_status": from_status, "to_status": to_status,
                "payload": payload or {}, "request_id": request_id, "version": int(version),
                "created_at": datetime.now(timezone.utc),
            }
            self.store.case_events.append(row)
            self.rowcount = 1
            return

        if sql.startswith("select * from procurement_case_events"):
            case_id = str(values[0])
            rows = [row for row in self.store.case_events if row["case_id"] == case_id]
            rows.sort(key=lambda row: row["version"])
            self._set_rows(rows, list(rows[0]) if rows else ["id", "case_id", "actor_id", "event_type", "from_status", "to_status", "payload", "request_id", "version", "created_at"])
            return

        if sql.startswith("update procurement_cases"):
            set_clause = sql.split(" set ", 1)[1].split(" where ", 1)[0]
            case_id, expected_version = values[-2:]
            row = self.store.cases.get(str(case_id))
            if row is None or int(row["version"]) != int(expected_version):
                self._set_rows([], list(row) if row else list(self._case_columns()))
                return
            fields = [term.split("=", 1)[0].strip() for term in set_clause.split(",") if "%s" in term]
            for field, value in zip(fields, values[:-2], strict=True):
                row[field] = value
            row["version"] = int(row["version"]) + 1
            row["updated_at"] = datetime.now(timezone.utc)
            self._set_rows([row], list(row))
            return

        if sql.startswith("insert into procurement_case_tasks"):
            link_id, case_id, task_id, actor_id = values
            if not any(link["case_id"] == str(case_id) and link["task_id"] == str(task_id) for link in self.store.case_tasks):
                self.store.case_tasks.append({
                    "id": str(link_id), "case_id": str(case_id), "task_id": str(task_id),
                    "linked_by": str(actor_id), "created_at": datetime.now(timezone.utc),
                })
                self.rowcount = 1
            return

        if "from procurement_case_tasks as link join monitor_review_tasks" in sql:
            case_id = str(values[0])
            rows = []
            for link in self.store.case_tasks:
                task = self.store.tasks.get(link["task_id"])
                if link["case_id"] == case_id and task:
                    rows.append({**link, **{key: task.get(key) for key in (
                        "task_type", "status", "version", "result", "evidence_refs", "updated_at",
                    )}})
            self._set_rows(rows, ["id", "case_id", "task_id", "linked_by", "created_at", "task_type", "status", "version", "result", "evidence_refs", "updated_at"])
            return

        if "from procurement_cases as cases join procurement_case_tasks as links" in sql:
            task_id = str(values[0])
            rows = [
                self.store.cases[link["case_id"]]
                for link in self.store.case_tasks
                if link["task_id"] == task_id and link["case_id"] in self.store.cases
            ]
            self._set_rows(rows, list(rows[0]) if rows else list(self._case_columns()))
            return

        if sql.startswith("select pg_advisory_xact_lock"):
            self._set_rows([], [])
            return

        if sql.startswith("select id from monitor_review_tasks"):
            target_id, task_type = values[:2]
            rows = [
                {"id": task["id"]} for task in self.store.tasks.values()
                if task["monitor_target_id"] == str(target_id)
                and task["task_type"] == str(task_type)
                and task["status"] in {"pending_approval", "approved", "executing"}
            ]
            self._set_rows(rows[:1], ["id"])
            return

        if sql.startswith("insert into monitor_review_tasks"):
            task_id, target_id, task_type, requested_by, payload = values
            now = datetime.now(timezone.utc)
            row = {
                "id": str(task_id), "monitor_target_id": str(target_id), "task_type": str(task_type),
                "status": "pending_approval", "requested_by": str(requested_by),
                "payload": payload or {}, "version": 1, "result": None, "evidence_refs": [],
                "error_code": None, "approved_by": None, "approval_comment": None,
                "approved_at": None, "started_at": None, "completed_at": None,
                "created_at": now, "updated_at": now,
            }
            self.store.tasks[row["id"]] = row
            self.rowcount = 1
            return

        if sql.startswith("insert into monitor_review_task_events"):
            event_id, task_id, actor_id, event_type, from_status, to_status, payload = values
            self.store.task_events.append({
                "id": str(event_id), "task_id": str(task_id), "actor_id": actor_id,
                "event_type": str(event_type), "from_status": from_status, "to_status": to_status,
                "payload": payload or {}, "created_at": datetime.now(timezone.utc),
            })
            self.rowcount = 1
            return

        if sql.startswith("select * from monitor_review_tasks where id = %s"):
            row = self.store.tasks.get(str(values[0]))
            self._set_rows([row] if row else [], list(row) if row else list(self._task_columns()))
            return

        if sql.startswith("select id, actor_id, event_type, from_status, to_status, payload, created_at from monitor_review_task_events"):
            task_id = str(values[0])
            rows = [row for row in self.store.task_events if row["task_id"] == task_id]
            self._set_rows(rows, ["id", "actor_id", "event_type", "from_status", "to_status", "payload", "created_at"])
            return

        if sql.startswith("update monitor_review_tasks"):
            task_id = str(values[-1])
            row = self.store.tasks.get(task_id)
            if row is None:
                return
            if "set status = 'executing'" in sql:
                row["status"] = "executing"
                row["version"] = int(row["version"]) + 1
                row["started_at"] = datetime.now(timezone.utc)
                row["updated_at"] = datetime.now(timezone.utc)
                self.rowcount = 1
                return
            set_clause = sql.split(" set ", 1)[1].split(" where ", 1)[0]
            fields = [term.split("=", 1)[0].strip() for term in set_clause.split(",") if "%s" in term]
            for field, value in zip(fields, values[:-1], strict=True):
                row[field] = value
            if "version = version + 1" in set_clause:
                row["version"] = int(row["version"]) + 1
            row["updated_at"] = datetime.now(timezone.utc)
            self.rowcount = 1
            return

        if sql.startswith("insert into monitor_review_task_evidence"):
            evidence_row_id, task_id, target_id, evidence_id, evidence = values
            self.store.task_evidence.append({
                "id": str(evidence_row_id), "task_id": str(task_id), "monitor_target_id": str(target_id),
                "evidence_id": str(evidence_id), "evidence": evidence,
            })
            self.rowcount = 1
            return

        if sql.startswith("select id, monitor_target_id, evidence_id, evidence, created_at from monitor_review_task_evidence"):
            task_id = str(values[0])
            rows = [row for row in self.store.task_evidence if row["task_id"] == task_id]
            self._set_rows(rows, ["id", "monitor_target_id", "evidence_id", "evidence", "created_at"])
            return

        raise AssertionError(f"Unhandled repository SQL in isolated E2E fixture: {sql}")

    @staticmethod
    def _case_columns() -> tuple[str, ...]:
        return (
            "id", "monitor_target_id", "company_id", "department_code", "case_type", "scope_key",
            "trigger_key", "title", "priority", "status", "created_by", "owner_user_id", "due_at",
            "next_review_at", "version", "reopened_from_case_id", "closure_request", "closed_at",
            "created_at", "updated_at",
        )

    @staticmethod
    def _task_columns() -> tuple[str, ...]:
        return (
            "id", "monitor_target_id", "task_type", "status", "requested_by", "payload", "version",
            "result", "evidence_refs", "error_code", "approved_by", "approval_comment", "approved_at",
            "started_at", "completed_at", "created_at", "updated_at",
        )

    def fetchone(self) -> tuple[Any, ...] | None:
        return self._rows.pop(0) if self._rows else None

    def fetchall(self) -> list[tuple[Any, ...]]:
        rows = self._rows
        self._rows = []
        return rows


@pytest.fixture
def procurement_case_e2e(monkeypatch: pytest.MonkeyPatch) -> tuple[TestClient, _CaseStore, dict[str, Any]]:
    store = _CaseStore()
    current = {"user": None}
    target = {
        "monitor_target_id": "monitor-e2e-1",
        "target_type": "formal_supplier",
        "identity_status": "verified",
        "supplier_id": "supplier-e2e-1",
        "company_id": "company-e2e-1",
        "company_name": "华东设备有限公司",
        "display_name": "华东设备有限公司",
        "data_coverage": {"status": "unknown", "missing_dimensions": ["transaction"]},
    }
    review_target = dict(target)

    @contextmanager
    def cursor_scope():
        snapshot = store.snapshot()
        cursor = _MemoryCursor(store)
        try:
            yield object(), cursor
        except Exception:
            store.restore(snapshot)
            raise

    monkeypatch.setattr(case_repo, "get_cursor", cursor_scope)
    monkeypatch.setattr(case_service, "get_cursor", cursor_scope)
    monkeypatch.setattr(review_tasks, "get_cursor", cursor_scope)
    monkeypatch.setattr(case_access, "get_case_target", lambda _target_id: dict(target))
    monkeypatch.setattr(case_access, "_target_for_case", lambda _target_id: dict(target))
    monkeypatch.setattr(
        case_access,
        "resolve_case_scope",
        lambda target_row, user_id, _role, department: {
            "target_id": target_row["monitor_target_id"],
            "department_code": department or "D1",
            "is_manager": str(user_id) == "manager-1",
        },
    )
    monkeypatch.setattr(
        case_access,
        "current_manager_departments",
        lambda user_id: ["D1"] if str(user_id) == "manager-1" else [],
    )
    monkeypatch.setattr(review_tasks, "_target", lambda _target_id: dict(target))
    monkeypatch.setattr(
        review_tasks,
        "_authorize_target_access",
        lambda _target_id, _user_id, _role, target=None: dict(target or review_target),
    )
    evidence_reads: list[str] = []
    monkeypatch.setattr(
        case_service,
        "_current_evidence_snapshot",
        lambda _case, user_id, _role: evidence_reads.append(str(user_id)) or {
            "evidence_ids": set(),
            "coverage_status": "unknown",
            "missing_dimensions": ["transaction"],
            "missing_dimension_keys": ["transaction"],
            "evidence_by_dimension": {},
            "observed_evidence_by_dimension": {},
            "evidence_collected_at": {},
        },
    )
    monkeypatch.setattr(
        __import__("app.domains.alert.service", fromlist=["get_watchlist_target_summaries"]),
        "get_watchlist_target_summaries",
        lambda _user_id, _role: [{"monitor_target_id": target["monitor_target_id"], "data_coverage": target["data_coverage"]}],
    )

    app = FastAPI()
    app.include_router(api_cases.router, prefix="/api/v1")
    app.dependency_overrides[get_current_user] = lambda: current["user"]
    client = TestClient(app)

    def use_actor(user_id: str, role: UserRole = UserRole.PURCHASER) -> None:
        current["user"] = UserInDB(
            id=user_id,
            username=user_id,
            email=f"{user_id}@example.test",
            role=role,
            password_hash="unused",
            created_at=datetime.now(timezone.utc),
            is_active=True,
        )

    return client, store, {"target": target, "use_actor": use_actor, "evidence_reads": evidence_reads}


def test_procurement_case_closure_keeps_unknown_risk_after_review_needs_human(
    procurement_case_e2e: tuple[TestClient, _CaseStore, dict[str, Any]],
) -> None:
    client, store, context = procurement_case_e2e
    use_actor = context["use_actor"]
    use_actor("buyer-1")

    created = client.post(
        "/api/v1/procurement-cases",
        headers={"Idempotency-Key": "case-create-e2e-1"},
        json={
            "monitor_target_id": "monitor-e2e-1",
            "case_type": "coverage_gap",
            "title": "复核华东设备有限公司资料缺口",
            "priority": "high",
        },
    )
    assert created.status_code == 201
    case_id = created.json()["case"]["id"]
    assert created.json()["case"]["status"] == "open"
    created_event = next(
        event for event in store.case_events if event["case_id"] == case_id
    )
    assert created_event["payload"]["closure_baseline"]["missing_dimension_keys"] == [
        "transaction"
    ]
    assert context["evidence_reads"] == ["buyer-1"]

    use_actor("outsider-1")
    unauthorized = client.post(
        f"/api/v1/procurement-cases/{case_id}/commands",
        headers={"Idempotency-Key": "unauthorized-close-e2e"},
        json={
            "command": "request_closure",
            "expected_version": 1,
            "category": "accepted_limitation",
            "summary": "越权关闭尝试",
            "receipt_ids": ["missing-receipt"],
            "residual_limitations": "未核实",
        },
    )
    assert unauthorized.status_code == 403
    assert context["evidence_reads"] == ["buyer-1"]
    assert ("outsider-1", "unauthorized-close-e2e") not in store.case_requests

    use_actor("buyer-1")
    claim_command = {"command": "claim", "expected_version": 1}
    claimed = client.post(
        f"/api/v1/procurement-cases/{case_id}/commands",
        headers={"Idempotency-Key": "claim-e2e-1"},
        json=claim_command,
    )
    replayed_claim = client.post(
        f"/api/v1/procurement-cases/{case_id}/commands",
        headers={"Idempotency-Key": "claim-e2e-1"},
        json=claim_command,
    )
    assert claimed.status_code == replayed_claim.status_code == 200
    assert claimed.json()["event_id"] == replayed_claim.json()["event_id"]
    assert sum(event["event_type"] == "claimed" for event in store.case_events) == 1

    linked = client.post(
        f"/api/v1/procurement-cases/{case_id}/review-tasks",
        headers={"Idempotency-Key": "review-task-create-e2e-1"},
        json={"task_type": "supplement_data", "payload": {}},
    )
    assert linked.status_code == 200
    task = linked.json()["task"]
    assert task["status"] == "pending_approval"

    approved_task = review_tasks.decide_review_task(
        task["id"], 1, "approved", "批准本轮数据覆盖核验", "buyer-1", "purchaser"
    )
    assert approved_task["status"] == "approved"
    executed_task = review_tasks.execute_review_task(task["id"], 2, "buyer-1", "purchaser")
    assert executed_task["status"] == "needs_review"
    assert executed_task["result"]["status"] == "needs_review"
    assert executed_task["evidence_refs"]

    receipt = client.post(
        f"/api/v1/procurement-cases/{case_id}/commands",
        headers={"Idempotency-Key": "receipt-e2e-1"},
        json={
            "command": "receipt",
            "expected_version": 3,
            "summary": "本轮复核仍无法确认内部交易覆盖，保留人工处理记录。",
            "refs": executed_task["evidence_refs"],
        },
    )
    assert receipt.status_code == 200

    requested = client.post(
        f"/api/v1/procurement-cases/{case_id}/commands",
        headers={"Idempotency-Key": "closure-request-e2e-1"},
        json={
            "command": "request_closure",
            "expected_version": 4,
            "category": "accepted_limitation",
            "summary": "已完成现有资料核验，按当前限制关闭采购跟进事项。",
            "receipt_ids": [receipt.json()["event_id"]],
            "residual_limitations": "内部交易数据仍未覆盖；风险结论继续保持未知。",
        },
    )
    assert requested.status_code == 200
    assert requested.json()["case"]["status"] == "pending_closure"
    assert context["evidence_reads"] == ["buyer-1", "buyer-1"]

    use_actor("manager-1")
    decided = client.post(
        f"/api/v1/procurement-cases/{case_id}/commands",
        headers={"Idempotency-Key": "closure-approval-e2e-1"},
        json={
            "command": "approve_closure",
            "expected_version": 5,
            "reason": "接受剩余资料限制，风险保持未知。",
        },
    )
    assert decided.status_code == 200
    assert decided.json()["case"]["status"] == "closed"

    use_actor("buyer-1")
    final_detail = client.get(f"/api/v1/procurement-cases/{case_id}")
    assert final_detail.status_code == 200
    body = final_detail.json()
    assert body["case"]["closure_request"]["category"] == "accepted_limitation"
    assert body["case"]["closure_request"]["residual_limitations"] == "内部交易数据仍未覆盖；风险结论继续保持未知。"
    assert body["linked_tasks"][0]["status"] == "needs_review"
    assert body["linked_tasks"][0]["result"]["status"] == "needs_review"
    assert context["target"]["data_coverage"]["status"] == "unknown"
    assert sum(event["event_type"] == "closure_approved" for event in store.case_events) == 1
