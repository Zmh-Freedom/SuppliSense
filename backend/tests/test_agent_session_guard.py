from app.services import agent_session_guard


class _UnavailableRedis:
    def set(self, *_args, **_kwargs):
        raise ConnectionError("Redis unavailable")


def test_process_local_fallback_allows_only_one_active_session(monkeypatch) -> None:
    monkeypatch.setattr(agent_session_guard, "cache_client", _UnavailableRedis())
    session_id = "session-guard-test"

    first = agent_session_guard.acquire_agent_session_run(session_id)
    second = agent_session_guard.acquire_agent_session_run(session_id)

    assert first is not None
    assert second is None
    assert agent_session_guard.renew_agent_session_run(session_id, first) is True

    agent_session_guard.release_agent_session_run(session_id, first)

    third = agent_session_guard.acquire_agent_session_run(session_id)
    assert third is not None
    agent_session_guard.release_agent_session_run(session_id, third)
