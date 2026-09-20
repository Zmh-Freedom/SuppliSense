"""Deterministic, isolated fixtures used by the Agent 60 acceptance run."""

from __future__ import annotations

import json
import os
import uuid
from uuid import NAMESPACE_URL, uuid5
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


FIXTURE_DIR = Path(__file__).parent
QUESTIONS_PATH = FIXTURE_DIR / "agent_60_questions.jsonl"
MANIFEST_PATH = FIXTURE_DIR / "agent_60_fixture_manifest.json"
FIXTURE_NAMESPACE = uuid.UUID("8e7a5c1c-14e1-4d6c-8f3c-60aa60000060")


def load_agent_60_questions() -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in QUESTIONS_PATH.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def load_agent_60_manifest() -> dict[str, Any]:
    return json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))


def build_agent_60_recovery_fixture(kind: str, *, session_id: str = "agent60-session") -> dict[str, Any]:
    """Build a disposable durable-state seed without touching live business data."""
    if kind == "paused_review_run":
        return {
            "namespace": "E2E_TEST",
            "session_id": session_id,
            "run_id": "agent60_paused_review_run",
            "status": "paused",
            "resume_command": {"approved": True},
            "pending_action": None,
            "cleanup": True,
        }
    if kind == "paused_identity_run":
        return {
            "namespace": "E2E_TEST",
            "session_id": session_id,
            "run_id": "agent60_paused_identity_run",
            "status": "waiting_approval",
            "resume_command": {"identity_resolutions": {"external-candidate": "company-1"}},
            "pending_action": "identity_review",
            "cleanup": True,
        }
    if kind == "refreshable_run":
        return {
            "namespace": "E2E_TEST",
            "session_id": session_id,
            "run_id": "agent60_refreshable_run",
            "status": "running",
            "last_event_id": 1,
            "resume_command": {"continue": True},
            "pending_action": None,
            "cleanup": True,
        }
    raise KeyError(f"unknown Agent 60 recovery fixture: {kind}")


def stable_agent_60_id(kind: str, value: str) -> str:
    """Return a deterministic UUID for one disposable Agent 60 object."""
    return str(uuid.uuid5(FIXTURE_NAMESPACE, f"{kind}:{value}"))


@dataclass(frozen=True)
class Agent60LiveFixture:
    """Identifiers and cleanup handles for one real three-database run."""

    marker: str
    user_id: str
    session_id: str
    active_run_id: str
    resume_run_id: str
    refreshable_run_id: str
    mongo_marker: str
    redis_key: str
    business_names: tuple[str, ...] = ()
    mongo_ids: tuple[tuple[str, str], ...] = ()
    responsibility_ids: tuple[str, ...] = ()
    company_ids: tuple[str, ...] = ()
    session_ids: tuple[str, ...] = ()
    role_user_ids: tuple[tuple[str, str], ...] = ()


def _business_documents(marker: str, now: datetime) -> dict[str, list[dict[str, Any]]]:
    """Return the real Mongo read-model documents required by Agent 60.

    Every document carries the fixture marker and uses deterministic IDs.  The
    production graph reads these collections directly; a marker-only document
    therefore cannot be used as a substitute for a business fixture.
    """
    names = {
        "complete": "测试-完整数据供应商",
        "risk": "测试-风险供应商",
        "no_transaction": "测试-无交易供应商",
        "no_data": "测试-无数据主体",
        "external": "测试-外部候选",
        "private": "测试-B专属供应商",
        "compare_a": "测试-供应商 A",
        "compare_b": "测试-供应商 B",
    }
    suppliers = []
    master = []
    capabilities = []
    contacts = []
    for key, name in names.items():
        supplier_id = stable_agent_60_id("supplier", f"{marker}:{key}")
        supplier_code = f"{marker[:12]}-{key}"
        categories = ["工业相机"] if key in {"complete", "risk", "compare_a", "compare_b"} else []
        suppliers.append({
            "_id": supplier_id,
            "supplier_id": supplier_id,
            "supplier_code": supplier_code,
            "name": name,
            "status": "active",
            "source": "agent60_fixture",
            "fixture_marker": marker,
            "categories": categories,
            "created_at": now,
            "updated_at": now,
        })
        master.append({
            "_id": stable_agent_60_id("master", f"{marker}:{key}"),
            "supplier_id": supplier_id,
            "supplier_code": supplier_code,
            "name": name,
            "status": "active",
            "source": "feishu_bitable",
            "sync_status": "current",
            "source_record_id": f"{marker}:master:{key}",
            "fixture_marker": marker,
            "categories": categories,
            "regions": ["华东"] if key in {"complete", "risk"} else [],
            "unified_code": f"9132{abs(hash(supplier_id)) % 10**14:014d}",
            "reg_status": "存续",
            "synced_at": now,
        })
        if key in {"complete", "risk", "compare_a", "compare_b"}:
            capabilities.append({
                "_id": stable_agent_60_id("capability", f"{marker}:{key}"),
                "supplier_id": supplier_id,
                "supplier_code": supplier_code,
                "source": "feishu_bitable",
                "sync_status": "current",
                "source_record_id": f"{marker}:capability:{key}",
                "fixture_marker": marker,
                "category": "工业相机",
                "product_name": "4K工业相机模组",
                "product_keywords": ["工业相机", "GigE"],
                "supply_regions": ["华东"],
                "qualifications": "ISO9001",
                "capability_status": "已验证",
            })
            contacts.append({
                "_id": stable_agent_60_id("contact", f"{marker}:{key}"),
                "supplier_id": supplier_id,
                "supplier_code": supplier_code,
                "source": "feishu_bitable",
                "sync_status": "current",
                "source_record_id": f"{marker}:contact:{key}",
                "fixture_marker": marker,
                "contact_name": "Agent 60 测试联系人",
                "phone": "400-060-0060",
                "email": f"{key}@agent60.example.test",
                "is_primary_contact": True,
            })

    # Five deterministic sourcing rows make the local discovery path complete
    # without invoking an external provider.
    for index, category in enumerate(("制动系统",) * 5 + ("工业相机",)):
        key = f"candidate-{index}"
        supplier_id = stable_agent_60_id("candidate", f"{marker}:{key}")
        supplier_code = f"{marker[:10]}-C{index}"
        name = f"Agent60-{category}-{index + 1}供应商"
        master.append({
            "_id": stable_agent_60_id("candidate-master", f"{marker}:{key}"),
            "supplier_id": supplier_id,
            "supplier_code": supplier_code,
            "name": name,
            "status": "active",
            "source": "feishu_bitable",
            "sync_status": "current",
            "source_record_id": f"{marker}:candidate:{index}",
            "fixture_marker": marker,
            "categories": [category],
            "regions": ["华东"],
            "synced_at": now,
        })
        capabilities.append({
            "_id": stable_agent_60_id("candidate-capability", f"{marker}:{key}"),
            "supplier_id": supplier_id,
            "supplier_code": supplier_code,
            "source": "feishu_bitable",
            "sync_status": "current",
            "source_record_id": f"{marker}:candidate-capability:{index}",
            "fixture_marker": marker,
            "category": category,
            "product_name": f"{category}测试产品",
            "product_keywords": [category],
            "supply_regions": ["华东"],
            "qualifications": "ISO9001",
            "capability_status": "已验证",
        })

    return {
        "suppliers": suppliers,
        "supplier_master_snapshots": master,
        "supplier_capability_snapshots": capabilities,
        "supplier_contact_snapshots": contacts,
    }


def _guard_business_name_collisions(db: Any, documents: dict[str, list[dict[str, Any]]]) -> None:
    """Fail closed if a fixed acceptance name belongs to non-fixture data."""
    collection_names = {
        "suppliers": {str(doc.get("name")) for doc in documents.get("suppliers", []) if doc.get("name")},
        "supplier_master_snapshots": {str(doc.get("name")) for doc in documents.get("supplier_master_snapshots", []) if doc.get("name")},
    }
    for collection_name, names in collection_names.items():
        existing = list(db[collection_name].find({"name": {"$in": sorted(names)}}).limit(1))
        if existing and not existing[0].get("fixture_marker"):
            raise RuntimeError(
                f"Agent 60 fixture name collision in {collection_name}: {existing[0].get('name')}"
            )


def seed_agent_60_live_fixture(*, user_id: str | None = None) -> Agent60LiveFixture:
    """Seed only namespaced control-plane data for live HTTP/SSE tests.

    The caller supplies an existing authenticated PostgreSQL user.  This
    avoids creating credentials in the test runner while still exercising the
    real session, turn, run, interrupt, Mongo and Redis persistence seams.
    """
    from app.core.cache import cache_client
    from app.db.init_pg import ensure_pg_schema
    from app.db.mongo import get_db
    from app.db.postgres import get_cursor
    from app.domains.agent_run.state_store import session_state_store

    ensure_pg_schema()
    resolved_user_id = str(user_id or os.getenv("AGENT_60_USER_ID") or "").strip()
    if not resolved_user_id:
        raise RuntimeError("AGENT_60_USER_ID is required for the live fixture")
    marker = f"agent60_{uuid.uuid4().hex}"
    session_id = stable_agent_60_id("session", marker)
    active_run_id = stable_agent_60_id("run", f"{marker}:active")
    resume_run_id = stable_agent_60_id("run", f"{marker}:resume")
    refreshable_run_id = stable_agent_60_id("run", f"{marker}:refreshable")
    now = datetime.now(timezone.utc)
    buyer_a_user_id = stable_agent_60_id("user", f"{marker}:buyer_a")

    session_state_store.create_session(resolved_user_id, session_id=session_id)
    with get_cursor() as (_, cur):
        cur.execute(
            """
            INSERT INTO users (id, username, email, password_hash, role)
            VALUES (%s::uuid, %s, %s, %s, 'purchaser')
            ON CONFLICT (id) DO NOTHING
            """,
            (buyer_a_user_id, f"{marker}_buyer_a", f"{marker}_buyer_a@example.test", "agent60-fixture"),
        )
        cur.execute(
            """
            INSERT INTO agent_turns (id, session_id, turn_number, user_message, status, request)
            VALUES (%s, %s, 1, %s, 'running', %s)
            RETURNING id
            """,
            (stable_agent_60_id("turn", marker), session_id, "Agent 60 durable recovery", json.dumps({"fixture": marker})),
        )
        turn_id = str(cur.fetchone()[0])
        for run_id, status in (
            (refreshable_run_id, "COMPLETED"),
            (resume_run_id, "ACTION_PENDING"),
            (active_run_id, "RUNNING"),
        ):
            cur.execute(
                """
                INSERT INTO agent_runs
                    (id, session_id, run_type, user_id, status, requirement, version, completed_at)
                VALUES (%s, %s, 'agent_harness', %s, %s, %s, 1, %s)
                """,
                (
                    run_id,
                    session_id,
                    resolved_user_id,
                    status,
                    json.dumps({"fixture": marker, "session_id": session_id, "run_id": run_id}),
                    now if status == "COMPLETED" else None,
                ),
            )
            cur.execute(
                """
                INSERT INTO agent_run_events (run_id, event_id, version, event_type, payload)
                VALUES (%s, 1, %s, %s, %s)
                """,
                (
                    run_id,
                    1,
                    "done" if status == "COMPLETED" else "workflow_status",
                    json.dumps({"status": status.lower(), "fixture": marker}),
                ),
            )
        cur.execute(
            """
            INSERT INTO agent_chat_interrupts
                (session_id, user_id, config, mode, user_message, status, claim_token, claimed_at)
            VALUES (%s, %s, %s, 'agent-supervisor', %s, 'pending', NULL, NULL)
            ON CONFLICT (session_id) DO UPDATE SET
                user_id = EXCLUDED.user_id,
                config = EXCLUDED.config,
                mode = EXCLUDED.mode,
                user_message = EXCLUDED.user_message,
                status = 'pending', claim_token = NULL, claimed_at = NULL,
                updated_at = NOW()
            """,
            (
                session_id,
                resolved_user_id,
                json.dumps({
                    "configurable": {"thread_id": session_id, "run_id": resume_run_id},
                    "__resume_value": {"approved": True},
                }),
                "Agent 60 durable identity recovery",
            ),
        )
        cur.execute(
            "UPDATE agent_turns SET run_id = %s WHERE id = %s",
            (active_run_id, turn_id),
        )

    mongo_marker = f"{marker}:mongo"
    db = get_db()
    business_documents = _business_documents(marker, now)
    _guard_business_name_collisions(db, business_documents)
    for collection_name, documents in business_documents.items():
        if documents:
            db[collection_name].insert_many(documents, ordered=True)

    # The formal-supplier scope is PostgreSQL-backed.  Seed responsibility
    # rows for the supplied user rather than weakening the production access
    # check or relying on Mongo-only ownership fields.
    responsibility_ids: list[str] = []
    company_ids: list[str] = []
    with get_cursor() as (_, cur):
        cur.execute("SELECT feishu_open_id FROM users WHERE id = %s::uuid", (resolved_user_id,))
        feishu_open_id = cur.fetchone()
        purchaser_open_id = str(feishu_open_id[0]) if feishu_open_id and feishu_open_id[0] else f"agent60:{resolved_user_id}"
        for index, master in enumerate(business_documents["supplier_master_snapshots"]):
            source_record_id = f"{marker}:responsibility:{index}"
            responsibility_ids.append(source_record_id)
            cur.execute(
                """
                INSERT INTO supplier_responsibility_snapshots
                    (source_record_id, supplier_code, supplier_id, supplier_name,
                     source_active, department_code, department_name,
                     purchaser_open_id, purchaser_name, manager_open_id, manager_name,
                     sync_status, sync_batch_id, synced_at)
                VALUES (%s, %s, %s, %s, TRUE, 'E2E_TEST', 'Agent 60 测试部门',
                        %s, 'Agent 60 测试采购员', %s, 'Agent 60 测试经理',
                        'current', %s, %s)
                ON CONFLICT (source_record_id) DO UPDATE SET
                    source_active = TRUE, sync_status = 'current', supplier_id = EXCLUDED.supplier_id,
                    supplier_name = EXCLUDED.supplier_name, purchaser_open_id = EXCLUDED.purchaser_open_id
                """,
                (source_record_id, master.get("supplier_code"), master.get("supplier_id"), master.get("name"),
                 purchaser_open_id, purchaser_open_id, str(uuid.uuid4()), now),
            )
        identity_company_id = stable_agent_60_id("company", f"{marker}:identity-code")
        company_ids.append(identity_company_id)
        cur.execute(
            """
            INSERT INTO companies
                (id, legal_name, normalized_name, unified_social_credit_code,
                 registration_status, verification_status, identity_source,
                 source_reference, created_by, verified_by)
            VALUES (%s, '测试-统一社会信用代码主体', '测试-统一社会信用代码主体',
                    '913200000000006060', '存续', 'verified', 'import', %s, %s::uuid, %s::uuid)
            ON CONFLICT (id) DO NOTHING
            """,
            (identity_company_id, f"{marker}:identity-code", resolved_user_id, resolved_user_id),
        )

    # Risk/profile read models consumed by the Harness tools.
    supplier_by_key = {
        doc["name"]: doc for doc in business_documents["supplier_master_snapshots"]
        if doc.get("name") in {"测试-完整数据供应商", "测试-风险供应商", "测试-无交易供应商", "测试-无数据主体"}
    }
    baseinfo_documents = [
        {
            "_id": stable_agent_60_id("baseinfo", f"{marker}:complete"),
            "name": "测试-完整数据供应商",
            "fixture_marker": marker,
            "items": {"result": {"legalPersonName": "Agent60法人", "regCapital": "1000万", "industry": "制造业", "website": "https://agent60.example.test", "phone": "400-060-0060", "email": "complete@agent60.example.test"}},
            "updated_at": now,
        },
        {
            "_id": stable_agent_60_id("baseinfo", f"{marker}:risk"),
            "name": "测试-风险供应商",
            "fixture_marker": marker,
            "items": {"result": {"legalPersonName": "Agent60风险法人", "regCapital": "500万", "industry": "制造业", "website": "https://risk.agent60.example.test", "phone": "400-060-0061"}},
            "updated_at": now,
        },
        {
            "_id": stable_agent_60_id("baseinfo", f"{marker}:external"),
            "name": "测试-外部候选",
            "fixture_marker": marker,
            "items": {"result": {"legalPersonName": "Agent60外部法人", "regStatus": "存续", "regNumber": "913206000000006060", "creditCode": "913206000000006060", "industry": "制造业"}},
            "updated_at": now,
        },
    ]
    for document in baseinfo_documents:
        existing = db["baseinfo"].find_one({"name": document["name"]})
        if existing is None:
            db["baseinfo"].insert_one(document)
        elif existing.get("fixture_marker") not in (None, marker):
            raise RuntimeError(f"Agent 60 fixture baseinfo collision: {document['name']}")
    risk_list = [{"title": "裁判文书", "total": 2}, {"title": "行政处罚", "total": 1}, {"title": "经营异常", "total": 0}]
    for name, score, level in (("测试-完整数据供应商", 82, "低风险"), ("测试-风险供应商", 38, "高风险")):
        db["riskInfo"].insert_one({
            "_id": stable_agent_60_id("risk-info", f"{marker}:{name}"),
            "name": name,
            "fixture_marker": marker,
            "item": {"result": {"riskList": [{"title": "司法与合规", "list": risk_list}]}},
        })
        db["lawSuit"].insert_one({"_id": stable_agent_60_id("lawsuit", f"{marker}:{name}"), "name": name, "fixture_marker": marker, "items": {"result": {"total": 2}}})
        db["punishmentInfo"].insert_one({"_id": stable_agent_60_id("punishment", f"{marker}:{name}"), "name": name, "fixture_marker": marker, "items": {"result": {"total": 1}}})
        for collection_name in ("courtRegister", "executedPerson", "dishonesty", "courtAnnouncement", "consumptionRestriction", "abnormal", "equityPledge", "taxArrears"):
            db[collection_name].insert_one({
                "_id": stable_agent_60_id(collection_name, f"{marker}:{name}"),
                "name": name, "fixture_marker": marker, "items": {"result": {"total": 0}},
            })
        db["financial_cache"].insert_one({
            "_id": stable_agent_60_id("financial", f"{marker}:{name}"),
            "name": name, "fixture_marker": marker,
            "metrics": {"revenue_growth": 0.12 if score > 50 else -0.18, "net_profit_growth": 0.08, "debt_ratio": 0.42 if score > 50 else 0.78, "cash_flow": 120000, "roe": 0.11, "net_profit_margin": 0.08, "current_ratio": 1.4, "quick_ratio": 1.1},
            "history": [{"period": "2025", "revenue": 1000000, "net_profit": 80000, "debt_ratio": 0.42}],
            "cached_at": now,
        })
        db["sentiment_results"].insert_one({
            "_id": stable_agent_60_id("sentiment", f"{marker}:{name}"),
            "company_name": name, "fixture_marker": marker, "overall_sentiment": "正面" if score > 50 else "负面", "sentiment_score": score - 50, "analyzed_at": now,
        })
    for name in ("测试-完整数据供应商", "测试-风险供应商"):
        supplier = supplier_by_key[name]
        db["supplier_transaction_snapshots"].insert_one({
            "_id": stable_agent_60_id("transaction", f"{marker}:{name}"),
            "supplier_id": supplier["supplier_id"], "supplier_code": supplier["supplier_code"], "fixture_marker": marker,
            "source": "feishu_bitable", "source_record_id": f"{marker}:transaction:{name}", "sync_status": "current", "data_mode": "real", "eligible_for_formal_assessment": True,
            "data_quality_status": "valid", "snapshot_month": "2026-08", "purchasing_org_code": "E2E_TEST", "category_code": "CAM",
            "received_amount": 100000 if name == "测试-完整数据供应商" else 600000, "received_qty": 100, "unit_price": 1000,
            "actual_settlement_amount": 100000, "unsettled_amount": 10000, "contract_status": "active",
        })
    # Two alert snapshots make the trend and monitor summary deterministic.
    # Start only the risk supplier as monitored. Q27 adds the complete
    # supplier first; Q28 then makes it active before Q30 removes it.
    for name, score, monitor_id in (("测试-风险供应商", 38, stable_agent_60_id("monitor", f"{marker}:risk")),):
        supplier = supplier_by_key[name]
        db["watchlist"].insert_one({
            "_id": stable_agent_60_id("watchlist", f"{marker}:{name}"), "monitor_target_id": monitor_id,
            "company_name": name, "display_name": name, "supplier_id": supplier["supplier_id"], "supplier_code": supplier["supplier_code"],
            "target_type": "formal_supplier", "identity_status": "verified", "monitor_status": "active", "is_responsible_supplier": True,
            "responsibility_status": "assigned", "fixture_marker": marker, "added_at": now,
        })
        for version, snapshot_score in ((1, score - 8), (2, score)):
            db["alert_snapshots"].insert_one({
                "_id": stable_agent_60_id("alert", f"{marker}:{name}:{version}"), "monitor_target_id": monitor_id,
                "company_name": name, "fixture_marker": marker, "snapshot_id": f"{marker}:{name}:{version}", "snapshot_version": version,
                "checked_at": now, "risk_score": snapshot_score, "risk_level": "高风险" if snapshot_score < 50 else "低风险",
                "risk_detail": {"data_coverage": {"available_dimensions": ["financial", "business_risk", "sentiment", "compliance"]}},
                "financial": {"revenue_growth": 0.1}, "scoring_version": "agent60",
            })
    db["agent60_fixture"].insert_one({"_id": mongo_marker, "namespace": "E2E_TEST", "marker": marker, "created_at": now})
    redis_key = f"E2E_TEST:{marker}:redis"
    cache_client.set(redis_key, marker, ex=900)
    session_ids = tuple(
        str(uuid5(NAMESPACE_URL, f"agent60:{marker}:{session}"))
        for session in {item["session"] for item in load_agent_60_questions()}
    )
    return Agent60LiveFixture(
        marker=marker,
        user_id=resolved_user_id,
        session_id=session_id,
        active_run_id=active_run_id,
        resume_run_id=resume_run_id,
        refreshable_run_id=refreshable_run_id,
        mongo_marker=mongo_marker,
        redis_key=redis_key,
        business_names=tuple(doc["name"] for doc in business_documents["suppliers"]),
        mongo_ids=tuple(
            (collection_name, str(doc["_id"]))
            for collection_name, documents in business_documents.items()
            for doc in documents
        ) + tuple(
            (collection_name, str(doc["_id"]))
            for collection_name in ("baseinfo", "riskInfo", "lawSuit", "punishmentInfo", "courtRegister", "executedPerson", "dishonesty", "courtAnnouncement", "consumptionRestriction", "abnormal", "equityPledge", "taxArrears", "financial_cache", "sentiment_results", "supplier_transaction_snapshots", "watchlist", "alert_snapshots")
            for doc in db[collection_name].find({"fixture_marker": marker})
        ),
        responsibility_ids=tuple(responsibility_ids),
        company_ids=tuple(company_ids),
        session_ids=session_ids,
        role_user_ids=(("buyer_a", buyer_a_user_id),),
    )


def teardown_agent_60_live_fixture(fixture: Agent60LiveFixture) -> None:
    """Delete only the exact disposable rows created by the fixture."""
    from app.core.cache import cache_client
    from app.db.mongo import get_db
    from app.db.postgres import get_cursor

    db = get_db()
    cache_client.delete(fixture.redis_key)
    for collection_name, document_id in fixture.mongo_ids:
        db[collection_name].delete_one({"_id": document_id, "fixture_marker": fixture.marker})
    db["agent60_fixture"].delete_one({"_id": fixture.mongo_marker})
    with get_cursor() as (_, cur):
        for source_record_id in fixture.responsibility_ids:
            cur.execute("DELETE FROM supplier_responsibility_snapshots WHERE source_record_id = %s", (source_record_id,))
        for company_id in fixture.company_ids:
            cur.execute("DELETE FROM companies WHERE id = %s::uuid", (company_id,))
        for _, role_user_id in fixture.role_user_ids:
            cur.execute("DELETE FROM users WHERE id = %s::uuid", (role_user_id,))
        cur.execute("DELETE FROM agent_chat_interrupts WHERE session_id = %s", (fixture.session_id,))
        cur.execute("DELETE FROM agent_sessions WHERE id = %s AND user_id = %s", (fixture.session_id, fixture.user_id))
        for session_id in fixture.session_ids:
            if session_id != fixture.session_id:
                cur.execute("DELETE FROM agent_chat_interrupts WHERE session_id = %s", (session_id,))
                cur.execute("DELETE FROM agent_sessions WHERE id = %s AND user_id = %s", (session_id, fixture.user_id))


__all__ = [
    "Agent60LiveFixture",
    "build_agent_60_recovery_fixture",
    "load_agent_60_manifest",
    "load_agent_60_questions",
    "seed_agent_60_live_fixture",
    "stable_agent_60_id",
    "teardown_agent_60_live_fixture",
]
