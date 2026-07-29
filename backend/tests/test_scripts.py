"""Executable behavior tests for the root Compose helper scripts."""

import json
import os
import shutil
import subprocess
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]


def _copy_scripts(project_dir: Path) -> None:
    for name in ("backup.sh", "start.sh", "stop.sh", "docker-compose.yml"):
        shutil.copy2(REPO_ROOT / name, project_dir / name)
    (project_dir / ".env.docker").write_text("MONGO_DB=from_env_file\n", encoding="utf-8")


def _fake_docker(bin_dir: Path) -> None:
    docker = bin_dir / "docker"
    docker.write_text(
        """#!/usr/bin/env python3
import json
import os
import sys
from pathlib import Path

args = sys.argv[1:]
with Path(os.environ[\"DOCKER_LOG\"]).open(\"a\", encoding=\"utf-8\") as log:
    log.write(json.dumps(args) + \"\\n\")

if args == [\"info\"]:
    raise SystemExit(0)
if args and args[0] == \"ps\":
    print(\"sra-mongo\")
    raise SystemExit(0)
if \"exec\" in args:
    sys.stdout.write(\"archive-data\")
    sys.stderr.write(\"dump-diagnostic\")
    raise SystemExit(int(os.environ.get(\"DUMP_EXIT\", \"0\")))
raise SystemExit(0)
""",
        encoding="utf-8",
    )
    docker.chmod(0o755)


def _run_script(tmp_path: Path, *args: str, dump_exit: int = 0) -> tuple[subprocess.CompletedProcess[str], list[list[str]], Path]:
    project_dir = tmp_path / "project"
    project_dir.mkdir(parents=True)
    _copy_scripts(project_dir)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _fake_docker(bin_dir)
    log_file = tmp_path / "docker.jsonl"
    outside_dir = tmp_path / "outside"
    outside_dir.mkdir()
    env = {
        **os.environ,
        "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
        "DOCKER_LOG": str(log_file),
        "DUMP_EXIT": str(dump_exit),
        "TERM": "dumb",
    }
    result = subprocess.run(
        ["bash", str(project_dir / args[0]), *args[1:]],
        cwd=outside_dir,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    calls = [json.loads(line) for line in log_file.read_text(encoding="utf-8").splitlines()]
    return result, calls, project_dir


def test_backup_uses_compose_and_keeps_password_and_diagnostics_out_of_archive(tmp_path) -> None:
    """Backup must use Compose context, preserve --db, and keep dump stderr separate."""
    sample_env = tmp_path / "sample.env"
    password = "fixture-password:with/special?chars"
    sample_env.write_text(
        f"MONGO_DB=from_env_file\nMONGO_PASSWORD={password}\n",
        encoding="utf-8",
    )
    result, calls, project_dir = _run_script(
        tmp_path,
        "backup.sh",
        "--env-file",
        str(sample_env),
        "--db",
        "explicit_db",
    )

    assert result.returncode == 0
    archives = list((project_dir / "backups").glob("explicit_db_*.archive"))
    assert len(archives) == 1
    archive = archives[0]
    assert archive.read_text(encoding="utf-8") == "archive-data"
    assert "dump-diagnostic" not in archive.read_text(encoding="utf-8")
    assert not list((project_dir / "backups").glob("from_env_file_*.archive"))
    flattened = " ".join(" ".join(call) for call in calls)
    assert password not in flattened
    assert all("--password" not in call for call in calls)
    assert ["compose", "--project-directory", str(project_dir), "-f", str(project_dir / "docker-compose.yml")] in [call[:5] for call in calls]


def test_start_and_stop_target_their_own_compose_project_when_run_elsewhere(tmp_path) -> None:
    """Start and stop must not depend on the caller's current directory."""
    start_result, start_calls, project_dir = _run_script(tmp_path, "start.sh")
    stop_result, stop_calls, stop_project_dir = _run_script(tmp_path / "stop", "stop.sh")

    assert start_result.returncode == 0
    assert stop_result.returncode == 0
    start_expected = ["compose", "--project-directory", str(project_dir), "-f", str(project_dir / "docker-compose.yml")]
    stop_expected = ["compose", "--project-directory", str(stop_project_dir), "-f", str(stop_project_dir / "docker-compose.yml")]
    assert start_expected in [call[:5] for call in start_calls]
    assert stop_expected in [call[:5] for call in stop_calls]


def test_backup_removes_partial_archive_when_dump_fails(tmp_path) -> None:
    """A failed dump must not leave its partial stdout artifact behind."""
    result, _, project_dir = _run_script(tmp_path, "backup.sh", "--db", "failed_db", dump_exit=1)

    assert result.returncode != 0
    assert not list((project_dir / "backups").glob("failed_db_*.archive"))
