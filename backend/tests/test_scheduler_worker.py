"""The independent scheduler enqueues work and never executes business tasks."""

import asyncio
import signal

import pytest

from app.workers import scheduler as scheduler_worker


def test_run_once_only_enqueues_due_schedules(monkeypatch):
    calls = []
    monkeypatch.setattr(scheduler_worker.repo, "list_due_schedules", lambda batch_size: [("s1", "t1"), ("s2", "t2")])

    def enqueue(schedule_id, target_id):
        calls.append((schedule_id, target_id))
        return {"job_id": schedule_id, "created": schedule_id == "s1", "status": "QUEUED"}

    monkeypatch.setattr(scheduler_worker.repo, "enqueue_due_schedule", enqueue)
    result = scheduler_worker.run_scheduler_once(batch_size=2)
    assert calls == [("s1", "t1"), ("s2", "t2")]
    assert result == {"scanned": 2, "created": 1, "existing": 1, "blocked": 0, "errors": 0}


def test_one_broken_schedule_does_not_block_the_batch(monkeypatch):
    checked = []
    monkeypatch.setattr(scheduler_worker.repo, "list_due_schedules", lambda batch_size: [("bad", "t1"), ("good", "t2")])

    def enqueue(schedule_id, target_id):
        if schedule_id == "bad":
            raise ValueError("bad schedule definition")
        return {"job_id": "j2", "created": True, "status": "QUEUED"}

    monkeypatch.setattr(scheduler_worker.repo, "enqueue_due_schedule", enqueue)
    monkeypatch.setattr(scheduler_worker.repo, "mark_schedule_checked", lambda schedule_id, target_id, **_: checked.append(schedule_id))
    result = scheduler_worker.run_scheduler_once(batch_size=2)
    assert result["errors"] == 1
    assert result["created"] == 1
    assert checked == ["bad"]


def test_signal_stops_before_the_next_batch(monkeypatch):
    batches = []

    def one_batch(batch_size=20):
        batches.append(batch_size)
        scheduler_worker._request_stop()
        return {"scanned": 0, "created": 0, "existing": 0, "blocked": 0, "errors": 0}

    monkeypatch.setattr(scheduler_worker, "run_scheduler_once", one_batch)
    scheduler_worker._STOP = False
    scheduler_worker._STOP_EVENT = None
    scheduler_worker._LOOP = None
    asyncio.run(scheduler_worker._run_forever(poll_seconds=0.01))
    assert batches == [20]
    scheduler_worker._STOP = False


def test_invalid_poll_interval_is_rejected():
    try:
        asyncio.run(scheduler_worker._run_forever(poll_seconds=0))
    except ValueError as exc:
        assert "轮询间隔" in str(exc)
    else:
        raise AssertionError("non-positive poll interval must fail")


@pytest.mark.parametrize("signal_number", [signal.SIGTERM, signal.SIGINT])
def test_cli_stops_within_deadline_after_signal(signal_number):
    import os
    import select
    import subprocess
    import sys
    import time
    from pathlib import Path

    source = """
import asyncio
from app.workers import scheduler
def batch(batch_size=20):
    print('READY', flush=True)
    return {'scanned': 0, 'created': 0, 'existing': 0, 'blocked': 0, 'errors': 0}
scheduler.run_scheduler_once = batch
scheduler.main()
"""
    process = subprocess.Popen(
        [sys.executable, "-u", "-c", source, "--poll-seconds", "60"],
        cwd=Path(__file__).resolve().parents[1],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, bufsize=0,
        env={**os.environ, "PYTHONUNBUFFERED": "1"},
    )
    try:
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            if select.select([process.stdout], [], [], 0.1)[0]:
                if process.stdout.readline().strip() == b"READY":
                    break
            if process.poll() is not None:
                pytest.fail("scheduler exited before its first batch")
        else:
            pytest.fail("scheduler did not start within ten seconds")
        process.send_signal(signal_number)
        assert process.wait(timeout=3) == 0
    finally:
        if process.poll() is None:
            process.kill()
        process.communicate(timeout=3)


def test_transient_batch_failure_does_not_kill_the_loop(monkeypatch):
    calls = []

    def flaky(batch_size=20):
        calls.append(batch_size)
        if len(calls) == 1:
            raise RuntimeError("database temporarily unavailable")
        scheduler_worker._request_stop()
        return {"scanned": 0, "created": 0, "existing": 0, "blocked": 0, "errors": 0}

    monkeypatch.setattr(scheduler_worker, "run_scheduler_once", flaky)
    scheduler_worker._STOP = False
    scheduler_worker._STOP_EVENT = None
    scheduler_worker._LOOP = None
    asyncio.run(scheduler_worker._run_forever(poll_seconds=0.01))
    scheduler_worker._STOP = False
    assert len(calls) == 2
