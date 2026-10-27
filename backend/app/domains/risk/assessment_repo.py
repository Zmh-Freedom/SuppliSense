"""PostgreSQL authority for assessment identities, frozen inputs and results."""

from datetime import datetime, timezone
from uuid import UUID, uuid4

from psycopg2.extras import Json

from app.core.errors import DomainError
from app.db.postgres import get_cursor
from app.domains.agent_run.coordinator import (
    current_execution,
    lock_run,
    require_active,
)
from app.domains.agent_run.repo import _row_to_dict
from app.domains.outbox.repo import enqueue_event
from app.domains.risk.assessment_contracts import (
    canonical_hash,
    identity_hash,
    json_snapshot,
    validate_manifest,
)


class AssessmentConflict(DomainError):
    def __init__(self, message: str = "评估幂等键或冻结输入发生冲突") -> None:
        super().__init__("ASSESSMENT_CONFLICT", message, 409)


def _require_run(cur, run_id: str | None, actor_id: str | None = None) -> None:
    if run_id:
        run = lock_run(cur, run_id)
        require_active(cur, run, current_execution.get())
        if actor_id and str(run["user_id"]) != actor_id:
            raise PermissionError("评估操作者与 Run 不一致")


def _lock_assessment(cur, assessment_id: str) -> dict:
    cur.execute("SELECT run_id FROM assessment_history WHERE id=%s", (assessment_id,))
    row = cur.fetchone()
    if not row:
        raise ValueError("评估不存在")
    _require_run(cur, str(row[0]) if row[0] else None)
    cur.execute(
        "SELECT * FROM assessment_history WHERE id=%s FOR UPDATE", (assessment_id,)
    )
    return _row_to_dict(cur, cur.fetchone())


def begin_assessment(
    identity: dict,
    *,
    actor_id: str,
    policy: dict,
    run_id: str | None = None,
    logical_key: str | None = None,
    request_key: str | None = None,
    provenance: dict | None = None,
    linked_assessment_id: str | None = None,
) -> dict:
    """Allocate an ID before providers run; retries retain the first policy."""
    identity = json_snapshot(identity)
    identity["company_id"] = str(UUID(identity["company_id"]))
    identity["dimensions"] = sorted(set(identity["dimensions"]))
    if not actor_id or not identity.get("company_name") or not identity["dimensions"]:
        raise ValueError("评估必须绑定操作者、对象和请求范围")
    if run_id and not logical_key:
        raise ValueError("Run 评估必须提供逻辑任务键")
    if not run_id and not request_key:
        raise ValueError("独立评估必须提供请求幂等键")
    actor_scope = "actor:" + actor_id
    if linked_assessment_id:
        identity["linked_assessment_id"] = linked_assessment_id
    request_hash = identity_hash(identity)
    scope_key = canonical_hash(
        {
            key: value
            for key, value in identity.items()
            if key not in {"company_name", "company_id", "linked_assessment_id"}
        }
    )
    policy = json_snapshot(policy)
    policy_hash = canonical_hash(policy)
    policy_scope = canonical_hash(policy["required_dimensions"])
    with get_cursor() as (_, cur):
        _require_run(cur, run_id, actor_id)
        mutex = (
            f"assessment:{run_id}:{logical_key}"
            if run_id
            else f"assessment:{actor_scope}:{request_key}"
        )
        cur.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))", (mutex,))
        if run_id:
            cur.execute(
                "SELECT * FROM assessment_history WHERE run_id=%s AND logical_key=%s FOR UPDATE",
                (run_id, logical_key),
            )
        else:
            cur.execute(
                "SELECT * FROM assessment_history WHERE actor_scope=%s AND request_key=%s FOR UPDATE",
                (actor_scope, request_key),
            )
        existing = _row_to_dict(cur, cur.fetchone())
        if existing:
            if (
                existing["actor_id"] != actor_id
                or existing["request_hash"] != request_hash
            ):
                raise AssessmentConflict()
            existing["acquired"] = False
            permit = current_execution.get()
            expired = (
                not run_id
                and existing["collection_lease_until"]
                and existing["collection_lease_until"] < datetime.now(timezone.utc)
            )
            takeover = (
                permit
                and int(existing["execution_fencing_token"]) < permit.fencing_token
            )
            if existing["assessment_status"] == "collecting" and (expired or takeover):
                cur.execute(
                    "SELECT COUNT(*) FROM assessment_payload_staging WHERE assessment_id=%s",
                    (existing["id"],),
                )
                if expired or cur.fetchone()[0]:
                    cur.execute(
                        "UPDATE assessment_history SET assessment_status='needs_review',quality='needs_review' WHERE id=%s RETURNING *",
                        (existing["id"],),
                    )
                    existing = _row_to_dict(cur, cur.fetchone())
                    cur.execute(
                        "UPDATE assessment_payload_staging SET status='pending_compensation' WHERE assessment_id=%s",
                        (existing["id"],),
                    )
                    enqueue_event(
                        cur,
                        "risk.assessment.reconcile",
                        "assessment",
                        existing["id"],
                        {"assessment_id": existing["id"]},
                    )
                    existing["acquired"] = False
                    return existing
            if (
                existing["assessment_status"] == "collecting"
                and permit
                and int(existing["execution_fencing_token"]) < permit.fencing_token
            ):
                token = str(uuid4())
                cur.execute(
                    "UPDATE assessment_history SET collector_token=%s,execution_fencing_token=%s WHERE id=%s RETURNING *",
                    (token, permit.fencing_token, existing["id"]),
                )
                existing = _row_to_dict(cur, cur.fetchone())
                existing["acquired"] = True
            return existing
        cur.execute(
            "SELECT id FROM companies WHERE id=%s AND verification_status='verified' AND merged_into_id IS NULL",
            (identity["company_id"],),
        )
        if not cur.fetchone():
            raise ValueError("评估对象尚未核验或已合并，请先确认规范主体")
        user_id = None
        if not actor_id.startswith("system:"):
            user_id = str(UUID(actor_id))
            cur.execute("SELECT is_active FROM users WHERE id=%s", (user_id,))
            actor = cur.fetchone()
            if not actor or not actor[0]:
                raise PermissionError("评估操作者不存在或已停用")
        cur.execute(
            "SELECT policy_hash FROM assessment_policies WHERE policy_id=%s AND version=%s AND scope_key=%s",
            (policy["policy_id"], policy["version"], policy_scope),
        )
        previous = cur.fetchone()
        if previous and previous[0] != policy_hash:
            raise AssessmentConflict("同一政策版本的配置不可变更")
        cur.execute(
            "INSERT INTO assessment_policies(policy_hash,policy_id,version,scope_key,snapshot) VALUES(%s,%s,%s,%s,%s) ON CONFLICT(policy_hash) DO NOTHING",
            (
                policy_hash,
                policy["policy_id"],
                policy["version"],
                policy_scope,
                Json(policy),
            ),
        )
        assessment_id, token = str(uuid4()), str(uuid4())
        sequence = canonical_hash(
            [identity["company_id"], scope_key, policy_hash, actor_scope]
        )
        cur.execute(
            "INSERT INTO assessment_sequences(sequence_key) VALUES(%s) ON CONFLICT DO NOTHING",
            (sequence,),
        )
        cur.execute(
            "SELECT version,last_assessment_id FROM assessment_sequences WHERE sequence_key=%s FOR UPDATE",
            (sequence,),
        )
        previous_version, predecessor = cur.fetchone()
        version = int(previous_version) + 1
        cur.execute(
            "UPDATE assessment_sequences SET version=%s,last_assessment_id=%s WHERE sequence_key=%s",
            (version, assessment_id, sequence),
        )
        permit = current_execution.get()
        cur.execute(
            """INSERT INTO assessment_history(id,user_id,company_name,scoring_version,actor_id,actor_scope,
               company_id,monitor_target_id,run_id,logical_key,request_key,request_hash,scope_key,identity,
               assessment_status,quality,policy_hash,policy_snapshot,provenance,assessment_version,
               previous_assessment_id,collector_token,execution_fencing_token,collection_lease_until)
               VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'collecting','unavailable',%s,%s,%s,%s,%s,%s,%s,NOW()+INTERVAL '180 seconds') RETURNING *""",
            (
                assessment_id,
                user_id,
                identity["company_name"],
                policy["scoring_version"],
                actor_id,
                actor_scope,
                identity["company_id"],
                identity.get("monitor_target_id"),
                run_id,
                logical_key,
                request_key,
                request_hash,
                scope_key,
                Json(identity),
                policy_hash,
                Json(policy),
                Json(json_snapshot(provenance or {})),
                version,
                predecessor,
                token,
                permit.fencing_token if permit else 0,
            ),
        )
        created = _row_to_dict(cur, cur.fetchone())
        if linked_assessment_id:
            cur.execute(
                "UPDATE assessment_history SET linked_assessment_id=%s WHERE id=%s",
                (linked_assessment_id, assessment_id),
            )
            created["linked_assessment_id"] = linked_assessment_id
        created["acquired"] = True
        return created


def get_assessment(
    assessment_id: str, actor_id: str, *, allow_all: bool = False
) -> dict | None:
    with get_cursor() as (_, cur):
        cur.execute(
            "SELECT * FROM assessment_history WHERE id=%s AND (actor_id=%s OR %s)",
            (assessment_id, actor_id, allow_all),
        )
        row = _row_to_dict(cur, cur.fetchone())
        if row:
            cur.execute(
                "SELECT manifest FROM assessment_evidence WHERE assessment_id=%s ORDER BY evidence_id",
                (assessment_id,),
            )
            row["evidence_manifest"] = [item[0] for item in cur.fetchall()]
        return row


def freeze_assessment(
    assessment_id: str, collector_token: str, *, inputs: dict, manifest: list[dict]
) -> dict:
    manifest = sorted(validate_manifest(manifest), key=lambda item: item["evidence_id"])
    inputs = json_snapshot(inputs)
    digest = canonical_hash({"inputs": inputs, "manifest": manifest})
    with get_cursor() as (_, cur):
        row = _lock_assessment(cur, assessment_id)
        if row["input_digest"]:
            if row["input_digest"] != digest:
                raise AssessmentConflict("冻结后不能替换证据或输入，请创建关联的新评估")
            return row
        if (
            str(row["collector_token"]) != collector_token
            or row["assessment_status"] != "collecting"
        ):
            raise AssessmentConflict("评估采集许可已失效")
        for entry in manifest:
            cur.execute(
                "INSERT INTO assessment_evidence(assessment_id,evidence_id,manifest) VALUES(%s,%s,%s)",
                (assessment_id, entry["evidence_id"], Json(entry)),
            )
        cur.execute(
            "UPDATE assessment_history SET input_snapshot=%s,input_digest=%s,evidence_digest=%s,evidence_frozen_at=NOW(),assessment_status='frozen' WHERE id=%s RETURNING *",
            (Json(inputs), digest, canonical_hash(manifest), assessment_id),
        )
        return _row_to_dict(cur, cur.fetchone())


def commit_assessment(
    assessment_id: str, collector_token: str, *, result: dict, quality: str
) -> dict:
    if quality not in {"complete", "partial", "unavailable", "needs_review"}:
        raise ValueError("评估质量无效")
    result = json_snapshot(result)
    with get_cursor() as (_, cur):
        row = _lock_assessment(cur, assessment_id)
        if row["assessment_status"] == "committed":
            if row["result"] != result or row["quality"] != quality:
                raise AssessmentConflict("评估已提交，不允许覆盖原始结果")
            return row
        if (
            row["assessment_status"] != "frozen"
            or str(row["collector_token"]) != collector_token
        ):
            raise AssessmentConflict("评估尚未冻结或执行许可已失效")
        cur.execute(
            "UPDATE assessment_history SET assessment_status='committed',quality=%s,result=%s,risk_score=%s,risk_level=%s,score_breakdown=%s,financial_data=%s,risk_detail=%s,assessed_at=evidence_frozen_at,completed_at=NOW() WHERE id=%s RETURNING *",
            (
                quality,
                Json(result),
                result.get("risk_score"),
                result.get("risk_level"),
                Json(result.get("score_breakdown")),
                Json(result.get("financial")),
                Json(result.get("risk_detail")),
                assessment_id,
            ),
        )
        committed = _row_to_dict(cur, cur.fetchone())
        if row["run_id"]:
            receipt = {
                "assessment_id": assessment_id,
                "input_digest": row["input_digest"],
                "policy_hash": row["policy_hash"],
                "quality": quality,
            }
            cur.execute(
                "INSERT INTO assessment_task_results(run_id,logical_key,assessment_id,receipt) VALUES(%s,%s,%s,%s)",
                (row["run_id"], row["logical_key"], assessment_id, Json(receipt)),
            )
        enqueue_event(
            cur,
            "risk.assessment.committed",
            "assessment",
            assessment_id,
            {"assessment_id": assessment_id},
        )
        return committed


def reserve_payloads(
    assessment_id: str, collector_token: str, payloads: list[dict]
) -> None:
    with get_cursor() as (_, cur):
        row = _lock_assessment(cur, assessment_id)
        if (
            row["assessment_status"] != "collecting"
            or str(row["collector_token"]) != collector_token
        ):
            raise AssessmentConflict("采集许可已失效")
        for payload in payloads:
            detail = {
                key: payload.get(key)
                for key in (
                    "raw_payload_ref",
                    "assessment_id",
                    "run_id",
                    "company_id",
                    "dimension",
                    "content_hash",
                    "staging_owner",
                )
            }
            cur.execute(
                "INSERT INTO assessment_payload_staging(assessment_id,raw_payload_ref,staging_owner,detail) VALUES(%s,%s,%s,%s) ON CONFLICT(assessment_id,raw_payload_ref) DO NOTHING",
                (
                    assessment_id,
                    payload["raw_payload_ref"],
                    collector_token,
                    Json(detail),
                ),
            )
            cur.execute(
                "SELECT staging_owner,status FROM assessment_payload_staging WHERE assessment_id=%s AND raw_payload_ref=%s",
                (assessment_id, payload["raw_payload_ref"]),
            )
            owner, status = cur.fetchone()
            if owner != collector_token or status not in {"pending", "committed"}:
                raise AssessmentConflict("原始载荷已有其他采集所有者，需要核对")


def list_payload_staging(assessment_id: str) -> list[dict]:
    with get_cursor() as (_, cur):
        cur.execute(
            "SELECT * FROM assessment_payload_staging WHERE assessment_id=%s ORDER BY raw_payload_ref",
            (assessment_id,),
        )
        return [_row_to_dict(cur, row) for row in cur.fetchall()]


def mark_payloads(assessment_id: str, status: str) -> None:
    with get_cursor() as (_, cur):
        cur.execute(
            "UPDATE assessment_payload_staging SET status=%s,updated_at=NOW() WHERE assessment_id=%s",
            (status, assessment_id),
        )


def abort_collection(assessment_id: str, collector_token: str) -> None:
    """Record remediation intent before attempting cross-store cleanup."""
    with get_cursor() as (_, cur):
        cur.execute(
            "SELECT * FROM assessment_history WHERE id=%s FOR UPDATE", (assessment_id,)
        )
        row = _row_to_dict(cur, cur.fetchone())
        if (
            row
            and row["assessment_status"] == "collecting"
            and str(row["collector_token"]) == collector_token
        ):
            cur.execute(
                "UPDATE assessment_history SET assessment_status='needs_review',quality='needs_review' WHERE id=%s",
                (assessment_id,),
            )
            cur.execute(
                "UPDATE assessment_payload_staging SET status='pending_compensation' WHERE assessment_id=%s",
                (assessment_id,),
            )
            enqueue_event(
                cur,
                "risk.assessment.reconcile",
                "assessment",
                assessment_id,
                {"assessment_id": assessment_id},
            )


def find_cached(identity: dict, actor_id: str, policy_hash: str) -> dict | None:
    identity = json_snapshot(identity)
    identity["dimensions"] = sorted(set(identity["dimensions"]))
    with get_cursor() as (_, cur):
        cur.execute(
            "SELECT * FROM assessment_history WHERE actor_id=%s AND request_hash=%s AND policy_hash=%s AND assessment_status='committed' AND quality IN ('complete','partial') ORDER BY assessment_version DESC LIMIT 1",
            (actor_id, identity_hash(identity), policy_hash),
        )
        row = _row_to_dict(cur, cur.fetchone())
    return get_assessment(row["id"], actor_id) if row else None


def list_assessments(
    company_id: str, actor_id: str, *, days: int = 90, limit: int = 1000
) -> list[dict]:
    with get_cursor() as (_, cur):
        cur.execute(
            """SELECT * FROM assessment_history WHERE company_id=%s AND actor_id=%s
            AND assessment_status='committed' AND assessed_at>=NOW()-(%s * INTERVAL '1 day')
            ORDER BY assessed_at DESC,assessment_version DESC LIMIT %s""",
            (company_id, actor_id, max(1, min(days, 3650)), max(1, min(limit, 1000))),
        )
        return [_row_to_dict(cur, row) for row in cur.fetchall()]


def get_reviews(assessment_id: str) -> list[dict]:
    with get_cursor() as (_, cur):
        cur.execute(
            "SELECT * FROM assessment_reviews WHERE assessment_id=%s ORDER BY created_at,id",
            (assessment_id,),
        )
        return [_row_to_dict(cur, row) for row in cur.fetchall()]


def append_review(
    assessment_id: str,
    actor_id: str,
    *,
    reason: str,
    decision: str,
    request_key: str,
    allow_all: bool = False,
) -> dict:
    reason = reason.strip()
    if (
        not reason
        or not request_key
        or decision not in {"confirmed", "needs_review", "rejected"}
    ):
        raise ValueError("复核需要原因、有效结论和幂等键")
    digest = canonical_hash({"reason": reason, "decision": decision})
    with get_cursor() as (_, cur):
        # Review does not mutate the frozen result, and is allowed after Run completion.
        cur.execute(
            "SELECT actor_id,assessment_status FROM assessment_history WHERE id=%s FOR UPDATE",
            (assessment_id,),
        )
        row = cur.fetchone()
        if not row or (row[0] != actor_id and not allow_all):
            raise PermissionError("无权复核该评估")
        if row[1] != "committed":
            raise AssessmentConflict("仅可复核已提交评估")
        cur.execute(
            "SELECT * FROM assessment_reviews WHERE assessment_id=%s AND actor_id=%s AND idempotency_key=%s",
            (assessment_id, actor_id, request_key),
        )
        existing = _row_to_dict(cur, cur.fetchone())
        if existing:
            if existing["request_hash"] != digest:
                raise AssessmentConflict("复核幂等键内容不一致")
            return existing
        review_id = str(uuid4())
        receipt = {
            "review_id": review_id,
            "assessment_id": assessment_id,
            "decision": decision,
            "original_result_preserved": True,
        }
        cur.execute(
            "INSERT INTO assessment_reviews(id,assessment_id,actor_id,reason,decision,idempotency_key,request_hash,receipt) VALUES(%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *",
            (
                review_id,
                assessment_id,
                actor_id,
                reason,
                decision,
                request_key,
                digest,
                Json(receipt),
            ),
        )
        review = _row_to_dict(cur, cur.fetchone())
        from app.domains.auth.audit_repo import create_log_with_cursor

        create_log_with_cursor(
            cur,
            action="risk.assessment.reviewed",
            user_id=actor_id,
            resource_type="assessment",
            resource_id=assessment_id,
            details=receipt,
        )
        return review
