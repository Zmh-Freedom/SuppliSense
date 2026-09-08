"""PostgreSQL persistence primitives for sourcing-risk agent runs."""

import hashlib
import json
import uuid
from typing import Any

from psycopg2.extras import Json

from app.db.postgres import PgCursor, get_cursor


def get_rollout_control_state() -> dict[str, str]:
    with get_cursor() as (_, cur):
        cur.execute("SELECT state, stage FROM agent_rollout_control WHERE control_key = 'agent_run_v2'")
        row = cur.fetchone()
    if row is None:
        raise RuntimeError("agent rollout control state is unavailable")
    return {"state": str(row[0]), "stage": str(row[1])}


def freeze_in_flight_v2_runs() -> int:
    """Safely stop non-terminal V2 runs while retaining checkpoints and audit."""
    with get_cursor() as (_, cur):
        cur.execute(
            """
            UPDATE agent_runs
            SET status = 'ROLLBACK_FROZEN', error_code = 'AGENT_RUN_ROLLBACK_FROZEN', updated_at = NOW(), completed_at = NOW()
            WHERE run_type = 'sourcing_risk_v2'
              AND status NOT IN ('COMPLETED', 'PARTIAL', 'NEEDS_REVIEW', 'ACTION_FAILED', 'FAILED', 'CANCELLED')
            RETURNING id
            """
        )
        return len(cur.fetchall())


def freeze_pending_v2_proposals() -> int:
    """Freeze pending proposals without deleting their approval/audit history."""
    with get_cursor() as (_, cur):
        cur.execute(
            """
            UPDATE agent_action_proposals
            SET status = 'frozen', execution_state = 'frozen', updated_at = NOW()
            FROM agent_runs
            WHERE agent_action_proposals.run_id = agent_runs.id
              AND agent_runs.run_type = 'sourcing_risk_v2'
              AND agent_action_proposals.status = 'pending'
              AND agent_action_proposals.execution_state = 'pending'
            RETURNING agent_action_proposals.id
            """
        )
        return len(cur.fetchall())


def set_rollout_control_state(state: str, stage: str | None = None) -> dict[str, str]:
    if state not in {"active", "rollback_frozen"}:
        raise ValueError("invalid rollout control state")
    with get_cursor() as (_, cur):
        cur.execute(
            """
            INSERT INTO agent_rollout_control (control_key, state, stage)
            VALUES ('agent_run_v2', %s, COALESCE(%s, 'shadow'))
            ON CONFLICT (control_key) DO UPDATE SET state = EXCLUDED.state,
                stage = COALESCE(EXCLUDED.stage, agent_rollout_control.stage), updated_at = NOW()
            RETURNING state, stage
            """,
            (state, stage),
        )
        row = cur.fetchone()
    return {"state": str(row[0]), "stage": str(row[1])}


def _row_to_dict(cur: PgCursor, row: tuple[Any, ...] | None) -> dict[str, Any] | None:
    if row is None:
        return None
    result = dict(zip((column[0] for column in cur.description), row))
    for key, value in result.items():
        if isinstance(value, uuid.UUID):
            result[key] = str(value)
    return result


def insert_run(
    run_type: str,
    requirement: dict[str, Any],
    user_id: str | None = None,
    status: str = "CREATED",
    cur: PgCursor | None = None,
    session_id: str | None = None,
) -> dict[str, Any]:
    run_id = str(uuid.uuid4())
    if cur is not None:
        return _insert_run_with_cursor(cur, run_id, run_type, requirement, user_id, status, session_id)
    with get_cursor() as (_, cur):
        return _insert_run_with_cursor(cur, run_id, run_type, requirement, user_id, status, session_id)


def _insert_run_with_cursor(
    cur: PgCursor,
    run_id: str,
    run_type: str,
    requirement: dict[str, Any],
    user_id: str | None,
    status: str,
    session_id: str | None,
) -> dict[str, Any]:
    cur.execute(
        """
        INSERT INTO agent_runs (id, session_id, run_type, user_id, status, requirement)
        VALUES (%s, %s, %s, %s, %s, %s)
        RETURNING *
        """,
        (run_id, session_id, run_type, user_id, status, Json(requirement)),
    )
    return _row_to_dict(cur, cur.fetchone())  # type: ignore[return-value]


def get_run_for_user(run_id: str, user_id: str) -> dict[str, Any] | None:
    with get_cursor() as (_, cur):
        cur.execute(
            "SELECT * FROM agent_runs WHERE id = %s AND user_id = %s",
            (run_id, user_id),
        )
        return _row_to_dict(cur, cur.fetchone())


def get_run(run_id: str) -> dict[str, Any] | None:
    """Return a Run for a separately-authorized administrative read."""
    with get_cursor() as (_, cur):
        cur.execute("SELECT * FROM agent_runs WHERE id = %s", (run_id,))
        return _row_to_dict(cur, cur.fetchone())


def get_harness_artifact_counts(run_id: str) -> dict[str, int]:
    """Count the relational Harness projections for one persisted Run."""
    with get_cursor() as (_, cur):
        cur.execute(
            """
            SELECT
                (SELECT COUNT(*) FROM agent_tasks WHERE run_id = %s) AS task_count,
                (SELECT COUNT(*) FROM agent_tool_calls WHERE run_id = %s) AS tool_call_count,
                (SELECT COUNT(*) FROM agent_tool_calls
                 WHERE run_id = %s AND status NOT IN (
                     'success', 'partial', 'not_found', 'invalid',
                     'denied', 'failed', 'unavailable'
                 )) AS active_tool_call_count
            """,
            (run_id, run_id, run_id),
        )
        row = cur.fetchone()
    if row is None:
        return {"task_count": 0, "tool_call_count": 0, "active_tool_call_count": 0}
    return {
        "task_count": int(row[0] or 0),
        "tool_call_count": int(row[1] or 0),
        "active_tool_call_count": int(row[2] or 0),
    }


def get_run_for_update(run_id: str, cur: PgCursor) -> dict[str, Any] | None:
    """Lock one Run inside the caller-owned lifecycle transaction."""
    cur.execute("SELECT * FROM agent_runs WHERE id = %s FOR UPDATE", (run_id,))
    return _row_to_dict(cur, cur.fetchone())


def get_run_detail_collections(run_id: str) -> dict[str, Any]:
    """Load persisted workbench data for an already-authorized Run."""
    with get_cursor() as (_, cur):
        candidates = _list_rows(
            cur,
            "SELECT * FROM agent_run_candidates WHERE run_id = %s ORDER BY created_at ASC",
            (run_id,),
        )
        evidence = _list_rows(
            cur,
            "SELECT * FROM agent_evidence WHERE run_id = %s ORDER BY created_at ASC",
            (run_id,),
        )
        evidence_reviews = _list_rows(
            cur,
            "SELECT * FROM agent_evidence_reviews WHERE run_id = %s ORDER BY company_id ASC",
            (run_id,),
        )
        decisions = _list_rows(
            cur,
            "SELECT * FROM candidate_decisions WHERE run_id = %s ORDER BY created_at ASC",
            (run_id,),
        )
        proposals = _list_rows(
            cur,
            "SELECT * FROM agent_action_proposals WHERE run_id = %s ORDER BY created_at ASC",
            (run_id,),
        )
        approvals = _list_rows(
            cur,
            "SELECT * FROM agent_approval_decisions WHERE run_id = %s ORDER BY created_at ASC",
            (run_id,),
        )
    return {
        "candidates": [_candidate_detail(candidate) for candidate in candidates],
        "evidence_by_company_id": _evidence_by_company(evidence),
        "evidence_reviews": _evidence_reviews_by_company(evidence_reviews),
        "decisions": [_decision_detail(decision) for decision in decisions],
        "action_proposals": [_proposal_detail(proposal) for proposal in proposals],
        "approvals": approvals,
    }


def upsert_raw_payload_compensations(
    compensations: list[dict[str, Any]], cur: PgCursor | None = None
) -> None:
    """Persist cross-store recovery metadata independently from a failed snapshot."""
    if not compensations:
        return
    if cur is not None:
        _upsert_raw_payload_compensations_with_cursor(cur, compensations)
        return
    with get_cursor() as (_, cursor):
        _upsert_raw_payload_compensations_with_cursor(cursor, compensations)


def _upsert_raw_payload_compensations_with_cursor(
    cur: PgCursor, compensations: list[dict[str, Any]]
) -> None:
    for compensation in compensations:
        cur.execute(
            """
            INSERT INTO agent_raw_payload_compensations
                (run_id, raw_payload_ref, company_id, staging_owner, status, last_error)
            VALUES (%s, %s, %s, %s, 'pending_compensation', %s)
            ON CONFLICT (run_id, raw_payload_ref) DO UPDATE SET
                status = CASE
                    WHEN agent_raw_payload_compensations.status = 'compensated'
                    THEN agent_raw_payload_compensations.status
                    ELSE 'pending_compensation'
                END,
                last_error = EXCLUDED.last_error,
                staging_owner = EXCLUDED.staging_owner,
                updated_at = NOW()
            """,
            (
                compensation["run_id"],
                compensation["raw_payload_ref"],
                compensation.get("company_id"),
                compensation["staging_owner"],
                compensation.get("last_error"),
            ),
        )


def list_raw_payload_compensations(run_id: str) -> list[dict[str, Any]]:
    with get_cursor() as (_, cur):
        cur.execute(
            """
            SELECT run_id, raw_payload_ref, company_id, staging_owner, status, attempt_count, last_error
            FROM agent_raw_payload_compensations
            WHERE run_id = %s
            ORDER BY created_at ASC
            """,
            (run_id,),
        )
        columns = [column[0] for column in cur.description]
        return [_row_to_dict_from_columns(columns, row) for row in cur.fetchall()]


def update_raw_payload_compensation(
    run_id: str, raw_payload_ref: str, status: str, last_error: str | None = None
) -> None:
    with get_cursor() as (_, cur):
        cur.execute(
            """
            UPDATE agent_raw_payload_compensations
            SET status = CASE
                    WHEN status = 'compensated' THEN status
                    ELSE %s
                END,
                attempt_count = attempt_count + 1,
                last_error = %s,
                updated_at = NOW()
            WHERE run_id = %s AND raw_payload_ref = %s
            """,
            (status, last_error, run_id, raw_payload_ref),
        )


def update_run_status(
    run_id: str,
    expected_version: int,
    status: str,
    error_code: str | None = None,
    cur: PgCursor | None = None,
) -> dict[str, Any] | None:
    if cur is not None:
        return update_run_status_with_cursor(
            cur, run_id, expected_version, status, error_code
        )
    with get_cursor() as (_, cur):
        return update_run_status_with_cursor(
            cur, run_id, expected_version, status, error_code
        )


def update_run_requirement(
    run_id: str,
    expected_version: int,
    requirement: dict[str, Any],
    status: str,
    cur: PgCursor | None = None,
) -> dict[str, Any] | None:
    """Atomically replace runner-consumable requirement input while advancing a run version."""
    if cur is not None:
        return update_run_requirement_with_cursor(cur, run_id, expected_version, requirement, status)
    with get_cursor() as (_, cursor):
        return update_run_requirement_with_cursor(cursor, run_id, expected_version, requirement, status)


def update_run_requirement_with_cursor(
    cur: PgCursor,
    run_id: str,
    expected_version: int,
    requirement: dict[str, Any],
    status: str,
) -> dict[str, Any] | None:
    cur.execute(
        """
        UPDATE agent_runs
        SET requirement = %s,
            status = %s,
            version = version + 1,
            updated_at = NOW()
        WHERE id = %s AND version = %s
        RETURNING *
        """,
        (Json(requirement), status, run_id, expected_version),
    )
    return _row_to_dict(cur, cur.fetchone())


def update_run_status_with_cursor(
    cur: PgCursor,
    run_id: str,
    expected_version: int,
    status: str,
    error_code: str | None = None,
) -> dict[str, Any] | None:
    cur.execute(
        """
        UPDATE agent_runs
        SET status = %s,
            error_code = %s,
            version = version + 1,
            updated_at = NOW(),
            completed_at = CASE
                WHEN %s IN ('COMPLETED', 'PARTIAL', 'FAILED', 'CANCELLED', 'ACTION_FAILED')
                THEN NOW() ELSE completed_at END
        WHERE id = %s AND version = %s
        RETURNING *
        """,
        (status, error_code, status, run_id, expected_version),
    )
    return _row_to_dict(cur, cur.fetchone())


def append_event(
    run_id: str,
    version: int,
    event_type: str,
    payload: dict[str, Any],
    cur: PgCursor | None = None,
) -> dict[str, Any]:
    if cur is not None:
        return append_event_with_cursor(cur, run_id, version, event_type, payload)
    with get_cursor() as (_, cur):
        return append_event_with_cursor(cur, run_id, version, event_type, payload)


def append_event_with_cursor(
    cur: PgCursor,
    run_id: str,
    version: int,
    event_type: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    cur.execute("SELECT id FROM agent_runs WHERE id = %s FOR UPDATE", (run_id,))
    if cur.fetchone() is None:
        raise ValueError("agent run 不存在")
    cur.execute(
        "SELECT COALESCE(MAX(event_id), 0) + 1 FROM agent_run_events WHERE run_id = %s",
        (run_id,),
    )
    event_id = cur.fetchone()[0]
    cur.execute(
        """
        INSERT INTO agent_run_events (run_id, event_id, version, event_type, payload)
        VALUES (%s, %s, %s, %s, %s)
        RETURNING *
        """,
        (run_id, event_id, version, event_type, Json(payload)),
    )
    return _row_to_dict(cur, cur.fetchone())  # type: ignore[return-value]


def list_events_after(run_id: str, event_id: int = 0) -> list[dict[str, Any]]:
    with get_cursor() as (_, cur):
        cur.execute(
            """
            SELECT * FROM agent_run_events
            WHERE run_id = %s AND event_id > %s
            ORDER BY event_id ASC
            """,
            (run_id, event_id),
        )
        columns = [column[0] for column in cur.description]
        return [
            _row_to_dict_from_columns(columns, row)
            for row in cur.fetchall()
        ]


def list_execution_snapshot_events(run_id: str) -> list[dict[str, Any]]:
    """Return only durable events that carry an execution recovery snapshot."""
    with get_cursor() as (_, cur):
        cur.execute(
            """
            SELECT * FROM agent_run_events
            WHERE run_id = %s AND payload ? 'execution_snapshot'
            ORDER BY event_id ASC
            """,
            (run_id,),
        )
        columns = [column[0] for column in cur.description]
        return [_row_to_dict_from_columns(columns, row) for row in cur.fetchall()]


def insert_policy_snapshot(
    run_id: str, policy: dict[str, Any], template_id: str | None = None
) -> dict[str, Any]:
    return _insert_returning(
        "sourcing_policy_snapshots",
        {"id": str(uuid.uuid4()), "run_id": run_id, "template_id": template_id, "policy": policy},
        {"policy"},
    )


def insert_candidate(
    run_id: str, source: str, status: str, candidate_snapshot: dict[str, Any], company_id: str | None = None,
    cur: PgCursor | None = None,
) -> dict[str, Any]:
    if cur is not None:
        return _insert_candidate_with_cursor(cur, run_id, source, status, candidate_snapshot, company_id)
    return _insert_returning(
        "agent_run_candidates",
        {"id": str(uuid.uuid4()), "run_id": run_id, "company_id": company_id, "source": source, "status": status, "candidate_snapshot": candidate_snapshot},
        {"candidate_snapshot"},
    )


def _insert_candidate_with_cursor(
    cur: PgCursor, run_id: str, source: str, status: str, candidate_snapshot: dict[str, Any], company_id: str | None,
) -> dict[str, Any]:
    values = {
        "id": str(uuid.uuid4()), "run_id": run_id, "company_id": company_id,
        "source": source, "status": status, "candidate_snapshot": candidate_snapshot,
    }
    columns = list(values)
    cur.execute(
        f"INSERT INTO agent_run_candidates ({', '.join(columns)}) VALUES ({', '.join('%s' for _ in columns)}) RETURNING *",
        [Json(values[column]) if column == "candidate_snapshot" else values[column] for column in columns],
    )
    return _row_to_dict(cur, cur.fetchone())  # type: ignore[return-value]


def insert_evidence(
    run_id: str,
    company_id: str,
    evidence_type: str,
    source: str,
    evidence_snapshot: dict[str, Any],
    source_reference: str | None = None,
    cur: PgCursor | None = None,
    monitor_target_id: str | None = None,
) -> dict[str, Any]:
    if cur is not None:
        return _insert_evidence_with_cursor(
            cur, run_id, company_id, evidence_type, source, evidence_snapshot,
            source_reference, monitor_target_id,
        )
    return _insert_returning(
        "agent_evidence",
        {"id": str(uuid.uuid4()), "run_id": run_id, "company_id": company_id,
         "evidence_type": evidence_type, "source": source,
         "source_reference": source_reference, "evidence_snapshot": evidence_snapshot,
         "monitor_target_id": monitor_target_id},
        {"evidence_snapshot"},
    )


def _insert_evidence_with_cursor(
    cur: PgCursor, run_id: str, company_id: str, evidence_type: str, source: str,
    evidence_snapshot: dict[str, Any], source_reference: str | None,
    monitor_target_id: str | None = None,
) -> dict[str, Any]:
    values = {
        "id": str(uuid.uuid4()), "run_id": run_id, "company_id": company_id,
        "evidence_type": evidence_type, "source": source, "source_reference": source_reference,
        "evidence_snapshot": evidence_snapshot,
        "monitor_target_id": monitor_target_id,
    }
    columns = list(values)
    cur.execute(
        f"INSERT INTO agent_evidence ({', '.join(columns)}) VALUES ({', '.join('%s' for _ in columns)}) RETURNING *",
        [Json(values[column]) if column == "evidence_snapshot" else values[column] for column in columns],
    )
    return _row_to_dict(cur, cur.fetchone())  # type: ignore[return-value]


def insert_decision(
    run_id: str, decision: str, score_snapshot: dict[str, Any], reason_snapshot: dict[str, Any], candidate_id: str | None = None,
    cur: PgCursor | None = None,
) -> dict[str, Any]:
    if cur is not None:
        return _insert_decision_with_cursor(cur, run_id, decision, score_snapshot, reason_snapshot, candidate_id)
    return _insert_returning(
        "candidate_decisions",
        {"id": str(uuid.uuid4()), "run_id": run_id, "candidate_id": candidate_id, "decision": decision, "score_snapshot": score_snapshot, "reason_snapshot": reason_snapshot},
        {"score_snapshot", "reason_snapshot"},
    )


def _insert_decision_with_cursor(
    cur: PgCursor, run_id: str, decision: str, score_snapshot: dict[str, Any],
    reason_snapshot: dict[str, Any], candidate_id: str | None,
) -> dict[str, Any]:
    values = {
        "id": str(uuid.uuid4()), "run_id": run_id, "candidate_id": candidate_id,
        "decision": decision, "score_snapshot": score_snapshot, "reason_snapshot": reason_snapshot,
    }
    columns = list(values)
    cur.execute(
        f"INSERT INTO candidate_decisions ({', '.join(columns)}) VALUES ({', '.join('%s' for _ in columns)}) RETURNING *",
        [Json(values[column]) if column in {"score_snapshot", "reason_snapshot"} else values[column] for column in columns],
    )
    return _row_to_dict(cur, cur.fetchone())  # type: ignore[return-value]


def upsert_candidate(
    run_id: str, candidate_key: str, source: str, status: str, candidate_snapshot: dict[str, Any],
    company_id: str | None, cur: PgCursor,
) -> dict[str, Any]:
    """Make graph retries update one immutable-in-scope candidate identity per Run."""
    candidate_id = _stable_id(run_id, f"candidate:{candidate_key}")
    cur.execute(
        """
        INSERT INTO agent_run_candidates (id, run_id, company_id, source, status, candidate_snapshot)
        VALUES (%s, %s, %s, %s, %s, %s)
        ON CONFLICT (id) DO UPDATE SET
            company_id = EXCLUDED.company_id,
            source = EXCLUDED.source,
            status = EXCLUDED.status,
            candidate_snapshot = EXCLUDED.candidate_snapshot,
            updated_at = NOW()
        RETURNING *
        """,
        (candidate_id, run_id, company_id, source, status, Json(candidate_snapshot)),
    )
    return _row_to_dict(cur, cur.fetchone())  # type: ignore[return-value]


def upsert_evidence_review(
    run_id: str, company_id: str, review_snapshot: dict[str, Any], cur: PgCursor,
) -> dict[str, Any]:
    cur.execute(
        """
        INSERT INTO agent_evidence_reviews (run_id, company_id, review_snapshot)
        VALUES (%s, %s, %s)
        ON CONFLICT (run_id, company_id) DO UPDATE SET
            review_snapshot = EXCLUDED.review_snapshot,
            updated_at = NOW()
        RETURNING *
        """,
        (run_id, company_id, Json(review_snapshot)),
    )
    return _row_to_dict(cur, cur.fetchone())  # type: ignore[return-value]


def upsert_decision(
    run_id: str, decision_key: str, decision: str, score_snapshot: dict[str, Any],
    reason_snapshot: dict[str, Any], candidate_id: str | None, cur: PgCursor,
) -> dict[str, Any]:
    decision_id = _stable_id(run_id, f"decision:{decision_key}")
    cur.execute(
        """
        INSERT INTO candidate_decisions (id, run_id, candidate_id, decision, score_snapshot, reason_snapshot)
        VALUES (%s, %s, %s, %s, %s, %s)
        ON CONFLICT (id) DO UPDATE SET
            candidate_id = EXCLUDED.candidate_id,
            decision = EXCLUDED.decision,
            score_snapshot = EXCLUDED.score_snapshot,
            reason_snapshot = EXCLUDED.reason_snapshot
        RETURNING *
        """,
        (decision_id, run_id, candidate_id, decision, Json(score_snapshot), Json(reason_snapshot)),
    )
    return _row_to_dict(cur, cur.fetchone())  # type: ignore[return-value]


def persist_run_snapshot(
    run_id: str,
    *,
    candidates: list[dict[str, Any]] | None = None,
    evidence_by_company_id: dict[str, list[dict[str, Any]]] | None = None,
    evidence_reviews: dict[str, dict[str, Any]] | None = None,
    decisions: list[dict[str, Any]] | None = None,
    cur: PgCursor,
) -> dict[str, Any]:
    """Write graph-owned workbench collections in the Run lifecycle transaction."""
    candidate_ids: dict[str, str] = {}
    persisted_candidates: list[dict[str, Any]] = []
    for candidate in candidates or []:
        company_id = candidate.get("company_id")
        key = str(candidate.get("candidate_key") or company_id or candidate.get("supplier_id") or candidate.get("supplier_name"))
        if not key:
            raise ValueError("候选缺少稳定标识")
        persisted = upsert_candidate(
            run_id, key, _candidate_source(candidate), _candidate_status(candidate), dict(candidate),
            str(company_id) if company_id else None, cur=cur,
        )
        persisted_candidates.append(persisted)
        if company_id:
            candidate_ids[str(company_id)] = persisted["id"]
    for company_id, evidence_items in (evidence_by_company_id or {}).items():
        for evidence in evidence_items:
            _upsert_evidence(
                run_id,
                str(company_id),
                evidence,
                candidate_ids.get(str(company_id)),
                cur,
                monitor_target_id=evidence.get("monitor_target_id"),
            )
    for company_id, review in (evidence_reviews or {}).items():
        upsert_evidence_review(run_id, str(company_id), dict(review), cur=cur)
    for index, decision in enumerate(decisions or []):
        company_id = str(decision.get("company_id") or "")
        candidate_id = str(decision.get("candidate_id") or candidate_ids.get(company_id) or "") or None
        key = candidate_id or company_id or str(index)
        snapshot = dict(decision)
        upsert_decision(
            run_id, key, str(decision.get("group") or "needs_review"), snapshot,
            {"reason_codes": list(decision.get("reason_codes") or []), "evidence_ids": list(decision.get("evidence_ids") or [])},
            candidate_id, cur=cur,
        )
    return {"candidates": persisted_candidates}


def insert_action_proposal(
    run_id: str, action_type: str, payload: dict[str, Any], idempotency_key: str, status: str = "pending", execution_state: str = "pending", candidate_id: str | None = None, cur: PgCursor | None = None,
) -> dict[str, Any] | None:
    if cur is not None:
        return _insert_action_proposal_with_cursor(
            cur, run_id, action_type, payload, idempotency_key, status, execution_state, candidate_id
        )
    return _insert_returning(
        "agent_action_proposals",
        {"id": str(uuid.uuid4()), "run_id": run_id, "candidate_id": candidate_id, "action_type": action_type, "status": status, "execution_state": execution_state, "payload": payload, "idempotency_key": idempotency_key},
        {"payload"},
    )


def _insert_action_proposal_with_cursor(
    cur: PgCursor, run_id: str, action_type: str, payload: dict[str, Any], idempotency_key: str,
    status: str, execution_state: str, candidate_id: str | None,
) -> dict[str, Any] | None:
    values = {
        "id": str(uuid.uuid4()), "run_id": run_id, "candidate_id": candidate_id,
        "action_type": action_type, "status": status, "execution_state": execution_state,
        "payload": payload, "idempotency_key": idempotency_key,
    }
    columns = list(values)
    cur.execute(
        f"INSERT INTO agent_action_proposals ({', '.join(columns)}) VALUES ({', '.join('%s' for _ in columns)}) "
        "ON CONFLICT (idempotency_key) DO NOTHING RETURNING *",
        [Json(values[column]) if column == "payload" else values[column] for column in columns],
    )
    return _row_to_dict(cur, cur.fetchone())


def insert_approval_decision(
    run_id: str, proposal_id: str, decision: str, user_id: str | None = None,
    comment: str | None = None, cur: PgCursor | None = None,
) -> dict[str, Any]:
    if cur is not None:
        return _insert_approval_decision_with_cursor(cur, run_id, proposal_id, decision, user_id, comment)
    with get_cursor() as (_, cur):
        return _insert_approval_decision_with_cursor(cur, run_id, proposal_id, decision, user_id, comment)


def _insert_approval_decision_with_cursor(
    cur: PgCursor, run_id: str, proposal_id: str, decision: str, user_id: str | None, comment: str | None
) -> dict[str, Any]:
    values = {"id": str(uuid.uuid4()), "run_id": run_id, "proposal_id": proposal_id, "user_id": user_id, "decision": decision, "comment": comment}
    columns = list(values)
    cur.execute(
        f"INSERT INTO agent_approval_decisions ({', '.join(columns)}) VALUES ({', '.join('%s' for _ in columns)}) RETURNING *",
        [values[column] for column in columns],
    )
    return _row_to_dict(cur, cur.fetchone())  # type: ignore[return-value]


def _insert_returning(table: str, values: dict[str, Any], json_columns: set[str]) -> dict[str, Any]:
    columns = list(values)
    placeholders = ", ".join("%s" for _ in columns)
    params = [Json(values[column]) if column in json_columns else values[column] for column in columns]
    with get_cursor() as (_, cur):
        cur.execute(
            f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({placeholders}) RETURNING *",
            params,
        )
        return _row_to_dict(cur, cur.fetchone())  # type: ignore[return-value]


def _row_to_dict_from_columns(columns: list[str], row: tuple[Any, ...]) -> dict[str, Any]:
    result = dict(zip(columns, row))
    for key, value in result.items():
        if isinstance(value, uuid.UUID):
            result[key] = str(value)
    return result


def _list_rows(cur: PgCursor, query: str, params: tuple[object, ...]) -> list[dict[str, Any]]:
    cur.execute(query, params)
    columns = [column[0] for column in cur.description]
    return [_row_to_dict_from_columns(columns, row) for row in cur.fetchall()]


def _candidate_detail(candidate: dict[str, Any]) -> dict[str, Any]:
    snapshot = dict(candidate.get("candidate_snapshot") or {})
    return {
        **snapshot,
        "id": candidate["id"],
        "candidate_id": candidate["id"],
        "company_id": candidate.get("company_id"),
        "source": candidate.get("source"),
        "status": candidate.get("status"),
    }


def _evidence_by_company(evidence: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for item in evidence:
        company_id = item.get("company_id")
        if company_id is None:
            continue
        snapshot = dict(item.get("evidence_snapshot") or {})
        grouped.setdefault(str(company_id), []).append({
            **snapshot,
            "evidence_id": str(snapshot.get("evidence_id") or item["id"]),
            "dimension": snapshot.get("dimension") or item.get("evidence_type"),
            "source": snapshot.get("source_type") or item.get("source"),
            "source_reference": snapshot.get("source_reference") or item.get("source_reference"),
            "monitor_target_id": snapshot.get("monitor_target_id") or item.get("monitor_target_id"),
        })
    return grouped


def _evidence_reviews_by_company(reviews: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {
        str(review["company_id"]): dict(review.get("review_snapshot") or {})
        for review in reviews
    }


def _decision_detail(decision: dict[str, Any]) -> dict[str, Any]:
    return {
        **dict(decision.get("score_snapshot") or {}),
        **dict(decision.get("reason_snapshot") or {}),
        "id": decision["id"],
        "candidate_id": decision.get("candidate_id"),
        "group": dict(decision.get("score_snapshot") or {}).get("group", decision["decision"]),
    }


def _proposal_detail(proposal: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": proposal["id"],
        "candidate_id": proposal.get("candidate_id"),
        "action_type": proposal["action_type"],
        "status": proposal["status"],
        "execution_state": proposal["execution_state"],
        "payload": dict(proposal.get("payload") or {}),
    }


def _stable_id(run_id: str, key: str) -> str:
    return str(uuid.uuid5(uuid.UUID(run_id), key))


def _candidate_source(candidate: dict[str, Any]) -> str:
    return "staged_external" if candidate.get("status") == "staged_candidate" else "local"


def _candidate_status(candidate: dict[str, Any]) -> str:
    return str(candidate.get("status") or "identity_pending")


def _upsert_evidence(
    run_id: str,
    company_id: str,
    evidence: dict[str, Any],
    candidate_id: str | None,
    cur: PgCursor,
    *,
    monitor_target_id: str | None = None,
) -> None:
    evidence_key = _evidence_key(evidence)
    evidence_id = _stable_id(
        run_id,
        f"evidence:{company_id}:{evidence_key}",
    )
    cur.execute(
        """
        INSERT INTO agent_evidence (
            id, run_id, company_id, candidate_id, evidence_type, source, source_reference, evidence_snapshot,
            monitor_target_id
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (id) DO UPDATE SET
            candidate_id = EXCLUDED.candidate_id,
            evidence_type = EXCLUDED.evidence_type,
            source = EXCLUDED.source,
            source_reference = EXCLUDED.source_reference,
            evidence_snapshot = EXCLUDED.evidence_snapshot,
            monitor_target_id = EXCLUDED.monitor_target_id
        """,
        (
            evidence_id, run_id, company_id, candidate_id,
            str(evidence.get("dimension") or "unknown"),
            str(evidence.get("source_type") or evidence.get("source") or "unknown"),
            evidence.get("source_reference"), Json(evidence),
            monitor_target_id or evidence.get("monitor_target_id"),
        ),
    )


def _evidence_key(evidence: dict[str, Any]) -> str:
    """Return a replay-stable evidence identity without any list-position input."""
    dimension = str(evidence.get("dimension") or "unknown")
    claim_code = str(evidence.get("claim_code") or "unknown")
    source_type = str(evidence.get("source_type") or evidence.get("source") or "unknown")
    source_identity = evidence.get("source_reference") or evidence.get("provider_key") or evidence.get("raw_payload_ref")
    if source_identity:
        return f"{dimension}:{claim_code}:{source_type}:{source_identity}"
    stable_snapshot = {key: value for key, value in evidence.items() if key != "evidence_id"}
    digest = hashlib.sha256(
        json.dumps(stable_snapshot, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()
    return f"{dimension}:{claim_code}:{source_type}:snapshot:{digest}"
