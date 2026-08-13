"""Small, executable release gate for the sourcing-risk V2 rollout.

The checklist deliberately distinguishes an unavailable integration environment
from an implementation failure.  A blocked database or Docker check is never
reported as a pass and keeps ``ready_for_release`` false.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Callable, Any

PASS = "PASS"
FAIL = "FAIL"
BLOCKED = "BLOCKED"

CommandRunner = Callable[[list[str], Path], tuple[int, str]]
_ENVIRONMENT_MARKERS = (
    "operation not permitted",
    "connection refused",
    "could not connect",
    "connection timed out",
    "name or service not known",
    "temporary failure in name resolution",
    "docker daemon",
    "fe_sendauth: no password supplied",
    "no password supplied",
    "password authentication failed",
    "authentication failed",
    "authentication required",
    "password is required",
    "serverselectiontimeout",
    "server selection timeout",
    "no servers found yet",
    "mongodb uri",
    "mongodb_uri",
    "mongo configuration",
    "database url is not configured",
    "database url missing",
    "configuration missing",
    "env file",
)


def _run(command: list[str], cwd: Path) -> tuple[int, str]:
    try:
        completed = subprocess.run(
            command,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=180,
            check=False,
        )
    except (FileNotFoundError, OSError) as exc:
        return 127, f"environment unavailable: {exc}"
    except subprocess.TimeoutExpired as exc:
        return 124, f"command timed out after {exc.timeout}s"
    output = (completed.stdout + completed.stderr).strip()
    return completed.returncode, output[-2000:]


def _command_check(
    root: Path,
    name: str,
    command: list[str],
    *,
    run_external_commands: bool,
    command_runner: CommandRunner,
    blocked_detail: str,
    cwd: Path | None = None,
) -> dict[str, str]:
    if not run_external_commands:
        return {"name": name, "status": BLOCKED, "detail": blocked_detail}
    try:
        return_code, output = command_runner(command, cwd or root)
    except subprocess.TimeoutExpired as exc:
        return_code, output = 124, f"command timed out after {exc.timeout}s"
    if return_code == 0:
        return {"name": name, "status": PASS, "detail": output or "command completed"}
    if return_code == 127 or output.startswith("environment unavailable:") or any(
        marker in output.lower() for marker in _ENVIRONMENT_MARKERS
    ):
        return {"name": name, "status": BLOCKED, "detail": output or blocked_detail}
    return {"name": name, "status": FAIL, "detail": output or f"command exited {return_code}"}


def _pytest_check(
    root: Path,
    backend: Path,
    name: str,
    test_targets: list[str],
    *,
    run_external_commands: bool,
    command_runner: CommandRunner,
    database_blocked: bool,
) -> dict[str, str]:
    if database_blocked:
        return {
            "name": name,
            "status": BLOCKED,
            "detail": "PG/Mongo 集成依赖不可用，已停止后续后端 pytest 集成门禁",
        }
    return _command_check(
        root,
        name,
        ["python", "-m", "pytest", "-q", *test_targets],
        run_external_commands=run_external_commands,
        command_runner=command_runner,
        cwd=backend,
        blocked_detail="后端 pytest 未执行；需要 Python/测试依赖与可访问的外部依赖",
    )


def _compose_check(
    root: Path,
    *,
    run_external_commands: bool,
    command_runner: CommandRunner,
) -> dict[str, str]:
    """Validate operator configuration before asking Compose to parse YAML."""
    name = "compose_config"
    blocked_detail = "Compose config 未执行；需要 Docker 与 .env.docker（不要把占位符当作上线配置）"
    if not run_external_commands:
        return {"name": name, "status": BLOCKED, "detail": blocked_detail}
    env_file = root / ".env.docker"
    if command_runner is _run:
        if not env_file.is_file():
            return {"name": name, "status": BLOCKED, "detail": f"缺少 Compose 环境文件: {env_file}"}
        values: dict[str, str] = {}
        for line in env_file.read_text(encoding="utf-8").splitlines():
            if not line.strip() or line.lstrip().startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            values[key.strip()] = value.strip()
        missing = [key for key in ("PG_PASSWORD", "MONGO_PASSWORD", "REDIS_PASSWORD") if not values.get(key)]
        if missing:
            return {
                "name": name,
                "status": BLOCKED,
                "detail": f"Compose 环境文件缺少必填配置: {', '.join(missing)}",
            }
    return _command_check(
        root,
        name,
        ["docker", "compose", "--env-file", ".env.docker", "config", "--quiet"],
        run_external_commands=run_external_commands,
        command_runner=command_runner,
        blocked_detail=blocked_detail,
    )


def run_release_checklist(
    root: str | Path,
    *,
    run_external_commands: bool = True,
    command_runner: CommandRunner = _run,
) -> dict[str, Any]:
    """Run release checks and return a stable, machine-readable report."""
    project_root = Path(root).resolve()
    backend = project_root / "backend"
    frontend = project_root / "frontend"
    checks = []
    migration = _command_check(
        project_root,
        "migration_schema",
        ["python", "-m", "pytest", "-q"],
        run_external_commands=run_external_commands,
        command_runner=command_runner,
        cwd=backend,
        blocked_detail="PG/Mongo 集成未执行或当前环境不可达；不得宣称 V2 schema/migration 已通过",
    )
    checks.append(migration)
    database_blocked = migration["status"] == BLOCKED
    checks.extend([
        _pytest_check(
            project_root,
            backend,
            "api_auth",
            ["tests/test_auth.py::test_protected_endpoints_require_auth", "tests/test_agent_run_api.py::test_agent_run_endpoints_require_authentication"],
            run_external_commands=run_external_commands,
            command_runner=command_runner,
            database_blocked=database_blocked,
        ),
        _pytest_check(
            project_root,
            backend,
            "approval_approved_only_outbox",
            ["tests/test_sourcing_risk_actions.py::test_unapproved_import_only_persists_proposal_without_outbox_or_master_write", "tests/test_sourcing_risk_actions.py::test_approval_enqueues_one_transactional_event_and_duplicate_replay_is_rejected"],
            run_external_commands=run_external_commands,
            command_runner=command_runner,
            database_blocked=database_blocked,
        ),
        _pytest_check(
            project_root,
            backend,
            "recovery_fail_closed",
            ["tests/test_sourcing_risk_evidence_service.py", "tests/test_agent_run_service.py::test_get_sourcing_risk_run_fails_closed_when_raw_payload_record_is_missing", "tests/test_agent_run_service.py::test_get_sourcing_risk_run_fails_closed_for_unknown_recovery_lifecycle"],
            run_external_commands=run_external_commands,
            command_runner=command_runner,
            database_blocked=database_blocked,
        ),
        _pytest_check(
            project_root,
            backend,
            "rollout_shadow_no_write",
            ["tests/test_sourcing_risk_actions.py::test_shadow_action_boundary_rejects_proposal_before_database_write", "tests/test_sourcing_risk_evals.py::test_shadow_persists_v2_but_keeps_legacy_response_route"],
            run_external_commands=run_external_commands,
            command_runner=command_runner,
            database_blocked=database_blocked,
        ),
        _pytest_check(
            project_root,
            backend,
            "sse_replay",
            ["tests/test_agent_run_api.py::test_event_endpoint_replays_events_after_last_event_id", "tests/test_agent_run_service.py::test_stream_events_stops_after_replaying_a_durable_terminal_stage"],
            run_external_commands=run_external_commands,
            command_runner=command_runner,
            database_blocked=database_blocked,
        ),
        _command_check(
            project_root,
            "frontend_lint",
            ["npm", "run", "lint"],
            run_external_commands=run_external_commands,
            command_runner=command_runner,
            cwd=frontend,
            blocked_detail="frontend lint 未执行；需要 Node/npm 环境",
        ),
        _command_check(
            project_root,
            "frontend_build",
            ["npm", "run", "build"],
            run_external_commands=run_external_commands,
            command_runner=command_runner,
            cwd=frontend,
            blocked_detail="frontend build 未执行；需要 Node/npm 环境",
        ),
        _command_check(
            project_root,
            "frontend_test",
            ["npm", "test", "--", "--run"],
            run_external_commands=run_external_commands,
            command_runner=command_runner,
            cwd=frontend,
            blocked_detail="frontend test 未执行；需要 Node/npm 环境",
        ),
        _compose_check(
            project_root,
            run_external_commands=run_external_commands,
            command_runner=command_runner,
        ),
    ])
    counts = {status.lower(): sum(check["status"] == status for check in checks) for status in (PASS, FAIL, BLOCKED)}
    return {
        "checklist_version": "task-15-v2",
        "ready_for_release": counts["fail"] == 0 and counts["blocked"] == 0,
        "summary": counts,
        "checks": checks,
        "checks_by_name": {check["name"]: check for check in checks},
    }


def main() -> int:
    import argparse
    import json

    parser = argparse.ArgumentParser(description="Run SuppliSense Task 15 release gates")
    parser.add_argument("--root", default=Path(__file__).resolve().parents[2], type=Path)
    parser.add_argument("--skip-commands", action="store_true", help="report command-backed gates as BLOCKED")
    args = parser.parse_args()
    report = run_release_checklist(args.root, run_external_commands=not args.skip_commands)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["ready_for_release"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
