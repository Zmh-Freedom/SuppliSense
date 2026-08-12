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
    output = (completed.stdout + completed.stderr).strip()
    return completed.returncode, output[-2000:]


def _contract_check(root: Path, name: str, paths: list[str], needles: list[str]) -> dict[str, str]:
    missing = [path for path in paths if not (root / path).is_file()]
    contents = "\n".join((root / path).read_text(encoding="utf-8") for path in paths if (root / path).is_file())
    missing.extend(needle for needle in needles if needle not in contents)
    if missing:
        return {"name": name, "status": FAIL, "detail": "missing release contract: " + ", ".join(missing)}
    return {"name": name, "status": PASS, "detail": "focused safety contract is present"}


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
    return_code, output = command_runner(command, cwd or root)
    if return_code == 0:
        return {"name": name, "status": PASS, "detail": output or "command completed"}
    if return_code == 127 or output.startswith("environment unavailable:") or any(
        marker in output.lower() for marker in _ENVIRONMENT_MARKERS
    ):
        return {"name": name, "status": BLOCKED, "detail": output or blocked_detail}
    return {"name": name, "status": FAIL, "detail": output or f"command exited {return_code}"}


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
    checks = [
        _command_check(
            project_root,
            "migration_schema",
            ["python", "-m", "pytest", "-q", "tests/test_company_schema.py"],
            run_external_commands=run_external_commands,
            command_runner=command_runner,
            cwd=backend,
            blocked_detail="PG/Mongo 集成未执行或当前环境不可达；不得宣称 migration/schema 已通过",
        ),
        _contract_check(
            project_root,
            "api_auth",
            ["backend/tests/test_auth.py"],
            ["test_protected_endpoints_require_auth"],
        ),
        _contract_check(
            project_root,
            "approval_approved_only_outbox",
            ["backend/tests/test_sourcing_risk_actions.py"],
            [
                "test_unapproved_import_only_persists_proposal_without_outbox_or_master_write",
                "test_approval_enqueues_one_transactional_event_and_duplicate_replay_is_rejected",
            ],
        ),
        _contract_check(
            project_root,
            "recovery_fail_closed",
            ["backend/tests/test_sourcing_risk_evidence_service.py", "backend/tests/test_agent_run_service.py"],
            ["fail_closed", "unknown"],
        ),
        _contract_check(
            project_root,
            "rollout_shadow_no_write",
            ["backend/tests/test_sourcing_risk_actions.py", "backend/tests/test_sourcing_risk_evals.py"],
            ["test_shadow_action_boundary_rejects_proposal_before_database_write", "AGENT_RUN_V2_SHADOW_READ_ONLY"],
        ),
        _contract_check(
            project_root,
            "sse_replay",
            ["backend/tests/test_agent_run_models.py", "backend/tests/test_agent_run_checkpointer.py"],
            ["replay", "stream"],
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
            "compose_config",
            ["docker", "compose", "--env-file", ".env.docker", "config", "--quiet"],
            run_external_commands=run_external_commands,
            command_runner=command_runner,
            blocked_detail="Compose config 未执行；需要 Docker 与 .env.docker（不要把占位符当作上线配置）",
        ),
    ]
    counts = {status.lower(): sum(check["status"] == status for check in checks) for status in (PASS, FAIL, BLOCKED)}
    return {
        "checklist_version": "task-15-v1",
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
