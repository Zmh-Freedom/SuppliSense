"""Pure candidate disposition rules shared by the sourcing review workflow."""

from copy import deepcopy
from typing import Any


def apply_candidate_decisions(candidates: list[dict], decisions: list[dict]) -> dict[str, list[dict[str, Any]]]:
    """Apply one review batch without mutating the supplied candidates or decisions.

    Pending candidates omitted from a batch are deliberately deferred for this
    round. Already exact candidates remain eligible unless an explicit prior
    disposition says otherwise.
    """
    candidate_snapshots = deepcopy(candidates)
    decision_snapshots = deepcopy(decisions)
    by_key: dict[str, dict[str, Any]] = {}
    for candidate in candidate_snapshots:
        key = candidate.get("candidate_key")
        if not isinstance(key, str) or not key.strip():
            raise ValueError("候选缺少稳定 candidate_key")
        if key in by_key:
            raise ValueError(f"candidate_key 重复: {key}")
        by_key[key] = candidate

    decisions_by_key: dict[str, dict[str, Any]] = {}
    for decision in decision_snapshots:
        key = decision.get("candidate_key")
        if not isinstance(key, str) or not key.strip():
            raise ValueError("处置缺少 candidate_key")
        if key in decisions_by_key:
            raise ValueError(f"candidate_key 重复: {key}")
        if key not in by_key:
            raise ValueError(f"candidate_key 不属于当前任务: {key}")
        action = decision.get("action")
        if action not in {"confirm", "defer", "exclude"}:
            raise ValueError("候选处置必须是 confirm、defer 或 exclude")
        if action == "confirm":
            company_id = decision.get("company_id")
            options = candidate_options(by_key[key])
            if not isinstance(company_id, str) or not company_id.strip():
                raise ValueError("确认候选主体必须选择 company_id")
            if company_id not in options:
                raise ValueError("company_id 不在当前候选主体选项中")
        elif not isinstance(decision.get("reason"), str) or not decision["reason"].strip():
            raise ValueError("暂存或排除候选必须填写原因")
        decisions_by_key[key] = decision

    groups: dict[str, list[dict[str, Any]]] = {"eligible": [], "deferred": [], "excluded": []}
    for key, candidate in by_key.items():
        decision = decisions_by_key.get(key)
        prior_action = candidate.get("disposition")
        action = decision.get("action") if decision else prior_action

        if action == "exclude":
            groups["excluded"].append({
                **candidate,
                "disposition": "exclude",
                "reason": (decision or {}).get("reason") or candidate.get("reason"),
            })
            continue
        if action == "defer":
            groups["deferred"].append({
                **candidate,
                "disposition": "defer",
                "reason": (decision or {}).get("reason") or candidate.get("reason"),
            })
            continue

        if decision and decision["action"] == "confirm":
            groups["eligible"].append({
                **candidate,
                "company_id": decision["company_id"],
                "identity_company_id": decision["company_id"],
                "identity_status": "exact",
                "score_eligible": True,
                "identity_review": False,
                "disposition": "confirm",
                "reason": None,
            })
            continue

        if candidate.get("identity_status") == "exact" and candidate.get("company_id"):
            groups["eligible"].append(candidate)
            continue

        groups["deferred"].append({
            **candidate,
            "disposition": "defer",
            "reason": "not_selected_this_round",
        })

    return groups


def candidate_options(candidate: dict[str, Any]) -> set[str]:
    """Return only company IDs explicitly captured as current identity options."""
    options = candidate.get("identity_candidates") or []
    if not isinstance(options, list):
        return set()
    return {
        str(option["company_id"])
        for option in options
        if isinstance(option, dict) and option.get("company_id")
    }
