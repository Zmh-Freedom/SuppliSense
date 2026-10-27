"""Pure, framework-independent rules for deciding whether evidence is usable."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone
from typing import Any


_ALLOWED_SOURCE_STATES = frozenset(
    {"available", "empty", "missing", "stale", "failed", "conflicting", "unknown", "not_queried"}
)


def _timestamp(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _result(
    evidence: dict[str, Any],
    *,
    status: str,
    reason_code: str,
    detail: str,
    observed_at: datetime | None = None,
    collected_at: datetime | None = None,
    expires_at: datetime | None = None,
) -> dict[str, Any]:
    result = deepcopy(evidence)
    result.update(
        {
            "status": status,
            "observed_at": observed_at.isoformat() if observed_at else None,
            "collected_at": collected_at.isoformat() if collected_at else None,
            "expires_at": expires_at.isoformat() if expires_at else None,
            "reason_codes": [reason_code],
            "detail": detail,
        }
    )
    return result


def evaluate_evidence_validity(
    evidence: dict[str, Any],
    *,
    expected_entity_id: str,
    expected_scope: str,
    now: datetime,
    max_age: timedelta | None = None,
    policy_version: str | None = None,
) -> dict[str, Any]:
    """Return a detached evidence record with its current usability status.

    Collection time records when this system read evidence; it never makes an
    old source observation fresh. An explicit source expiry and a declared
    max-age policy both bound usability, with the earlier deadline taking
    precedence.
    """
    if not isinstance(evidence, dict):
        return _result(
            {}, status="unknown", reason_code="INVALID_EVIDENCE",
            detail="来源记录格式无效，当前无法核验。",
        )

    source_state = evidence.get("status")
    if source_state not in _ALLOWED_SOURCE_STATES:
        return _result(
            evidence, status="unknown", reason_code="INVALID_SOURCE_STATE",
            detail="来源状态未知，当前无法核验。",
        )
    if source_state == "missing":
        return _result(
            evidence, status="missing", reason_code="SOURCE_MISSING",
            detail="当前没有该来源记录。",
        )
    if source_state == "failed":
        return _result(
            evidence, status="failed", reason_code="SOURCE_QUERY_FAILED",
            detail="来源查询失败，当前无法核验。",
        )
    if source_state == "conflicting" or evidence.get("conflict"):
        return _result(
            evidence, status="conflicting", reason_code="SOURCE_CONFLICT",
            detail="来源记录存在冲突，需要核对。",
        )
    if source_state in {"unknown", "not_queried"}:
        return _result(
            evidence, status="unknown", reason_code="SOURCE_NOT_VERIFIABLE",
            detail="来源尚未完成核验。",
        )
    if (
        not expected_entity_id
        or not expected_scope
        or evidence.get("entity_id") != expected_entity_id
        or evidence.get("scope") != expected_scope
    ):
        return _result(
            evidence, status="conflicting", reason_code="ENTITY_OR_SCOPE_MISMATCH",
            detail="来源主体或业务范围与当前对象不一致。",
        )

    observed_at = _timestamp(evidence.get("observed_at"))
    collected_at = _timestamp(evidence.get("collected_at"))
    explicit_expiry = _timestamp(evidence.get("expires_at"))
    current_time = _timestamp(now)
    if (
        observed_at is None
        or collected_at is None
        or current_time is None
        or observed_at > current_time
        or collected_at > current_time
        or collected_at < observed_at
        or (evidence.get("expires_at") is not None and explicit_expiry is None)
        or (explicit_expiry is not None and explicit_expiry < observed_at)
    ):
        return _result(
            evidence, status="unknown", reason_code="INVALID_SOURCE_TIME",
            detail="来源时间缺失或矛盾，当前有效性无法判断。",
            observed_at=observed_at, collected_at=collected_at,
        )

    source_version = evidence.get("source_version")
    if not isinstance(source_version, str) or not source_version.strip():
        return _result(
            evidence, status="unknown", reason_code="SOURCE_VERSION_MISSING",
            detail="来源版本缺失，当前无法确认覆盖范围。",
            observed_at=observed_at, collected_at=collected_at,
            expires_at=explicit_expiry,
        )
    if evidence.get("source_mode") != "formal":
        return _result(
            evidence, status="unknown", reason_code="SOURCE_NOT_FORMAL",
            detail="该记录不是经确认的正式来源。",
            observed_at=observed_at, collected_at=collected_at,
            expires_at=explicit_expiry,
        )
    if evidence.get("coverage_partial"):
        return _result(
            evidence, status="unknown", reason_code="SOURCE_COVERAGE_PARTIAL",
            detail="来源覆盖不完整，不能形成完整结论。",
            observed_at=observed_at, collected_at=collected_at,
            expires_at=explicit_expiry,
        )

    max_age_expiry: datetime | None = None
    if max_age is not None:
        if (
            not isinstance(max_age, timedelta)
            or max_age <= timedelta(0)
            or not isinstance(policy_version, str)
            or not policy_version.strip()
        ):
            return _result(
                evidence, status="unknown", reason_code="FRESHNESS_POLICY_INVALID",
                detail="有效期策略缺失或无效，当前无法核验。",
                observed_at=observed_at, collected_at=collected_at,
                expires_at=explicit_expiry,
            )
        max_age_expiry = observed_at + max_age
    elif policy_version is not None and (
        not isinstance(policy_version, str) or not policy_version.strip()
    ):
        return _result(
            evidence, status="unknown", reason_code="FRESHNESS_POLICY_INVALID",
            detail="有效期策略版本无效，当前无法核验。",
            observed_at=observed_at, collected_at=collected_at,
            expires_at=explicit_expiry,
        )

    deadlines = [stamp for stamp in (explicit_expiry, max_age_expiry) if stamp is not None]
    expiry = min(deadlines) if deadlines else None
    if expiry is None:
        return _result(
            evidence, status="unknown", reason_code="FRESHNESS_BOUNDARY_MISSING",
            detail="来源没有已确认的有效期，当前状态待核验。",
            observed_at=observed_at, collected_at=collected_at,
        )

    receipt = evidence.get("query_receipt")
    receipt_status = receipt.get("status") if isinstance(receipt, dict) else None
    record_count = receipt.get("record_count") if isinstance(receipt, dict) else None
    valid_count = isinstance(record_count, int) and not isinstance(record_count, bool) and record_count >= 0
    if source_state == "empty":
        valid_receipt = receipt_status == "success" and valid_count and record_count == 0
    else:
        valid_receipt = receipt_status == "success" and valid_count and record_count > 0
    if not valid_receipt:
        return _result(
            evidence, status="unknown", reason_code="QUERY_RECEIPT_INVALID",
            detail="查询回执与来源状态不一致，当前无法确认结果。",
            observed_at=observed_at, collected_at=collected_at,
            expires_at=expiry,
        )

    if current_time >= expiry:
        return _result(
            evidence, status="stale", reason_code="SOURCE_EXPIRED",
            detail="来源已超过有效期，需要重新核验。",
            observed_at=observed_at, collected_at=collected_at,
            expires_at=expiry,
        )
    if source_state == "stale":
        return _result(
            evidence, status="stale", reason_code="SOURCE_MARKED_STALE",
            detail="来源已标记为过期，需要重新核验。",
            observed_at=observed_at, collected_at=collected_at,
            expires_at=expiry,
        )
    if source_state not in {"available", "empty"}:
        return _result(
            evidence, status="unknown", reason_code="SOURCE_STATE_UNSUPPORTED",
            detail="来源状态无法映射为当前有效事实。",
            observed_at=observed_at, collected_at=collected_at,
            expires_at=expiry,
        )

    return _result(
        evidence, status=source_state, reason_code="SOURCE_CURRENT",
        detail="来源在声明范围内有效。",
        observed_at=observed_at, collected_at=collected_at,
        expires_at=expiry,
    )
