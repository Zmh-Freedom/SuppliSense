"""PostgreSQL persistence boundary for authorized sourcing handoffs."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from psycopg2.extras import Json

from app.db.postgres import PgCursor, get_cursor


class SourcingHandoffConflict(RuntimeError):
    """The source changed or an idempotency key was reused with new input."""


def _row_to_dict(cur: PgCursor, row: tuple[Any, ...] | None) -> dict[str, Any] | None:
    if row is None:
        return None
    result = dict(zip((column[0] for column in cur.description), row, strict=True))
    for key, value in result.items():
        if isinstance(value, uuid.UUID):
            result[key] = str(value)
    return result


def _json_object(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _handoff_aliases(row: dict[str, Any] | None) -> dict[str, Any] | None:
    if row is None:
        return None
    result = dict(row)
    result.setdefault("handoff_id", str(result.get("id") or ""))
    result.setdefault("user_id", str(result.get("actor_id") or ""))
    result.setdefault("candidate_key", str(result.get("candidate_id") or ""))
    result.setdefault("candidate", _json_object(result.get("candidate_snapshot")))
    result.setdefault("requirement", _json_object(result.get("requirement_snapshot")))
    result["source_policy"] = _json_object(result.get("source_policy"))
    return result


def _read_handoff_source(
    cur: PgCursor,
    run_id: str,
    candidate_key: str,
    user_id: str,
    *,
    lock: bool = False,
) -> dict[str, Any] | None:
    lock_clause = " FOR UPDATE" if lock else ""
    cur.execute(
        """SELECT id, run_type, user_id, status, version, requirement
           FROM agent_runs WHERE id=%s AND user_id=%s""" + lock_clause,
        (run_id, user_id),
    )
    run = _row_to_dict(cur, cur.fetchone())
    if run is None:
        return None
    run["requirement"] = _json_object(run.get("requirement"))
    cur.execute(
        "SELECT policy FROM sourcing_policy_snapshots WHERE run_id=%s",
        (run_id,),
    )
    policy_row = _row_to_dict(cur, cur.fetchone())
    run["policy"] = _json_object((policy_row or {}).get("policy"))

    cur.execute(
        """SELECT candidate.id, candidate.company_id, candidate.source, candidate.status,
                  candidate.candidate_snapshot, disposition.action AS disposition_action,
                  disposition.company_id AS disposition_company_id
           FROM agent_run_candidates AS candidate
           LEFT JOIN sourcing_candidate_dispositions AS disposition
             ON disposition.run_id=candidate.run_id AND disposition.candidate_id=candidate.id
           WHERE candidate.run_id=%s AND candidate.id=%s""" + (" FOR UPDATE OF candidate" if lock else ""),
        (run_id, candidate_key),
    )
    candidate_row = _row_to_dict(cur, cur.fetchone())
    if candidate_row is None:
        return None
    candidate = _json_object(candidate_row.pop("candidate_snapshot", None))
    candidate.update({
        "id": str(candidate_row["id"]),
        "candidate_key": str(candidate_row["id"]),
        "candidate_id": str(candidate_row["id"]),
        "company_id": str(candidate_row["company_id"]) if candidate_row.get("company_id") else None,
        "source": candidate_row.get("source"),
        "status": candidate_row.get("status"),
    })
    disposition = {
        "action": candidate_row.get("disposition_action"),
        "company_id": str(candidate_row["disposition_company_id"])
        if candidate_row.get("disposition_company_id") else None,
    }

    cur.execute(
        """SELECT id, evidence_type, source, source_reference, evidence_snapshot, created_at
           FROM agent_evidence WHERE run_id=%s AND candidate_id=%s ORDER BY created_at, id""",
        (run_id, candidate_key),
    )
    evidence_refs = []
    for row in cur.fetchall():
        evidence = _row_to_dict(cur, row) or {}
        snapshot = _json_object(evidence.pop("evidence_snapshot", None))
        reference = {
            "evidence_id": str(evidence.get("id") or snapshot.get("evidence_id") or ""),
            "dimension": snapshot.get("dimension") or evidence.get("evidence_type"),
            "source_type": snapshot.get("source_type") or evidence.get("source"),
            "source_reference": snapshot.get("source_reference") or evidence.get("source_reference"),
            "freshness_status": snapshot.get("freshness_status") or "unknown",
        }
        for field in ("observed_at", "collected_at", "claim_code"):
            value = snapshot.get(field)
            if value is None and field == "collected_at":
                value = evidence.get("created_at")
            if isinstance(value, datetime):
                value = value.isoformat()
            if value is not None:
                reference[field] = value
        evidence_refs.append(reference)

    return {"run": run, "candidate": candidate, "disposition": disposition, "evidence_refs": evidence_refs}


def read_handoff_source(
    run_id: str, candidate_key: str, user_id: str, user_role: str,
) -> dict[str, Any] | None:
    del user_role
    with get_cursor() as (_, cur):
        return _read_handoff_source(cur, run_id, candidate_key, user_id)


def get_handoff_for_request(user_id: str, request_key: str) -> dict[str, Any] | None:
    with get_cursor() as (_, cur):
        cur.execute(
            "SELECT * FROM sourcing_handoffs WHERE actor_id=%s AND request_key=%s",
            (user_id, request_key),
        )
        return _row_to_dict(cur, cur.fetchone())


def create_handoff_transaction(
    source: dict[str, Any], payload: dict[str, Any], user_id: str, user_role: str,
    request_key: str, request_hash: str,
) -> dict[str, Any]:
    del user_role
    run_id = str(payload["run_id"])
    candidate_key = str(payload["candidate_key"])
    expected_version = int(payload["expected_version"])
    with get_cursor() as (_, cur):
        cur.execute(
            "SELECT pg_advisory_xact_lock(hashtext(%s))",
            (f"sourcing-handoff:{user_id}:{request_key}",),
        )
        cur.execute(
            "SELECT * FROM sourcing_handoffs WHERE actor_id=%s AND request_key=%s FOR UPDATE",
            (user_id, request_key),
        )
        existing = _row_to_dict(cur, cur.fetchone())
        if existing is not None:
            if str(existing.get("request_hash") or "") != request_hash:
                raise SourcingHandoffConflict("交接幂等键已用于其他请求")
            return existing

        locked_source = _read_handoff_source(cur, run_id, candidate_key, user_id, lock=True)
        if locked_source is None:
            raise SourcingHandoffConflict("寻源任务或候选已不可用")
        locked_run = locked_source["run"]
        if (
            str(locked_run.get("run_type") or "") != "sourcing_risk_v2"
            or int(locked_run.get("version") or 0) != expected_version
            or int(source.get("run", {}).get("version") or 0) != expected_version
        ):
            raise SourcingHandoffConflict("寻源任务已更新，请刷新后重新交接")

        from app.domains.agent_run.state_store import session_state_store

        handoff_id = str(uuid.uuid4())
        session_id = str(uuid.uuid4())
        session_state_store.create_session_with_cursor(user_id, cur, session_id=session_id)
        candidate = dict(locked_source.get("candidate") or {})
        name = str(candidate.get("supplier_name") or candidate.get("name") or "待确认候选")
        intent = str(payload.get("intent") or "risk_review")
        continue_message = (
            f"请核验候选供应商“{name}”的主体，并继续进行风险检查"
            if intent == "risk_review"
            else f"请将候选供应商“{name}”加入风险监控清单，先完成主体核验并在写入前请求审批"
        )
        requirement = dict(locked_run.get("requirement") or {})
        candidate_snapshot = {
            key: value for key, value in candidate.items()
            if key in {
                "candidate_key", "candidate_id", "company_id", "supplier_name", "name", "source",
                "status", "identity_status", "identity_candidates", "verification_status", "categories",
            } and value is not None
        }
        cur.execute(
            """INSERT INTO sourcing_handoffs (
                   id, actor_id, session_id, source_run_id, source_run_version,
                   candidate_id, company_id, candidate_snapshot, requirement_snapshot,
                   evidence_refs, intent, status, request_key, request_hash, continue_message
               ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'ready',%s,%s,%s)
               RETURNING *""",
            (
                handoff_id, user_id, session_id, run_id, expected_version,
                candidate_key, candidate.get("company_id"), Json(candidate_snapshot),
                Json(requirement), Json(locked_source.get("evidence_refs") or []), intent,
                request_key, request_hash, continue_message,
            ),
        )
        binding = _row_to_dict(cur, cur.fetchone())
    if binding is None:
        raise RuntimeError("寻源交接创建失败")
    return binding


def get_handoff_for_user(handoff_id: str, user_id: str) -> dict[str, Any] | None:
    with get_cursor() as (_, cur):
        cur.execute(
            """SELECT handoff.*, policy.policy AS source_policy
               FROM sourcing_handoffs AS handoff
               LEFT JOIN sourcing_policy_snapshots AS policy ON policy.run_id=handoff.source_run_id
               WHERE handoff.id=%s AND handoff.actor_id=%s""",
            (handoff_id, user_id),
        )
        return _handoff_aliases(_row_to_dict(cur, cur.fetchone()))


def get_handoff_by_session(session_id: str) -> dict[str, Any] | None:
    with get_cursor() as (_, cur):
        cur.execute(
            """SELECT handoff.*, policy.policy AS source_policy
               FROM sourcing_handoffs AS handoff
               LEFT JOIN sourcing_policy_snapshots AS policy ON policy.run_id=handoff.source_run_id
               WHERE handoff.session_id=%s""",
            (session_id,),
        )
        return _handoff_aliases(_row_to_dict(cur, cur.fetchone()))
