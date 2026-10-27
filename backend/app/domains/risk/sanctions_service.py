"""Evidence-backed sanctions screening and compatible legacy projection."""

from copy import deepcopy
from datetime import datetime, timezone
from typing import Any

from app.domains.risk.assessment_contracts import get_assessment_context
from app.domains.risk.assessment_service import _actor, resolve_identity
from app.domains.risk.evidence_validity import evaluate_evidence_validity
from app.domains.risk import sanctions_sources
from app.domains.supplier.compliance_service import read_domestic_findings


def _utc_now(value: datetime | None) -> datetime:
    if value is None:
        return datetime.now(timezone.utc)
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _empty_analysis(
    company_name: str,
    *,
    now: datetime,
    status: str,
    limitation: str,
    entity_id: str | None = None,
    scope: str | None = None,
    domestic_findings: list[dict] | None = None,
) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "company_name": company_name,
        "entity_id": entity_id,
        "scope": scope,
        "screening_status": status,
        "coverage": {"status": "insufficient", "sources": [], "required_sources": []},
        "matches": [],
        "domestic_findings": domestic_findings or [],
        "limitations": [limitation],
        "checked_at": now.isoformat(),
        "legacy_projection_policy": None,
    }


def _normalized_name(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    return "".join(value.casefold().split())


def _source_failure(contract: dict, identity: dict, reason: str) -> dict:
    return {
        "evidence": {
            "source": contract.get("source"),
            "source_version": contract.get("source_version"),
            "entity_id": identity.get("company_id"),
            "scope": contract.get("scope"),
            "source_mode": "formal",
            "status": "failed",
            "observed_at": None,
            "collected_at": None,
            "expires_at": None,
            "query_receipt": {"status": "failed", "reason": reason},
            "coverage_partial": False,
            "conflict": False,
        },
        "matches": [],
    }


def _record_match(record: dict, identity: dict, contract: dict) -> tuple[str, str] | None:
    allowed_identifiers = contract.get("allowed_match_identifiers") or []
    identifiers = record.get("identifiers") if isinstance(record.get("identifiers"), dict) else {}
    for field in allowed_identifiers:
        expected = identity.get(field)
        actual = identifiers.get(field)
        if expected and actual and str(expected).strip() == str(actual).strip():
            return "confirmed", str(field)

    target = _normalized_name(identity.get("company_name"))
    if not target:
        return None
    names = [record.get("name"), *(record.get("aliases") or [])]
    if any(_normalized_name(candidate) == target for candidate in names):
        return "possible", "name"
    return None


def _legacy_policy(contracts: list[dict]) -> dict | None:
    policies = [
        contract.get("legacy_projection")
        for contract in contracts
        if contract.get("required", True)
    ]
    if not policies or any(not isinstance(policy, dict) for policy in policies):
        return None
    first = policies[0]
    if any(policy != first for policy in policies[1:]):
        return None
    return deepcopy(first)


def _source_validity(
    result: dict,
    contract: dict,
    identity: dict,
    *,
    expected_scope: str,
    now: datetime,
) -> dict:
    evidence = result.get("evidence") if isinstance(result, dict) else None
    if not isinstance(evidence, dict):
        evidence = {
            "source": contract.get("source"),
            "status": "unknown",
            "entity_id": identity.get("company_id"),
            "scope": contract.get("scope"),
        }
    return evaluate_evidence_validity(
        evidence,
        expected_entity_id=str(identity.get("company_id") or ""),
        expected_scope=expected_scope,
        now=now,
    )


def _coverage_status(source_entries: list[dict], required_sources: list[str]) -> str:
    by_source = {item["source"]: item["validity"]["status"] for item in source_entries}
    required_states = [by_source.get(source, "missing") for source in required_sources]
    if required_states and all(state in {"available", "empty"} for state in required_states):
        return "complete"
    if any(state in {"available", "empty"} for state in by_source.values()):
        return "partial"
    return "insufficient"


def _screening_status(
    source_entries: list[dict],
    required_sources: list[str],
    matches: list[dict],
    coverage_status: str,
) -> str:
    current_matches = [item for item in matches if item["validity"]["status"] == "available"]
    if any(item["match_type"] == "confirmed" for item in current_matches):
        return "confirmed_match"
    if current_matches:
        return "possible_match"

    states = [item["validity"]["status"] for item in source_entries]
    if "conflicting" in states:
        return "conflicting"
    if source_entries and all(state == "stale" for state in states):
        return "stale"
    if source_entries and all(
        state == "unknown" and not (item.get("evidence") or {}).get("query_receipt")
        for item, state in zip(source_entries, states)
    ):
        return "not_queried"
    if coverage_status == "complete":
        return "no_match_in_scope"
    if coverage_status == "partial":
        return "partial"
    if not source_entries:
        return "unavailable"
    if all(state in {"missing", "failed"} for state in states):
        return "unavailable"
    if all(state == "missing" for state in states):
        return "unavailable"
    return "unavailable"


def analyze_sanctions(company_name: str, *, now: datetime | None = None) -> dict:
    """Screen one currently authorized, verified company against configured lists."""
    checked_at = _utc_now(now)
    context = get_assessment_context()
    actor_id = context.get("actor_id")
    actor_role = context.get("actor_role")
    if actor_id and not actor_role:
        actor_id, actor_role = _actor(str(actor_id), None)
    if not company_name or not actor_id or not actor_role:
        return _empty_analysis(
            company_name,
            now=checked_at,
            status="unavailable",
            limitation="缺少当前已授权的主体范围，未查询名单来源。",
        )

    identity = resolve_identity(
        company_name,
        company_id=context.get("company_id"),
        monitor_target_id=context.get("monitor_target_id"),
        actor_id=str(actor_id),
        actor_role=str(actor_role),
        dimensions=["compliance"],
        purchase_scope=str(context.get("purchase_scope") or ""),
    )
    entity_id = str(identity.get("company_id") or "")
    canonical_name = str(identity.get("company_name") or company_name)
    scope = str(context.get("scope") or context.get("purchase_scope") or "organization")
    if not entity_id:
        return _empty_analysis(
            canonical_name,
            now=checked_at,
            status="unavailable",
            limitation="未取得已核验的稳定主体标识，未查询名单来源。",
            scope=scope,
        )

    domestic_findings = read_domestic_findings(
        canonical_name,
        entity_id=entity_id,
        scope=scope,
        now=checked_at,
    )
    contracts = [
        contract
        for contract in sanctions_sources.get_sanctions_source_contracts()
        if isinstance(contract, dict) and contract.get("source")
    ]
    if not contracts:
        return _empty_analysis(
            canonical_name,
            now=checked_at,
            status="unavailable",
            limitation="当前未接入经确认的正式名单来源，不能完成筛查。",
            entity_id=entity_id,
            scope=scope,
            domestic_findings=domestic_findings,
        )

    source_entries: list[dict] = []
    all_matches: list[dict] = []
    for contract in contracts:
        try:
            source_result = sanctions_sources.load_sanctions_source(contract, identity)
        except Exception as exc:
            source_result = _source_failure(contract, identity, type(exc).__name__)
        evidence = (source_result.get("evidence") or {}) if isinstance(source_result, dict) else {}
        source_records = source_result.get("matches") if isinstance(source_result, dict) else []
        receipt = evidence.get("query_receipt") if isinstance(evidence, dict) else None
        if (
            isinstance(evidence, dict)
            and evidence.get("status") == "empty"
            and isinstance(receipt, dict)
            and receipt.get("status") == "success"
            and receipt.get("record_count") == 0
            and source_records
        ):
            evidence = {**evidence, "status": "conflicting", "conflict": True}
            source_result = {**source_result, "evidence": evidence}
        validity = _source_validity(
            source_result,
            contract,
            identity,
            expected_scope=str(contract.get("scope") or scope),
            now=checked_at,
        )
        source_entry = {
            "source": contract["source"],
            "list_name": contract.get("list_name"),
            "authority": contract.get("authority"),
            "list_type": contract.get("list_type"),
            "version": evidence.get("source_version") or contract.get("source_version"),
            "scope": contract.get("scope"),
            "required": bool(contract.get("required", True)),
            "validity": validity,
            "evidence": evidence,
        }
        source_entries.append(source_entry)

        if validity.get("status") not in {"available", "stale"}:
            continue
        if evidence.get("source_mode") != "formal":
            continue
        for record in source_records or []:
            if not isinstance(record, dict):
                continue
            match = _record_match(record, identity, contract)
            if not match:
                continue
            match_type, matched_on = match
            all_matches.append(
                {
                    "source": contract["source"],
                    "list_name": contract.get("list_name"),
                    "authority": contract.get("authority"),
                    "list_type": contract.get("list_type"),
                    "source_version": evidence.get("source_version"),
                    "record_id": record.get("record_id"),
                    "record_name": record.get("name"),
                    "match_type": match_type,
                    "matched_on": matched_on,
                    "validity": validity,
                }
            )

    required_sources = [
        {
            "source": contract["source"],
            "list_name": contract.get("list_name"),
            "scope": contract.get("scope"),
        }
        for contract in contracts
        if contract.get("required", True)
    ]
    required_source_ids = [item["source"] for item in required_sources]
    coverage_status = _coverage_status(source_entries, required_source_ids)
    status = _screening_status(source_entries, required_source_ids, all_matches, coverage_status)
    limitations = []
    if status == "no_match_in_scope":
        limitations.append("未命中仅适用于已列明的名单及其声明范围。")
    elif status == "possible_match":
        limitations.append("存在名称线索，主体标识尚未核实。")
    elif status == "confirmed_match":
        limitations.append("确认仅针对所列名单记录；其他名单覆盖情况见来源状态。")
    elif status == "partial":
        limitations.append("部分必需名单来源当前不可用，不能形成完整筛查结论。")
    elif status == "stale":
        limitations.append("现有名单依据已过期，需要重新核验。")
    elif status == "conflicting":
        limitations.append("来源或主体信息存在冲突，需要人工核对。")
    elif status == "not_queried":
        limitations.append("已配置的名单来源尚未完成查询。")
    else:
        limitations.append("当前没有可用的正式名单查询结果。")

    return {
        "schema_version": 1,
        "company_name": canonical_name,
        "entity_id": entity_id,
        "scope": scope,
        "screening_status": status,
        "coverage": {
            "status": coverage_status,
            "sources": [
                {key: value for key, value in item.items() if key != "evidence"}
                for item in source_entries
            ],
            "required_sources": required_sources,
        },
        "matches": all_matches,
        "domestic_findings": domestic_findings,
        "limitations": limitations,
        "checked_at": checked_at.isoformat(),
        "legacy_projection_policy": _legacy_policy(contracts),
    }


def project_legacy_sanctions(analysis: dict) -> dict:
    """Project only explicitly rated, current conclusions to the old response."""
    if not isinstance(analysis, dict):
        raise ValueError("名单筛查结果无效，无法转换为旧响应")
    status = analysis.get("screening_status")
    if status not in {"no_match_in_scope", "confirmed_match"}:
        raise ValueError("当前名单筛查状态无法安全转换为旧响应")
    policy = analysis.get("legacy_projection_policy")
    if not isinstance(policy, dict) or not policy.get("version"):
        raise ValueError("没有已验收的旧版名单评分政策")
    projected = (policy.get("statuses") or {}).get(status)
    if not isinstance(projected, dict):
        raise ValueError("旧版名单评分政策未覆盖当前状态")
    score = projected.get("sanctions_score")
    level = projected.get("sanctions_level")
    if isinstance(score, bool) or not isinstance(score, int) or not isinstance(level, str) or not level:
        raise ValueError("旧版名单评分政策字段无效")
    legacy_matches = []
    if status == "confirmed_match":
        for match in analysis.get("matches") or []:
            if match.get("match_type") != "confirmed" or match.get("validity", {}).get("status") != "available":
                continue
            legacy_matches.append(
                {
                    "type": "entity_match",
                    "name": match.get("record_name"),
                    "program": match.get("list_type"),
                    "authority": match.get("authority"),
                    "match_type": f"确认匹配({match.get('matched_on')})",
                    "level": level,
                }
            )
    if status == "confirmed_match" and not legacy_matches:
        raise ValueError("确认命中缺少可投影的当前名单记录")
    return {
        "company_name": analysis.get("company_name", ""),
        "sanctions_score": score,
        "sanctions_level": level,
        "match_count": len(legacy_matches),
        "matches": legacy_matches,
        "clean": status == "no_match_in_scope",
    }


def check_sanctions(company_name: str) -> dict:
    """Keep the legacy response contract when a trusted projection exists."""
    return project_legacy_sanctions(analyze_sanctions(company_name))


def get_sanctions_dashboard(user_id: str | None = None, user_role: str | None = None) -> dict:
    """Build the legacy dashboard; any non-projectable item fails closed."""
    from app.domains.alert.service import get_watchlist_targets

    targets = (
        get_watchlist_targets()
        if user_id is None and user_role is None
        else get_watchlist_targets(user_id, user_role)
    )
    results = []
    from app.domains.risk.assessment_contracts import assessment_scope

    for target in targets:
        name = target.get("company_name", "")
        with assessment_scope(
            actor_id=user_id,
            actor_role=user_role,
            company_id=target.get("company_id"),
            monitor_target_id=target.get("monitor_target_id"),
            supplier_id=target.get("supplier_id"),
            scope=target.get("purchase_scope") or "",
        ):
            projected = check_sanctions(name)
        results.append({
            "company_name": name,
            "monitor_target_id": target.get("monitor_target_id"),
            "sanctions_score": projected["sanctions_score"],
            "sanctions_level": projected["sanctions_level"],
            "match_count": projected["match_count"],
        })

    companies = [target.get("company_name", "") for target in targets]
    critical = [item for item in results if item["sanctions_level"] == "critical"]
    high = [item for item in results if item["sanctions_level"] == "high"]
    return {
        "total_screened": len(companies),
        "critical_count": len(critical),
        "high_count": len(high),
        "clean_count": sum(1 for item in results if item["sanctions_level"] == "low"),
        "companies": sorted(results, key=lambda item: item["sanctions_score"], reverse=True),
    }
