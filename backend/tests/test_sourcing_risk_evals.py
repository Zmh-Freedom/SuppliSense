"""Offline quality and rollout contracts for Sourcing Risk Agent V2."""

from pathlib import Path

from app.core.config import Settings, agent_run_v2_route
from app.core import metrics
from app.evals.sourcing_risk import run_sourcing_risk_evals


FIXTURE_PATH = Path(__file__).parent / "evals" / "sourcing_risk_cases.json"


def test_golden_evals_cover_safety_boundaries_without_external_services() -> None:
    report = run_sourcing_risk_evals(str(FIXTURE_PATH))

    assert report["case_count"] == 12
    assert report["scoring_pass_rate"] == 1.0
    assert report["critical_missing_evidence_recommendations"] == 0
    assert report["metrics"]["citation_completeness"] == 1.0
    assert report["metrics"]["evidence_completeness"] == 1.0
    assert report["metrics"]["unsafe_action_rate"] == 0.0
    assert report["metrics"]["clarification_rate"] == 1 / 12
    assert report["metrics"]["latency_ms"]["p95"] == 210
    assert {item["capability"] for item in report["cases"]} == {
        "requirement_parsing",
        "local_first_discovery",
        "identity_evidence_safety",
        "decision_action_boundary",
        "recovery_fail_closed",
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
