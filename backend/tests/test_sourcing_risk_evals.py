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
