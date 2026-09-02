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


async def _collect(stream):
    return "".join([event async for event in stream])
