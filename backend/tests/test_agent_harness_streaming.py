from __future__ import annotations

import asyncio

from app.graphs.streaming import stream_harness_graph


def test_harness_stream_emits_contract_events(monkeypatch):
    async def fake_run_harness(*_args, **_kwargs):
        return {
            "task_specs": [{"tool_name": "assess_risk", "arguments": {"company_name": "甲公司"}}],
            "tool_outcomes": [{"tool_name": "assess_risk", "status": "success", "data": {}}],
            "evidence_records": [],
            "evidence_coverage": {
                "required_dimensions": ["risk"],
                "covered_dimensions": [],
                "missing_dimensions": ["risk"],
            },
            "answer": {
                "status": "needs_review",
                "summary": "证据不足",
                "claims": [],
                "limitations": ["缺少 risk 维度的正式证据"],
                "action_proposals": [],
                "action_receipts": [],
                "evidence_refs": [],
            },
        }

    monkeypatch.setattr("app.graphs.harness.run_harness", fake_run_harness)
    saved_turns: list[tuple[str, str, str, list[dict]]] = []
    monkeypatch.setattr(
        "app.graphs.agent_core.adapter.save_execution_turn",
        lambda *args: saved_turns.append(args),
    )
    events = asyncio.run(
        _collect(
            stream_harness_graph(
                "分析甲公司风险",
                "session-1",
                {"current_task": {"target_supplier_names": ["甲公司"]}},
            )
        )
    )

    assert "event: plan" in events
    assert "event: tool_call" in events
    assert "event: agent_answer" in events
    assert '"status": "needs_review"' in events
    assert "event: evidence" in events
    assert "event: done" in events
    assert saved_turns == [("session-1", "分析甲公司风险", "证据不足\n\n限制：缺少 risk 维度的正式证据", [])]


def test_harness_stream_publishes_progress_before_runner_finishes(monkeypatch):
    async def fake_run_harness(*_args, **kwargs):
        progress = kwargs["progress"]
        await progress("build_plan", {
            "status": "planned",
            "task_specs": [{"tool_name": "slow_tool", "arguments": {"company_name": "甲公司"}}],
        }, True)
        await progress("tool_call", {
            "run_id": "run-1", "task_id": "task-1", "tool": "slow_tool", "args": {},
        }, False)
        await asyncio.sleep(0.02)
        await progress("tool_result", {
            "run_id": "run-1", "task_id": "task-1", "tool": "slow_tool",
            "result": {"status": "success"},
        }, False)
        await progress("render_answer", {
            "status": "completed",
            "answer": {"status": "completed", "summary": "已完成", "claims": [], "limitations": [], "evidence_refs": []},
            "evidence_records": [],
            "evidence_coverage": {"required_dimensions": [], "covered_dimensions": [], "missing_dimensions": []},
        }, True)
        return {
            "answer": {"status": "completed", "summary": "已完成", "claims": [], "limitations": [], "evidence_refs": []},
            "tool_outcomes": [],
        }

    monkeypatch.setattr("app.graphs.harness.run_harness", fake_run_harness)
    events = asyncio.run(_collect(stream_harness_graph("分析甲公司", "session-1", {})))
    event_names = [line.removeprefix("event: ") for line in events.splitlines() if line.startswith("event: ")]

    assert event_names.index("tool_call") < event_names.index("tool_result")
    assert event_names.index("tool_result") < event_names.index("agent_answer")
    assert '"status": "completed"' in events


async def _collect(stream):
    return "".join([event async for event in stream])
