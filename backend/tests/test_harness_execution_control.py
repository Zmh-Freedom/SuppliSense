"""Cancellation must stop dependent tools, including after an in-flight call."""

import asyncio

import pytest

from app.domains.agent_run.coordinator import ExecutionLost
from app.graphs.harness.graph import run_harness
from app.tools.executor import ToolOutcome


def test_inflight_cancellation_never_starts_dependent_tool():
    async def scenario():
        started, release = asyncio.Event(), asyncio.Event()
        cancelled = False
        calls = []

        async def control():
            if cancelled:
                raise ExecutionLost("cancelled")

        class Executor:
            async def execute(self, tool_name, arguments, context):
                calls.append(tool_name)
                started.set()
                await release.wait()
                return ToolOutcome(call_id="call", tool_name=tool_name, tool_version="test", status="success", data={})

        runner = asyncio.create_task(run_harness({
            "session_id": "session", "turn_id": "turn", "run_id": "run", "user_message": "分析", "execution_context": {},
            "task_specs": [
                {"task_id": "first", "tool_name": "assess_risk", "arguments": {"company_name": "甲"}, "entity_id": "a", "dimension": "risk"},
                {"task_id": "second", "tool_name": "assess_risk", "arguments": {"company_name": "乙"}, "entity_id": "b", "dimension": "risk", "depends_on": ["first"]},
            ],
        }, executor=Executor(), control=control))
        await asyncio.wait_for(started.wait(), 2)
        cancelled = True
        release.set()
        with pytest.raises(ExecutionLost):
            await runner
        assert calls == ["assess_risk"]

    asyncio.run(scenario())
