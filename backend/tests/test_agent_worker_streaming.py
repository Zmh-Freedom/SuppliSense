"""A terminal/paused state must not race ahead of its persisted SSE tail."""

import asyncio

import pytest

from app.api.chat import _stream_durable_harness_run


@pytest.mark.parametrize("paused,event_type", [(False, "done"), (True, "approval_required")])
def test_worker_subscription_drains_events_committed_between_poll_and_state_read(monkeypatch, paused, event_type):
    polls = []

    def events(run_id, cursor):
        polls.append(cursor)
        # Commit happens after the first events query, before state is read.
        return [] if len(polls) == 1 else [{"event_id": 8, "event_type": event_type, "payload": {"status": "completed"}}]

    monkeypatch.setattr("app.domains.agent_run.repo.list_events_after", events)
    monkeypatch.setattr("app.domains.agent_run.repo.get_run", lambda _: {
        "user_id": "actor", "status": "RUNNING" if paused else "COMPLETED",
    })
    monkeypatch.setattr("app.domains.agent_run.coordinator.run_coordinator.is_paused", lambda _: paused)

    async def collect():
        return [frame async for frame in _stream_durable_harness_run("run", "actor", "purchaser", last_event_id=7)]

    frames = asyncio.run(collect())
    assert sum(f"event: {event_type}" in frame for frame in frames) == 1
    assert polls == [7, 7]
