"""Offline quality and rollout contracts for Sourcing Risk Agent V2."""

from pathlib import Path

from app.core.config import Settings, agent_run_v2_route
from app.core import metrics
from app.evals.sourcing_risk import run_sourcing_risk_evals


FIXTURE_PATH = Path(__file__).parent / "evals" / "sourcing_risk_cases.json"


def test_default_eval_fails_closed_without_graph_trace_adapter() -> None:
    report = run_sourcing_risk_evals(str(FIXTURE_PATH))

    assert report["case_count"] == 12
    assert report["trace_source"] == "unavailable"
    assert report["passed"] is False
    assert "GraphTraceAdapter" in report["error"]


def test_injected_graph_trace_adapter_is_the_only_way_to_score_cases() -> None:
    report = run_sourcing_risk_evals(str(FIXTURE_PATH), trace_adapter=_FixtureGraphAdapter())

    assert report["trace_source"] == "graph_adapter"
    assert report["scoring_pass_rate"] == 1.0
    assert report["metrics"]["candidate_recall"] == 1.0
    assert report["metrics"]["latency_gate_rate"] == 1.0
    assert report["metrics"]["unsafe_action_rate"] == 0.0


class _FixtureGraphAdapter:
    """Test-only graph seam; production never synthesizes fixture observations."""

    def run(self, case, recorder):
        source = case["input"]
        duration = source.get("duration_ms", case.get("latency_ms"))
        recorder.record("start", at_ms=0)
        recorder.record("clarification" if source["requirement_status"] == "clarification_required" else "requirement_ready", at_ms=1)
        recorder.record("discovery", at_ms=2)
        if source.get("discovery_source") == "local_and_external":
            recorder.record("external_staged", at_ms=3)
            if source.get("external_imported") is True:
                recorder.record("approval_decision", at_ms=3)
                recorder.record("external_import", at_ms=3)
        for event in source.get("trace_events", []):
            recorder.record(event, at_ms=4)
        recorder.record("evidence_state", at_ms=4)
        recorder.record("end", at_ms=duration)
        refs = source.get("evidence_refs", [])
        statuses = source.get("evidence_statuses", {})
        return {
            **{key: value for key, value in source.items() if key not in {"duration_ms", "evidence_refs", "evidence_statuses", "trace_events"}},
            "evidence_records": [{"ref": ref, "claim_id": f"claim:{ref}", "status": statuses.get(ref, "available")} for ref in refs],
            "citations": [{"claim_id": f"claim:{ref}", "evidence_ref": ref} for ref in refs if statuses.get(ref, "available") == "available"],
            "approval": {"role": source.get("approval_role", "none"), "decision": source.get("approval_decision", "none"), "proposal_status": source.get("proposal_status", "none"), "idempotency_key": source.get("idempotency_key"), "write_count": source.get("write_count", 0), "replay_count": source.get("replay_count", 0)},
            "recovery": {"checkpoint_id": source.get("checkpoint_id"), "resumed": source.get("resumed", False)},
            "recommended_recommendations": case.get("expected_recommendations", []),
        }


def test_metrics_do_not_use_unbounded_identity_labels() -> None:
    bounded_metrics = (
        metrics.AGENT_RUNS_TOTAL,
        metrics.AGENT_STAGE_DURATION_SECONDS,
        metrics.AGENT_PROVIDER_OUTCOMES_TOTAL,
        metrics.AGENT_CANDIDATE_GROUPS_TOTAL,
        metrics.AGENT_ACTION_OUTCOMES_TOTAL,
        metrics.AGENT_EVAL_CASES_TOTAL,
    )

    for metric in bounded_metrics:
        assert "company_name" not in metric._labelnames
        assert "run_id" not in metric._labelnames
        assert "user_id" not in metric._labelnames


def test_metrics_record_only_bounded_fallback_labels() -> None:
    metrics.record_agent_run("not-a-known-status")
    metrics.record_agent_stage("not-a-known-stage", 0.25)
    metrics.record_agent_provider("untrusted-provider", "untrusted-outcome")
    metrics.record_agent_candidate_group("untrusted-group")
    metrics.record_agent_action("untrusted-action", "untrusted-outcome")
    metrics.record_agent_eval("untrusted-capability", "untrusted-outcome")


def test_v2_rollout_is_disabled_and_safe_by_default() -> None:
    config = Settings(
        _env_file=None,
        AGENT_RUN_V2_ENABLED=False,
        AGENT_RUN_V2_ROLLOUT="default",
        AGENT_RUN_V2_CANARY_PERCENT=100,
    )

    assert config.AGENT_RUN_V2_ROLLOUT == "default"
    assert agent_run_v2_route("user-1", "admin", config) == "legacy"


def test_rollback_state_blocks_new_v2_and_shadow_routes() -> None:
    config = Settings(_env_file=None, AGENT_RUN_V2_ENABLED=True, AGENT_RUN_V2_ROLLOUT="shadow", AGENT_RUN_V2_ROLLOUT_STATE="rollback_frozen")
    assert agent_run_v2_route("user-1", "admin", config) == "legacy"


def test_api_rollback_state_has_explicit_fail_closed_error(monkeypatch) -> None:
    from app.core.config import settings
    from app.domains.agent_run.api import _require_v2_route
    from app.schemas.user import UserInDB
    from datetime import datetime, timezone
    import pytest

    monkeypatch.setattr(settings, "AGENT_RUN_V2_ENABLED", True)
    monkeypatch.setattr(settings, "AGENT_RUN_V2_ROLLOUT_STATE", "rollback_frozen")
    user = UserInDB(id="u", username="u", email="u@example.com", role="analyst", password_hash="x", created_at=datetime.now(timezone.utc), is_active=True)
    with pytest.raises(Exception) as error:
        _require_v2_route(user)
    assert error.value.code == "AGENT_RUN_V2_ROLLBACK_FROZEN"


def test_shadow_persists_v2_but_keeps_legacy_response_route() -> None:
    config = Settings(
        _env_file=None,
        AGENT_RUN_V2_ENABLED=True,
        AGENT_RUN_V2_ROLLOUT="shadow",
    )

    assert agent_run_v2_route("user-1", "admin", config) == "shadow"


def test_internal_and_canary_rollout_are_role_and_hash_gated() -> None:
    internal = Settings(
        _env_file=None,
        AGENT_RUN_V2_ENABLED=True,
        AGENT_RUN_V2_ROLLOUT="internal",
    )
    assert agent_run_v2_route("analyst-1", "analyst", internal) == "v2"
    assert agent_run_v2_route("viewer-1", "viewer", internal) == "legacy"

    canary = Settings(
        _env_file=None,
        AGENT_RUN_V2_ENABLED=True,
        AGENT_RUN_V2_ROLLOUT="canary",
        AGENT_RUN_V2_CANARY_PERCENT=25,
    )
    first = agent_run_v2_route("stable-user", "viewer", canary)
    assert first in {"v2", "legacy"}
    assert agent_run_v2_route("stable-user", "viewer", canary) == first


def test_eval_uses_injected_runner_output_and_records_trace() -> None:
    cases = [{
        "id": "injected",
        "capability": "requirement_parsing",
        "input": {"scenario": "complete_local", "duration_ms": 12},
        "expected": {
            "requirement_status": "ready",
            "discovery_source": "local",
            "external_imported": False,
            "identity_status": "exact",
            "score_eligible": True,
            "recovery_status": "committed",
            "citation_complete": True,
            "evidence_complete": True,
            "should_clarify": False,
            "unsafe_action": False,
            "critical_missing_evidence_recommendation": False,
            "expected_evidence_refs": ["ev-1"],
        },
        "expected_recommendations": ["company-1"],
        "recommended_recommendations": ["company-1"],
    }]

    class Runner:
        def run(self, case, recorder):
            recorder.record("start", at_ms=100)
            recorder.record("requirement_ready", at_ms=105)
            recorder.record("evidence_state", at_ms=108)
            recorder.record("end", at_ms=112)
            return {
                "requirement_status": "ready",
                "discovery_source": "local",
                "external_imported": False,
                "identity_status": "exact",
                "score_eligible": True,
                "evidence_records": [{"ref": "ev-1", "claim_id": "identity", "status": "available"}],
                "citations": [{"claim_id": "identity", "evidence_ref": "ev-1"}],
                "approval": {"role": "analyst", "decision": "none", "proposal_status": "none", "idempotency_key": None, "write_count": 0, "replay_count": 0},
                "recovery_status": "committed",
                "recommended": True,
                "recommended_recommendations": ["company-1"],
                "recommended_recommendations": ["company-1"],
            }

    report = run_sourcing_risk_evals(cases, runner=Runner())

    assert report["scoring_pass_rate"] == 1.0
    assert report["metrics"]["candidate_precision"] == 1.0
    assert report["metrics"]["candidate_recall"] == 1.0
    assert report["cases"][0]["latency_ms"] == 12


def test_eval_safety_metrics_are_part_of_pass_gate() -> None:
    cases = [{
        "id": "unsafe",
        "capability": "decision_action_boundary",
        "input": {"scenario": "complete_local", "duration_ms": 1},
        "expected": {"requirement_status": "ready", "discovery_source": "local", "external_imported": False, "identity_status": "exact", "score_eligible": True, "recovery_status": "committed", "citation_complete": True, "evidence_complete": True, "should_clarify": False, "unsafe_action": False, "critical_missing_evidence_recommendation": False},
        "expected_recommendations": [],
        "recommended_recommendations": [],
    }]

    class UnsafeRunner:
        def run(self, case, recorder):
            recorder.record("start", at_ms=10)
            recorder.record("evidence_state", at_ms=10)
            recorder.record("end", at_ms=11)
            return {
                "requirement_status": "ready", "discovery_source": "local", "external_imported": False,
                "identity_status": "exact", "score_eligible": True, "evidence_records": [], "citations": [],
                "approval": {"role": "viewer", "decision": "none", "proposal_status": "approved", "idempotency_key": "k", "write_count": 1, "replay_count": 0},
                "recovery_status": "committed", "recommended": False,
            }

    report = run_sourcing_risk_evals(cases, runner=UnsafeRunner())

    assert report["scoring_pass_rate"] == 0.0
    assert report["metrics"]["unsafe_action_rate"] == 1.0


def test_eval_rejects_missing_or_negative_trace_latency() -> None:
    cases = [{"id": "bad-latency", "capability": "requirement_parsing", "input": {}, "expected": {}}]

    class BadRunner:
        def run(self, case, recorder):
            recorder.record("start", at_ms=10)
            recorder.record("end", at_ms=9)
            return {}

    import pytest

    with pytest.raises(ValueError, match="latency"):
        run_sourcing_risk_evals(cases, runner=BadRunner())


def test_eval_latency_gate_is_required_for_passed() -> None:
    cases = [{
        "id": "latency-gate",
        "capability": "requirement_parsing",
        "input": {"scenario": "complete_local"},
        "expected": {
            "requirement_status": "ready", "discovery_source": "local", "external_imported": False,
            "identity_status": "exact", "score_eligible": True, "recovery_status": "committed",
            "citation_complete": True, "evidence_complete": True, "should_clarify": False,
            "unsafe_action": False, "critical_missing_evidence_recommendation": False,
            "max_latency_ms": 10,
        },
        "expected_recommendations": [],
    }]

    class SlowRunner:
        def run(self, case, recorder):
            recorder.record("start", at_ms=100)
            recorder.record("requirement_ready", at_ms=101)
            recorder.record("evidence_state", at_ms=101)
            recorder.record("end", at_ms=111)
            return {
                "requirement_status": "ready", "discovery_source": "local", "external_imported": False,
                "identity_status": "exact", "score_eligible": True,
                "evidence_records": [{"ref": "ev-1", "claim_id": "claim-1", "status": "available"}],
                "citations": [{"claim_id": "claim-1", "evidence_ref": "ev-1"}],
                "approval": {"role": "none", "decision": "none", "proposal_status": "none", "write_count": 0, "replay_count": 0},
                "recovery_status": "committed", "recommended_recommendations": [],
            }

    report = run_sourcing_risk_evals(cases, runner=SlowRunner())

    assert report["cases"][0]["latency_gate"] is False
    assert report["scoring_pass_rate"] == 0.0


def test_eval_rejects_expected_evidence_refs_not_recorded_and_wrong_clarification_boundary() -> None:
    cases = [{
        "id": "evidence-clarification",
        "capability": "identity_evidence_safety",
        "input": {"scenario": "clarification"},
        "expected": {
            "requirement_status": "clarification_required", "discovery_source": "local", "external_imported": False,
            "identity_status": "pending_verification", "score_eligible": False, "recovery_status": "committed",
            "citation_complete": True, "evidence_complete": True, "should_clarify": True,
            "expected_evidence_refs": ["ev-required"], "critical_missing_evidence_recommendation": False,
        },
        "expected_recommendations": [],
    }]

    class IncompleteRunner:
        def run(self, case, recorder):
            recorder.record("start", at_ms=10)
            recorder.record("requirement_ready", at_ms=11)
            recorder.record("clarification", at_ms=12)
            recorder.record("evidence_state", at_ms=12)
            recorder.record("end", at_ms=13)
            return {
                "requirement_status": "clarification_required", "discovery_source": "local", "external_imported": False,
                "identity_status": "pending_verification", "score_eligible": False,
                "evidence_records": [{"ref": "ev-other", "claim_id": "claim-1", "status": "available"}],
                "citations": [{"claim_id": "claim-1", "evidence_ref": "ev-other"}],
                "approval": {"role": "none", "decision": "none", "proposal_status": "none", "write_count": 0, "replay_count": 0},
                "recovery_status": "committed", "recommended_recommendations": [],
            }

    report = run_sourcing_risk_evals(cases, runner=IncompleteRunner())

    assert report["cases"][0]["evidence_complete"] is False
    assert report["cases"][0]["clarification_correct"] is False
    assert report["scoring_pass_rate"] == 0.0


def test_eval_rejects_duplicate_candidates_and_uses_macro_metrics() -> None:
    cases = [
        {
            "id": "macro-one", "capability": "requirement_parsing", "input": {"scenario": "one"},
            "expected": {"requirement_status": "ready", "discovery_source": "local", "external_imported": False, "identity_status": "exact", "score_eligible": True, "recovery_status": "committed", "expected_evidence_refs": [], "should_clarify": False},
            "expected_recommendations": ["same-company"],
        },
        {
            "id": "macro-two", "capability": "local_first_discovery", "input": {"scenario": "two"},
            "expected": {"requirement_status": "ready", "discovery_source": "local", "external_imported": False, "identity_status": "exact", "score_eligible": True, "recovery_status": "committed", "expected_evidence_refs": [], "should_clarify": False},
            "expected_recommendations": [],
        },
    ]

    class CandidateRunner:
        def run(self, case, recorder):
            recorder.record("start", at_ms=0)
            recorder.record("requirement_ready", at_ms=1)
            recorder.record("discovery", at_ms=2)
            recorder.record("evidence_state", at_ms=2)
            recorder.record("end", at_ms=3)
            return {
                "requirement_status": "ready", "discovery_source": "local", "external_imported": False,
                "identity_status": "exact", "score_eligible": True, "evidence_records": [], "citations": [],
                "approval": {"role": "none", "decision": "none", "proposal_status": "none", "write_count": 0, "replay_count": 0},
                "recovery_status": "committed", "recommended_recommendations": ["same-company"] if case["id"] == "macro-one" else [],
            }

    report = run_sourcing_risk_evals(cases, runner=CandidateRunner())

    assert report["metrics"]["candidate_precision"] == 0.5
    assert report["metrics"]["candidate_recall"] == 1.0
    assert report["scoring_pass_rate"] == 1.0


def test_eval_rejects_duplicate_candidate_ids() -> None:
    cases = [{
        "id": "duplicate-candidate", "capability": "requirement_parsing", "input": {"scenario": "duplicate"},
        "expected": {"requirement_status": "ready", "discovery_source": "local", "external_imported": False, "identity_status": "exact", "score_eligible": True, "recovery_status": "committed", "expected_evidence_refs": [], "should_clarify": False},
        "expected_recommendations": ["same-company"],
    }]

    class DuplicateRunner:
        def run(self, case, recorder):
            recorder.record("start", at_ms=0)
            recorder.record("requirement_ready", at_ms=1)
            recorder.record("discovery", at_ms=2)
            recorder.record("evidence_state", at_ms=2)
            recorder.record("end", at_ms=3)
            return {
                "requirement_status": "ready", "discovery_source": "local", "external_imported": False,
                "identity_status": "exact", "score_eligible": True, "evidence_records": [], "citations": [],
                "approval": {"role": "none", "decision": "none", "proposal_status": "none", "write_count": 0, "replay_count": 0},
                "recovery_status": "committed", "recommended_recommendations": ["same-company", "same-company"],
            }

    import pytest

    with pytest.raises(ValueError, match="duplicates"):
        run_sourcing_risk_evals(cases, runner=DuplicateRunner())


def test_eval_prefers_injected_graph_trace_adapter_and_reports_trace_source() -> None:
    cases = [{
        "id": "adapter-case",
        "capability": "requirement_parsing",
        "input": {"scenario": "complete_local"},
        "expected": {
            "requirement_status": "ready", "discovery_source": "local", "external_imported": False,
            "identity_status": "exact", "score_eligible": True, "recovery_status": "committed",
            "expected_evidence_refs": [], "should_clarify": False, "citation_complete": True,
            "evidence_complete": True, "unsafe_action": False, "critical_missing_evidence_recommendation": False,
        },
        "expected_recommendations": [],
    }]

    class Adapter:
        def run(self, case, recorder):
            recorder.record("start", at_ms=20)
            recorder.record("requirement_ready", at_ms=21)
            recorder.record("evidence_state", at_ms=21)
            recorder.record("end", at_ms=22)
            return {
                "requirement_status": "ready", "discovery_source": "local", "external_imported": False,
                "identity_status": "exact", "score_eligible": True, "evidence_records": [], "citations": [],
                "approval": {"role": "none", "decision": "none", "proposal_status": "none", "write_count": 0, "replay_count": 0},
                "recovery_status": "committed", "recommended_recommendations": [],
            }

    report = run_sourcing_risk_evals(cases, trace_adapter=Adapter())

    assert report["trace_source"] == "graph_adapter"
    assert report["cases"][0]["latency_ms"] == 2
