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
import asyncio, time
from app.workers import agent_worker as worker
worker.DRAIN_TIMEOUT_SECONDS = .5
async def checkpointer(): return None
async def blocked_job(worker_id):
    print('READY', flush=True)
    await asyncio.to_thread(time.sleep, SLEEP_SECONDS)
    print('FINISHED', flush=True)
    return {'executed': 1, 'terminal': True}
worker._get_checkpointer = checkpointer
worker.run_worker_once_async = blocked_job
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


def test_worker_renews_job_and_execution_with_the_same_configured_lease(monkeypatch):
    import asyncio
    from app.workers import agent_worker

    observed = []
    monkeypatch.setattr(agent_worker.settings, "AGENT_WORKER_LEASE_SECONDS", 6)
    monkeypatch.setattr(agent_worker.settings, "AGENT_WORKER_HEARTBEAT_SECONDS", 1)

    async def exercise():
        producer = asyncio.get_running_loop().create_future()

        class Coordinator:
            def __init__(self, *, lease_seconds):
                self.lease_seconds = lease_seconds

            def heartbeat(self, permit):
                observed.append(("execution", self.lease_seconds))
                loop.call_soon_threadsafe(producer.set_result, None)
                return True

        loop = asyncio.get_running_loop()
        monkeypatch.setattr(agent_worker, "RunCoordinator", Coordinator, raising=False)
        monkeypatch.setattr(agent_worker.execution_jobs, "heartbeat_job", lambda *a, lease_seconds: observed.append(("job", lease_seconds)) or True)
        monkeypatch.setattr(agent_worker.run_coordinator, "heartbeat", lambda p: observed.append(("execution", 30)) or loop.call_soon_threadsafe(producer.set_result, None) or True)
        await agent_worker._heartbeat_until_done(job_id="job", worker_id="worker", permit=object(), producer=producer)

    asyncio.run(exercise())
    assert observed == [("job", 6), ("execution", 6)]
