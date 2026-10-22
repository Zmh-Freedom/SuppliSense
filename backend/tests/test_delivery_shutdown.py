"""Exercise the CLI's real SIGTERM handling without databases or providers."""

import os
from pathlib import Path
import select
import signal
import subprocess
import sys
import time

import pytest


@pytest.mark.parametrize("signal_number", [signal.SIGTERM, signal.SIGINT])
@pytest.mark.parametrize("blocked", [True, False])
def test_worker_exits_within_drain_deadline_even_if_provider_thread_is_blocked(signal_number, blocked):
    source = """
import time
from app.workers import delivery as worker
worker.DRAIN_TIMEOUT_SECONDS = .5
def blocked_job(worker_id):
    print('READY', flush=True)
    time.sleep(SLEEP_SECONDS)
    print('FINISHED', flush=True)
    return {'executed': 1, 'terminal': True}
worker.settings.DELIVERY_ROLE_ENABLED = False
worker.run_delivery_once = blocked_job
worker.main()
""".replace("SLEEP_SECONDS", "60" if blocked else ".1")
    process = subprocess.Popen(
        [sys.executable, "-u", "-c", source],
        cwd=Path(__file__).resolve().parents[1],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        bufsize=0,
        env={**os.environ, "PYTHONUNBUFFERED": "1"},
    )
    try:
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            if select.select([process.stdout], [], [], 0.1)[0]:
                if process.stdout.readline().strip() == b"READY":
                    break
            if process.poll() is not None:
                pytest.fail("worker exited before reaching the blocked provider")
        else:
            pytest.fail("worker did not start within ten seconds")
        process.send_signal(signal_number)
        assert process.wait(timeout=3) == 0
        output = process.stdout.read()
        assert (b"FINISHED" in output) is not blocked
    finally:
        if process.poll() is None:
            process.kill()
        process.communicate(timeout=3)

