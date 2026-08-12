"""Acceptance contracts for the executable release checklist."""

from pathlib import Path

from app.release_check import BLOCKED, PASS, run_release_checklist


ROOT = Path(__file__).resolve().parents[2]


def test_release_checklist_covers_all_release_gates_without_external_databases() -> None:
    report = run_release_checklist(ROOT, run_external_commands=False)

    assert report["summary"]["blocked"] >= 2
    assert {item["name"] for item in report["checks"]} == {
        "migration_schema",
        "api_auth",
        "approval_approved_only_outbox",
        "recovery_fail_closed",
        "rollout_shadow_no_write",
        "sse_replay",
        "frontend_build",
        "compose_config",
    }
    assert report["checks_by_name"]["migration_schema"]["status"] == BLOCKED
    assert report["checks_by_name"]["approval_approved_only_outbox"]["status"] == PASS
    assert "PG/Mongo" in report["checks_by_name"]["migration_schema"]["detail"]


def test_release_checklist_marks_command_failures_and_successes() -> None:
    def runner(command: list[str], cwd: Path) -> tuple[int, str]:
        del cwd
        if command[:2] == ["npm", "run"]:
            return 1, "frontend build failed"
        return 0, "ok"

    report = run_release_checklist(ROOT, run_external_commands=True, command_runner=runner)

    frontend = report["checks_by_name"]["frontend_build"]
    compose = report["checks_by_name"]["compose_config"]
    assert frontend["status"] == "FAIL"
    assert "frontend build failed" in frontend["detail"]
    assert compose["status"] == PASS


def test_release_checklist_classifies_database_connectivity_as_blocked() -> None:
    def runner(command: list[str], cwd: Path) -> tuple[int, str]:
        del command, cwd
        return 1, "psycopg2.OperationalError: localhost:5432 Operation not permitted"

    report = run_release_checklist(ROOT, run_external_commands=True, command_runner=runner)

    assert report["checks_by_name"]["migration_schema"]["status"] == BLOCKED


def test_release_checklist_is_strict_only_when_no_failures_or_blockers() -> None:
    report = run_release_checklist(ROOT, run_external_commands=False)

    assert report["ready_for_release"] is False
    assert report["summary"]["fail"] == 0
    assert report["summary"]["blocked"] > 0
