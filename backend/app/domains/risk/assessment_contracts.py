"""Framework-independent identities and canonical frozen assessment inputs."""

from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime
from enum import Enum
import hashlib
import json
from typing import Any, Iterator
from uuid import UUID


EVIDENCE_STATES = frozenset({"available", "empty", "unavailable", "stale", "conflicting", "not_requested"})
_assessment_context: ContextVar[dict[str, Any] | None] = ContextVar("assessment_context", default=None)


def _json_default(value: Any) -> str:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, Enum):
        return value.value
    raise TypeError(f"Unsupported frozen input type: {type(value).__name__}")


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False, default=_json_default)


def canonical_hash(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode()).hexdigest()


def json_snapshot(value: Any) -> Any:
    return json.loads(canonical_json(value))


@contextmanager
def assessment_scope(**context: Any) -> Iterator[None]:
    token = _assessment_context.set({**(_assessment_context.get() or {}), **context})
    try:
        yield
    finally:
        _assessment_context.reset(token)


def get_assessment_context() -> dict[str, Any]:
    return json_snapshot(_assessment_context.get() or {})


def validate_manifest(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result = json_snapshot(entries)
    seen: set[str] = set()
    for entry in result:
        if not entry.get("evidence_id") or entry["evidence_id"] in seen:
            raise ValueError("证据 ID 缺失或重复")
        seen.add(entry["evidence_id"])
        if entry.get("status") not in EVIDENCE_STATES or not entry.get("dimension") or not entry.get("source"):
            raise ValueError("证据来源、维度或状态无效")
        if entry["status"] == "empty":
            receipt = entry.get("query_receipt") or {}
            if receipt.get("status") != "success" or receipt.get("record_count") != 0:
                raise ValueError("空结果必须具备成功查询回执")
        if entry["status"] in {"available", "empty"}:
            if not entry.get("raw_payload_ref") or len(str(entry.get("content_hash") or "")) != 64 or not entry.get("collected_at"):
                raise ValueError("可用证据必须绑定原文引用、哈希与采集时间")
    return result
