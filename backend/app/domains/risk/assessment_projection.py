"""Idempotent Mongo projection of already committed PG assessments."""

from datetime import datetime, timezone

from app.db.mongo import get_db
from app.domains.risk import assessment_repo as repo
from app.domains.risk.assessment_contracts import canonical_hash
from app.domains.risk.assessment_service import verify_frozen_payloads
from app.domains.sourcing_risk.evidence_service import (
    commit_raw_payloads,
    compensate_raw_payloads,
)


def project_assessment(event: dict) -> None:
    assessment_id = event["payload"]["assessment_id"]
    row = repo.get_assessment(assessment_id, "", allow_all=True)
    if not row or row["assessment_status"] != "committed":
        raise ValueError("评估结果尚未提交")
    manifest = row["evidence_manifest"]
    if (
        canonical_hash({"inputs": row["input_snapshot"], "manifest": manifest})
        != row["input_digest"]
    ):
        raise ValueError("冻结输入摘要不一致")
    payloads = verify_frozen_payloads(row, manifest)
    commit_raw_payloads(payloads)
    repo.mark_payloads(assessment_id, "committed")
    identity = row["identity"]
    result = row["result"] or {}
    document = {
        "_id": "assessment:" + assessment_id,
        "assessment_id": assessment_id,
        "snapshot_id": assessment_id,
        "snapshot_version": row["assessment_version"],
        "previous_snapshot_id": row["previous_assessment_id"],
        "company_name": row["company_name"],
        "company_id": str(row["company_id"]),
        "monitor_target_id": row["monitor_target_id"],
        "supplier_id": identity.get("supplier_id"),
        "candidate_id": identity.get("candidate_id"),
        "supplier_code": identity.get("supplier_code"),
        "target_type": identity.get("target_type"),
        "checked_at": row["assessed_at"],
        "assessment_dimensions": identity["dimensions"],
        "assessment_scope_key": row["scope_key"],
        "actor_id": row["actor_id"],
        "policy_hash": row["policy_hash"],
        "scoring_version": row["scoring_version"],
        "quality": row["quality"],
        "verification_status": "verified",
        "evidence_manifest": manifest,
        "risk_score": result.get("risk_score"),
        "risk_level": result.get("risk_level"),
        "risk_detail": result.get("risk_detail"),
        "score_breakdown": result.get("score_breakdown"),
        "financial": result.get("financial"),
        "projected_at": datetime.now(timezone.utc),
    }
    db = get_db()
    db["assessment_snapshots"].update_one(
        {"_id": document["_id"]}, {"$setOnInsert": document}, upsert=True
    )
    if len(identity["dimensions"]) == 4 and row["quality"] in {"complete", "partial"}:
        db["alert_snapshots"].update_one(
            {"_id": document["_id"]}, {"$setOnInsert": document}, upsert=True
        )


def reconcile_assessment_payloads(event: dict) -> None:
    assessment_id = event["payload"]["assessment_id"]
    row = repo.get_assessment(assessment_id, "", allow_all=True)
    if not row:
        return
    if row["assessment_status"] == "committed":
        project_assessment(event)
        return
    if row["assessment_status"] != "needs_review":
        raise ValueError("采集仍活动，不允许补偿原始证据")
    staging = repo.list_payload_staging(assessment_id)
    documents = []
    collection = get_db()["agent_evidence_payloads"]
    for intent in staging:
        payload = collection.find_one({"raw_payload_ref": intent["raw_payload_ref"]})
        if payload:
            if (
                payload.get("staging_owner") != intent["staging_owner"]
                or payload.get("lifecycle_status") == "committed"
            ):
                raise ValueError("证据所有权冲突，保留人工核对")
            documents.append(payload)
    compensate_raw_payloads(documents, reason="assessment_collection_aborted")
    for intent in staging:
        if collection.find_one({"raw_payload_ref": intent["raw_payload_ref"]}):
            raise ValueError("证据补偿尚未完成")
    repo.mark_payloads(assessment_id, "compensated")
