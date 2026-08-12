"""PostgreSQL persistence primitives for sourcing-risk agent runs."""

import uuid
from typing import Any

from psycopg2.extras import Json

from app.db.postgres import PgCursor, get_cursor


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
) -> dict[str, Any]:
    run_id = str(uuid.uuid4())
    with get_cursor() as (_, cur):
        cur.execute(
            """
            INSERT INTO agent_runs (id, run_type, user_id, status, requirement)
            VALUES (%s, %s, %s, %s, %s)
            RETURNING *
            """,
            (run_id, run_type, user_id, status, Json(requirement)),
        )
        return _row_to_dict(cur, cur.fetchone())  # type: ignore[return-value]


def get_run_for_user(run_id: str, user_id: str) -> dict[str, Any] | None:
    with get_cursor() as (_, cur):
        cur.execute(
            "SELECT * FROM agent_runs WHERE id = %s AND user_id = %s",
            (run_id, user_id),
        )
        return _row_to_dict(cur, cur.fetchone())


def update_run_status(
    run_id: str,
    expected_version: int,
    status: str,
    error_code: str | None = None,
) -> dict[str, Any] | None:
    with get_cursor() as (_, cur):
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


def insert_policy_snapshot(
    run_id: str, policy: dict[str, Any], template_id: str | None = None
) -> dict[str, Any]:
    return _insert_returning(
        "sourcing_policy_snapshots",
        {"id": str(uuid.uuid4()), "run_id": run_id, "template_id": template_id, "policy": policy},
        {"policy"},
    )


def insert_candidate(
    run_id: str, source: str, status: str, candidate_snapshot: dict[str, Any], company_id: str | None = None
) -> dict[str, Any]:
    return _insert_returning(
        "agent_run_candidates",
        {"id": str(uuid.uuid4()), "run_id": run_id, "company_id": company_id, "source": source, "status": status, "candidate_snapshot": candidate_snapshot},
        {"candidate_snapshot"},
    )


def insert_evidence(
    run_id: str, evidence_type: str, source: str, evidence_snapshot: dict[str, Any], candidate_id: str | None = None, source_reference: str | None = None
) -> dict[str, Any]:
    return _insert_returning(
        "agent_evidence",
        {"id": str(uuid.uuid4()), "run_id": run_id, "candidate_id": candidate_id, "evidence_type": evidence_type, "source": source, "source_reference": source_reference, "evidence_snapshot": evidence_snapshot},
        {"evidence_snapshot"},
    )


def insert_decision(
    run_id: str, decision: str, score_snapshot: dict[str, Any], reason_snapshot: dict[str, Any], candidate_id: str | None = None
) -> dict[str, Any]:
    return _insert_returning(
        "candidate_decisions",
        {"id": str(uuid.uuid4()), "run_id": run_id, "candidate_id": candidate_id, "decision": decision, "score_snapshot": score_snapshot, "reason_snapshot": reason_snapshot},
        {"score_snapshot", "reason_snapshot"},
    )


def insert_action_proposal(
    run_id: str, action_type: str, payload: dict[str, Any], idempotency_key: str, status: str = "pending", execution_state: str = "pending", candidate_id: str | None = None
) -> dict[str, Any]:
    return _insert_returning(
        "agent_action_proposals",
        {"id": str(uuid.uuid4()), "run_id": run_id, "candidate_id": candidate_id, "action_type": action_type, "status": status, "execution_state": execution_state, "payload": payload, "idempotency_key": idempotency_key},
        {"payload"},
    )


def insert_approval_decision(
    run_id: str, proposal_id: str, decision: str, user_id: str | None = None, comment: str | None = None
) -> dict[str, Any]:
    return _insert_returning(
        "agent_approval_decisions",
        {"id": str(uuid.uuid4()), "run_id": run_id, "proposal_id": proposal_id, "user_id": user_id, "decision": decision, "comment": comment},
        set(),
    )


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
