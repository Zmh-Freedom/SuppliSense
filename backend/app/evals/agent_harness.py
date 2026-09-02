"""Scenario-driven doubles and quality gates for the Agent E2E Harness.

The doubles replace only LLM/provider boundaries. Scenario runners are still
expected to exercise the real Resolver, Harness Runtime, ToolExecutor,
Evidence Ledger and action gate.
"""

from __future__ import annotations

import copy
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol


_REQUIRED_P0_SCENARIO_IDS = frozenset(
    {
        "recommendation-then-plural-risk",
        "plural-then-esg-sentiment",
        "explicit-new-entity",
        "ambiguous-identity",
        "discovery-provider-order",
        "missing-risk-not-low",
        "conflicting-evidence-review",
        "provider-timeout-one-retry",
        "watchlist-human-approval",
        "restart-approval-recovery",
        "approval-idempotency",
        "session-concurrency-isolation",
        "postgres-commit-failure",
        "invalid-tool-output",
        "unsupported-llm-claim",
    }
)


class HarnessDoubleError(RuntimeError):
    """Controlled provider/LLM failure used by offline E2E scenarios."""


def _redact(value: Any) -> Any:
    if isinstance(value, dict):
        sensitive = {"api_key", "authorization", "password", "secret", "token"}
        return {
            str(key): "[REDACTED]" if str(key).lower() in sensitive else _redact(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_redact(item) for item in value]
    return value


class ProgrammableLLMDouble:
    """Replayable async LLM substitute with explicit fault injection."""

    def __init__(self, responses: dict[str, Any] | None = None, faults: dict[str, str] | None = None) -> None:
        self.responses = copy.deepcopy(responses or {})
        self.faults = dict(faults or {})
        self.calls: list[str] = []

    async def ainvoke(self, key: str) -> Any:
        self.calls.append(key)
        fault = self.faults.get(key)
        if fault:
            raise HarnessDoubleError(f"llm:{fault}")
        if key not in self.responses:
            raise HarnessDoubleError(f"llm:missing_fixture:{key}")
        return copy.deepcopy(self.responses[key])


class ProviderReplayDouble:
    """Provider fixture replay with sanitized recording and fault injection."""

    def __init__(self, fixtures: dict[str, Any] | None = None, faults: dict[str, str] | None = None) -> None:
        self.fixtures = copy.deepcopy(fixtures or {})
        self.faults = dict(faults or {})
        self.calls: list[str] = []
        self.recordings: list[dict[str, Any]] = []

    def fetch(self, provider: str, key: str) -> Any:
        request_key = f"{provider}:{key}"
        self.calls.append(request_key)
        self.recordings.append({"provider": provider, "key": key, "request": _redact({"token": "secret", "key": key})})
        fault = self.faults.get(request_key)
        if fault:
            raise HarnessDoubleError(f"provider:{fault}")
        if request_key not in self.fixtures:
            raise HarnessDoubleError(f"provider:missing_fixture:{request_key}")
        return copy.deepcopy(self.fixtures[request_key])

    def replay_recordings(self) -> list[dict[str, Any]]:
        return copy.deepcopy(self.recordings)


@dataclass(frozen=True)
class P0Scenario:
    scenario_id: str
    description: str


@dataclass
class P0Observation:
    scenario_id: str
    passed: bool
    entity_focus_correct: bool = True
    tool_contract_compliant: bool = True
    evidence_support_rate: float = 1.0
    missing_data_low_risk_count: int = 0
    unapproved_writes: int = 0
    incorrect_success_claims: int = 0
    duplicate_writes: int = 0
    recovery_success: bool = True
    unresolved_tool_calls: int = 0
    fingerprint: str = ""
    details: dict[str, Any] = field(default_factory=dict)


class P0ScenarioRunner(Protocol):
    def run(self, scenario: P0Scenario) -> P0Observation: ...


def load_p0_scenarios(path: str | Path) -> list[P0Scenario]:
    """Load and validate the fixed P0 scenario contract used by CI."""
    fixture_path = Path(path)
    payload = json.loads(fixture_path.read_text(encoding="utf-8"))
    raw_scenarios = payload.get("scenarios")
    if not isinstance(raw_scenarios, list):
        raise ValueError("P0 Harness 场景文件必须包含 scenarios 数组")
    scenarios = [
        P0Scenario(str(item["id"]), str(item["description"]))
        for item in raw_scenarios
        if isinstance(item, dict) and item.get("id") and item.get("description")
    ]
    scenario_ids = {scenario.scenario_id for scenario in scenarios}
    if len(scenarios) != len(_REQUIRED_P0_SCENARIO_IDS) or scenario_ids != _REQUIRED_P0_SCENARIO_IDS:
        raise ValueError(
            "P0 Harness 必须完整覆盖 15 个固定场景，"
            f"当前为 {len(scenarios)} 个"
        )
    return scenarios


@dataclass
class P0QualityReport:
    scenario_count: int
    passed_count: int
    pass_rate: float
    entity_focus_accuracy: float
    tool_contract_compliance: float
    evidence_support_rate: float
    missing_data_low_risk_count: int
    unapproved_writes: int
    incorrect_success_claims: int
    duplicate_writes: int
    recovery_success_rate: float
    unresolved_tool_calls: int
    stability_failures: int
    observations: list[P0Observation]

    def as_dict(self) -> dict[str, Any]:
        return {
            "scenario_count": self.scenario_count,
            "passed_count": self.passed_count,
            "pass_rate": self.pass_rate,
            "entity_focus_accuracy": self.entity_focus_accuracy,
            "tool_contract_compliance": self.tool_contract_compliance,
            "evidence_support_rate": self.evidence_support_rate,
            "missing_data_low_risk_count": self.missing_data_low_risk_count,
            "unapproved_writes": self.unapproved_writes,
            "incorrect_success_claims": self.incorrect_success_claims,
            "duplicate_writes": self.duplicate_writes,
            "recovery_success_rate": self.recovery_success_rate,
            "unresolved_tool_calls": self.unresolved_tool_calls,
            "stability_failures": self.stability_failures,
            "observations": [observation.__dict__ for observation in self.observations],
        }


def run_p0_harness(
    scenarios: list[P0Scenario],
    runner: P0ScenarioRunner,
    *,
    repeats: int = 1,
) -> P0QualityReport:
    if not scenarios:
        raise ValueError("P0 Harness 至少需要一个固定场景")
    if repeats <= 0:
        raise ValueError("P0 Harness 重复次数必须为正数")
    observations: list[P0Observation] = []
    stability_failures = 0
    fingerprints: dict[str, str] = {}
    for _ in range(repeats):
        for scenario in scenarios:
            observation = runner.run(scenario)
            if observation.scenario_id != scenario.scenario_id:
                raise ValueError(f"场景返回 ID 不匹配: {scenario.scenario_id}")
            previous = fingerprints.get(scenario.scenario_id)
            if previous is not None and observation.fingerprint and previous != observation.fingerprint:
                stability_failures += 1
            if observation.fingerprint:
                fingerprints[scenario.scenario_id] = observation.fingerprint
            observations.append(observation)
    count = len(observations)
    return P0QualityReport(
        scenario_count=count,
        passed_count=sum(item.passed for item in observations),
        pass_rate=sum(item.passed for item in observations) / count,
        entity_focus_accuracy=sum(item.entity_focus_correct for item in observations) / count,
        tool_contract_compliance=sum(item.tool_contract_compliant for item in observations) / count,
        evidence_support_rate=sum(item.evidence_support_rate for item in observations) / count,
        missing_data_low_risk_count=sum(item.missing_data_low_risk_count for item in observations),
        unapproved_writes=sum(item.unapproved_writes for item in observations),
        incorrect_success_claims=sum(item.incorrect_success_claims for item in observations),
        duplicate_writes=sum(item.duplicate_writes for item in observations),
        recovery_success_rate=sum(item.recovery_success for item in observations) / count,
        unresolved_tool_calls=sum(item.unresolved_tool_calls for item in observations),
        stability_failures=stability_failures,
        observations=observations,
    )


def assert_p0_quality_gates(report: P0QualityReport) -> None:
    failures: list[str] = []
    if report.pass_rate != 1.0:
        failures.append(f"pass_rate={report.pass_rate}")
    if report.entity_focus_accuracy != 1.0:
        failures.append(f"entity_focus_accuracy={report.entity_focus_accuracy}")
    if report.tool_contract_compliance != 1.0:
        failures.append(f"tool_contract_compliance={report.tool_contract_compliance}")
    if report.evidence_support_rate != 1.0:
        failures.append(f"evidence_support_rate={report.evidence_support_rate}")
    for field_name in (
        "missing_data_low_risk_count",
        "unapproved_writes",
        "incorrect_success_claims",
        "duplicate_writes",
        "unresolved_tool_calls",
        "stability_failures",
    ):
        if getattr(report, field_name) != 0:
            failures.append(f"{field_name}={getattr(report, field_name)}")
    if report.recovery_success_rate != 1.0:
        failures.append(f"recovery_success_rate={report.recovery_success_rate}")
    if failures:
        raise AssertionError("P0 Agent Harness quality gate failed: " + ", ".join(failures))


def stable_fingerprint(value: Any) -> str:
    """Create a wording-independent fingerprint for repeatability checks."""
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


__all__ = [
    "HarnessDoubleError",
    "P0Observation",
    "P0QualityReport",
    "P0Scenario",
    "ProgrammableLLMDouble",
    "ProviderReplayDouble",
    "assert_p0_quality_gates",
    "load_p0_scenarios",
    "run_p0_harness",
    "stable_fingerprint",
]
