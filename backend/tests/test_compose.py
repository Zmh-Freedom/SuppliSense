"""Rendered Compose behavior for secrets that require exact argv handling."""

import hashlib
import json
import shutil
import subprocess
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]


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
            f"REDIS_PASSWORD={password}",
        )),
        encoding="utf-8",
    )
    shutil.copy2(env_file, tmp_path / ".env.docker")

    result = subprocess.run(
        [
            "docker", "compose", "--project-directory", str(tmp_path),
            "-f", str(compose_file), "--env-file", str(env_file),
            "config", "--format", "json",
        ],
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0
    command = json.loads(result.stdout)["services"]["redis"]["command"]
    assert isinstance(command, list)
    assert command[:2] == ["redis-server", "--requirepass"]
    assert len(command) == 3
    assert hashlib.sha256(command[2].encode()).digest() == hashlib.sha256(password.encode()).digest()
