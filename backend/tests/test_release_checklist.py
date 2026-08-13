"""Acceptance contracts for the executable release checklist."""

from pathlib import Path

from app.release_check import BLOCKED, FAIL, PASS, run_release_checklist


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
        "frontend_lint",
        "frontend_build",
        "frontend_test",
        "compose_config",
    }
    assert report["checks_by_name"]["migration_schema"]["status"] == BLOCKED
    assert report["checks_by_name"]["approval_approved_only_outbox"]["status"] == BLOCKED
    assert "PG/Mongo" in report["checks_by_name"]["migration_schema"]["detail"]


def test_release_checklist_marks_command_failures_and_successes() -> None:
    commands: list[list[str]] = []

    def runner(command: list[str], cwd: Path) -> tuple[int, str]:
        del cwd
        commands.append(command)
        if command == ["npm", "run", "lint"]:
            return 0, "lint passed"
        if command == ["npm", "run", "build"]:
            return 1, "frontend build failed"
        return 0, "ok"

    report = run_release_checklist(ROOT, run_external_commands=True, command_runner=runner)

    frontend = report["checks_by_name"]["frontend_build"]
    lint = report["checks_by_name"]["frontend_lint"]
    frontend_test = report["checks_by_name"]["frontend_test"]
    compose = report["checks_by_name"]["compose_config"]
    assert frontend["status"] == "FAIL"
    assert "frontend build failed" in frontend["detail"]
    assert lint["status"] == PASS
    assert frontend_test["status"] == PASS
    assert compose["status"] == PASS
    assert ["npm", "run", "lint"] in commands
    assert ["npm", "run", "build"] in commands
    assert ["npm", "test", "--", "--run"] in commands


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


def test_release_checklist_executes_real_pytest_commands_for_safety_gates() -> None:
    commands: list[list[str]] = []

    def runner(command: list[str], cwd: Path) -> tuple[int, str]:
        del cwd
        commands.append(command)
        return 0, "pytest passed"

    report = run_release_checklist(ROOT, command_runner=runner)

    for name in (
        "api_auth",
        "approval_approved_only_outbox",
        "recovery_fail_closed",
        "rollout_shadow_no_write",
        "sse_replay",
    ):
        assert report["checks_by_name"][name]["status"] == PASS
    assert sum(command[:3] == ["python", "-m", "pytest"] for command in commands) >= 6
    assert any("test_agent_run_models.py" in target for command in commands for target in command)
    assert any("test_agent_run_checkpointer.py" in target for command in commands for target in command)


def test_release_checklist_marks_pytest_failure_as_fail_not_contract_pass() -> None:
    def runner(command: list[str], cwd: Path) -> tuple[int, str]:
        del cwd
        if any("test_auth.py::test_protected_endpoints_require_auth" in target for target in command):
            return 1, "assertion failed in auth gate"
        return 0, "ok"

    report = run_release_checklist(ROOT, command_runner=runner)

    assert report["checks_by_name"]["api_auth"]["status"] == FAIL
    assert "assertion failed" in report["checks_by_name"]["api_auth"]["detail"]
    assert report["ready_for_release"] is False


def test_release_checklist_stops_backend_pytest_after_database_block() -> None:
    commands: list[list[str]] = []

    def runner(command: list[str], cwd: Path) -> tuple[int, str]:
        del cwd
        commands.append(command)
        if command[:3] == ["python", "-m", "pytest"]:
            return 1, "pymongo.errors.ServerSelectionTimeoutError: connection refused"
        return 0, "ok"

    report = run_release_checklist(ROOT, command_runner=runner)

    assert report["checks_by_name"]["migration_schema"]["status"] == BLOCKED
    assert not any("test_auth.py" in target for command in commands for target in command)
    assert ["npm", "run", "lint"] in commands
