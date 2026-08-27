"""Rendered Compose behavior for secrets that require exact argv handling."""

import hashlib
import json
import os
import shutil
import subprocess
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]


def _run_dev_start(tmp_path: Path, env_text: str | None) -> subprocess.CompletedProcess[str]:
    script = tmp_path / "start.sh"
    shutil.copy2(REPO_ROOT / "start.sh", script)
    backend_dir = tmp_path / "backend"
    backend_dir.mkdir()
    if env_text is not None:
        (backend_dir / ".env").write_text(env_text, encoding="utf-8")

    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    (fake_bin / "docker").write_text(
        "#!/bin/sh\n"
        "printf '%s\\n' \"$*\" > \"$DEV_COMPOSE_LOG\"\n",
        encoding="utf-8",
    )
    (fake_bin / "docker").chmod(0o755)
    run_env = os.environ.copy()
    run_env["PATH"] = f"{fake_bin}{os.pathsep}{run_env['PATH']}"
    run_env["DEV_COMPOSE_LOG"] = str(tmp_path / "docker.log")
    return subprocess.run(
        ["bash", str(script), "--dev"],
        cwd=tmp_path,
        env=run_env,
        text=True,
        capture_output=True,
        check=False,
    )


def _run_script(
    tmp_path: Path,
    script_name: str,
    args: list[str],
    env_files: dict[str, str],
) -> subprocess.CompletedProcess[str]:
    script = tmp_path / script_name
    shutil.copy2(REPO_ROOT / script_name, script)
    for relative_path, content in env_files.items():
        target = tmp_path / relative_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")

    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    (fake_bin / "docker").write_text(
        "#!/bin/sh\n"
        "printf '%s\\n' \"$*\" > \"$COMPOSE_LOG\"\n",
        encoding="utf-8",
    )
    (fake_bin / "docker").chmod(0o755)
    run_env = os.environ.copy()
    run_env["PATH"] = f"{fake_bin}{os.pathsep}{run_env['PATH']}"
    run_env["COMPOSE_LOG"] = str(tmp_path / "docker.log")
    return subprocess.run(
        ["bash", str(script), *args],
        cwd=tmp_path,
        env=run_env,
        text=True,
        capture_output=True,
        check=False,
    )


def test_dev_start_requires_backend_env_without_printing_password(tmp_path: Path) -> None:
    result = _run_dev_start(tmp_path, None)

    assert result.returncode != 0
    assert "backend/.env" in result.stderr
    assert "PG_PASSWORD" not in result.stdout + result.stderr


def test_dev_start_requires_both_database_passwords(tmp_path: Path) -> None:
    result = _run_dev_start(tmp_path, "PG_PASSWORD=present\n")

    assert result.returncode != 0
    assert "MONGO_PASSWORD" in result.stderr
    assert "present" not in result.stdout + result.stderr


def test_dev_start_passes_backend_env_file_to_dev_compose(tmp_path: Path) -> None:
    secret = "dev-secret-do-not-print"
    result = _run_dev_start(
        tmp_path,
        f"PG_PASSWORD={secret}\nMONGO_PASSWORD={secret}\n",
    )

    assert result.returncode == 0
    command = (tmp_path / "docker.log").read_text(encoding="utf-8")
    assert "--env-file " + str(tmp_path / "backend/.env") in command
    assert "docker-compose.dev.yml" in command
    assert secret not in result.stdout + result.stderr + command


def test_dev_stop_requires_database_passwords_without_printing_password(tmp_path: Path) -> None:
    secret = "stop-secret-do-not-print"
    result = _run_script(
        tmp_path,
        "stop.sh",
        ["--dev"],
        {"backend/.env": f"PG_PASSWORD={secret}\n"},
    )

    assert result.returncode != 0
    assert "MONGO_PASSWORD" in result.stderr
    assert secret not in result.stdout + result.stderr
    assert not (tmp_path / "docker.log").exists()


def test_dev_stop_requires_backend_env(tmp_path: Path) -> None:
    result = _run_script(tmp_path, "stop.sh", ["--dev"], {})

    assert result.returncode != 0
    assert "backend/.env" in result.stderr
    assert "PG_PASSWORD" not in result.stdout + result.stderr
    assert not (tmp_path / "docker.log").exists()


def test_dev_stop_passes_backend_env_file_to_dev_compose(tmp_path: Path) -> None:
    secret = "stop-secret-do-not-print"
    result = _run_script(
        tmp_path,
        "stop.sh",
        ["--dev"],
        {"backend/.env": f"PG_PASSWORD={secret}\nMONGO_PASSWORD={secret}\n"},
    )

    assert result.returncode == 0
    command = (tmp_path / "docker.log").read_text(encoding="utf-8")
    assert "--env-file " + str(tmp_path / "backend/.env") in command
    assert "docker-compose.dev.yml" in command
    assert secret not in result.stdout + result.stderr + command


def test_production_stop_uses_root_docker_env_file(tmp_path: Path) -> None:
    secret = "production-secret-do-not-print"
    result = _run_script(
        tmp_path,
        "stop.sh",
        [],
        {".env.docker": f"PG_PASSWORD={secret}\nMONGO_PASSWORD={secret}\nREDIS_PASSWORD={secret}\n"},
    )

    assert result.returncode == 0
    command = (tmp_path / "docker.log").read_text(encoding="utf-8")
    assert "--env-file " + str(tmp_path / ".env.docker") in command
    assert "docker-compose.yml" in command
    assert secret not in result.stdout + result.stderr + command


def test_redis_command_preserves_special_password_as_one_argument(tmp_path) -> None:
    """Compose must render Redis password as one exec-form argv element."""
    compose_file = tmp_path / "docker-compose.yml"
    shutil.copy2(REPO_ROOT / "docker-compose.yml", compose_file)
    password = "pa:ss/@?#% word"
    env_file = tmp_path / "sample.env"
    env_file.write_text(
        "\n".join((
            "MONGO_PASSWORD=fixture",
            "PG_PASSWORD=fixture",
            "REDIS_PASSWORD=fixture",
        )),
        encoding="utf-8",
    )
    shutil.copy2(env_file, tmp_path / ".env.docker")
    run_env = os.environ.copy()
    run_env["REDIS_PASSWORD"] = password

    result = subprocess.run(
        [
            "docker", "compose", "--project-directory", str(tmp_path),
            "-f", str(compose_file), "--env-file", str(env_file),
            "config", "--format", "json",
        ],
        text=True,
        capture_output=True,
        env=run_env,
        check=False,
    )

    assert result.returncode == 0
    command = json.loads(result.stdout)["services"]["redis"]["command"]
    assert isinstance(command, list)
    assert command[:2] == ["redis-server", "--requirepass"]
    assert len(command) == 3
    assert hashlib.sha256(command[2].encode()).digest() == hashlib.sha256(password.encode()).digest()
