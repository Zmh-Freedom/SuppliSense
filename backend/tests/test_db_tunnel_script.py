"""Offline safety tests for the SSH database-tunnel helper."""

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]
EXPECTED_PORTS = (15432, 16379, 27018)


def _write_executable(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")
    path.chmod(0o755)


def _write_fake_commands(bin_dir: Path) -> None:
    _write_executable(
        bin_dir / "lsof",
        """#!/usr/bin/env python3
import json
import os
import sys

args = sys.argv[1:]
port_arg = next((arg for arg in args if arg.startswith("-iTCP:")), None)
port = port_arg.removeprefix("-iTCP:") if port_arg else ""
listeners = json.loads(os.environ["DB_TUNNEL_LISTENERS"])
pids = listeners.get(port, [])
if "-t" in args:
    print("\\n".join(str(pid) for pid in pids))
elif pids:
    print("COMMAND PID USER FD TYPE DEVICE SIZE/OFF NODE NAME")
    print("fake " + " ".join(str(pid) for pid in pids))
raise SystemExit(0 if pids else 1)
""",
    )
    _write_executable(
        bin_dir / "ps",
        """#!/usr/bin/env python3
import json
import os
import sys

args = sys.argv[1:]
pid = args[args.index("-p") + 1]
field = args[args.index("-o") + 1].rstrip("=")
processes = json.loads(os.environ["DB_TUNNEL_PROCESSES"])
process = processes.get(pid)
if process is None:
    raise SystemExit(1)
print(process[field])
""",
    )
    _write_executable(
        bin_dir / "pgrep",
        "#!/bin/sh\nexit 1\n",
    )
    for command in ("ssh", "pkill", "kill"):
        _write_executable(
            bin_dir / command,
            """#!/usr/bin/env python3
import json
import os
import sys

with open(os.environ["DB_TUNNEL_CALL_LOG"], "a", encoding="utf-8") as log:
    log.write(json.dumps({"command": os.path.basename(sys.argv[0]), "args": sys.argv[1:]}) + "\\n")
""",
        )


def _run_tunnel(
    tmp_path: Path,
    action: str,
    *,
    listeners: dict[str, list[int]] | None = None,
    processes: dict[str, dict[str, str]] | None = None,
) -> tuple[subprocess.CompletedProcess[str], list[dict[str, object]]]:
    script = tmp_path / "db-tunnel.sh"
    shutil.copy2(REPO_ROOT / "scripts" / "db-tunnel.sh", script)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _write_fake_commands(bin_dir)
    call_log = tmp_path / "calls.jsonl"
    env = {
        **os.environ,
        "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
        "DB_TUNNEL_CALL_LOG": str(call_log),
        "DB_TUNNEL_LISTENERS": json.dumps(listeners or {}),
        "DB_TUNNEL_PROCESSES": json.dumps(processes or {}),
    }
    result = subprocess.run(
        ["bash", str(script), action],
        cwd=tmp_path,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    calls = [
        json.loads(line)
        for line in call_log.read_text(encoding="utf-8").splitlines()
    ] if call_log.exists() else []
    return result, calls


def _expected_tunnel() -> tuple[dict[str, list[int]], dict[str, dict[str, str]]]:
    pid = 48211
    listeners = {str(port): [pid] for port in EXPECTED_PORTS}
    processes = {
        str(pid): {
            "comm": "ssh",
            "command": "/usr/bin/ssh -f -N -o ServerAliveInterval=30 supplisense-db",
        }
    }
    return listeners, processes


def test_status_recognizes_parameterized_tunnel_from_all_expected_listeners(
    tmp_path: Path,
) -> None:
    listeners, processes = _expected_tunnel()

    result, calls = _run_tunnel(
        tmp_path,
        "status",
        listeners=listeners,
        processes=processes,
    )

    assert result.returncode == 0
    assert "隧道运行中" in result.stdout
    assert not [call for call in calls if call["command"] in {"ssh", "pkill", "kill"}]


def test_start_does_not_launch_a_duplicate_recognized_tunnel(tmp_path: Path) -> None:
    listeners, processes = _expected_tunnel()

    result, calls = _run_tunnel(
        tmp_path,
        "start",
        listeners=listeners,
        processes=processes,
    )

    assert result.returncode == 0
    assert "隧道已在运行" in result.stdout
    assert not [call for call in calls if call["command"] == "ssh"]


def test_start_preserves_a_port_owned_by_an_unrelated_process(tmp_path: Path) -> None:
    listeners = {str(EXPECTED_PORTS[0]): [9001]}
    processes = {
        "9001": {"comm": "node", "command": "node server.js"},
    }

    result, calls = _run_tunnel(
        tmp_path,
        "start",
        listeners=listeners,
        processes=processes,
    )

    assert result.returncode == 1
    assert "本地端口 15432 已被占用" in result.stderr
    assert not [call for call in calls if call["command"] == "ssh"]


def test_stop_signals_only_the_recognized_tunnel_pid(tmp_path: Path) -> None:
    listeners, processes = _expected_tunnel()

    result, calls = _run_tunnel(
        tmp_path,
        "stop",
        listeners=listeners,
        processes=processes,
    )

    assert result.returncode == 0
    assert "隧道已停止" in result.stdout
    assert [call for call in calls if call["command"] == "kill"] == [
        {"command": "kill", "args": ["-TERM", "48211"]}
    ]
    assert not [call for call in calls if call["command"] == "pkill"]


@pytest.mark.parametrize(
    ("listeners", "processes"),
    [
        (
            {str(port): [48211] for port in EXPECTED_PORTS},
            {"48211": {"comm": "ssh", "command": "/usr/bin/ssh -N another-host"}},
        ),
        (
            {"15432": [48211], "16379": [48211], "27018": [48212]},
            {
                "48211": {
                    "comm": "ssh",
                    "command": "/usr/bin/ssh -f -N supplisense-db",
                },
                "48212": {
                    "comm": "ssh",
                    "command": "/usr/bin/ssh -f -N supplisense-db",
                },
            },
        ),
    ],
)
def test_stop_leaves_unmatched_or_split_ssh_port_owners_untouched(
    tmp_path: Path,
    listeners: dict[str, list[int]],
    processes: dict[str, dict[str, str]],
) -> None:
    result, calls = _run_tunnel(
        tmp_path,
        "stop",
        listeners=listeners,
        processes=processes,
    )

    assert result.returncode == 0
    assert "隧道未运行" in result.stdout
    assert not [call for call in calls if call["command"] in {"kill", "pkill"}]
