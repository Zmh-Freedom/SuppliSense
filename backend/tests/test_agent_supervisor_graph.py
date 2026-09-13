"""Graph-level behavior tests for the durable Agent Supervisor."""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, call

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from app.domains.agent_run import service as agent_run_service
from app.api import chat as chat_api
from app.graphs import approval
from app.graphs import streaming
from app.graphs.agent_supervisor import graph as supervisor_graph
from app.graphs.agent_supervisor.contracts import AgentResult, PlannerTask, TaskPlan


@pytest.fixture(autouse=True)
def enable_supervisor_v2(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(supervisor_graph.settings, "AGENT_RUN_V2_ENABLED", True)
    monkeypatch.setattr(supervisor_graph, "require_v2_execution", lambda *_: None)


def _plan() -> TaskPlan:
    return TaskPlan(tasks=[PlannerTask(task_id="sourcing", agent="sourcing")])


def _result_with_pending_action() -> AgentResult:
    return AgentResult(
        agent="sourcing",
        status="completed",
        summary="找到一个外部候选供应商。",
        recommended_actions=[
            {
                "action_type": "import_external_supplier",
                "target": {"company_name": "外部供应商 A"},
                "reason": "候选供应商满足采购条件",
                "impact": "写入供应商主库",
            }
        ],
    )


def _state(run_id: str) -> dict:
    return {
        "run_id": run_id,
        "user_query": "寻找供应商并评估风险",
        "intent": {"requirement": {"category": "电机"}},
    }


def _install_graph_doubles(
    monkeypatch: pytest.MonkeyPatch,
) -> list[tuple[str, str, dict]]:
    events: list[tuple[str, str, dict]] = []

    async def run_ready_tasks(_plan: TaskPlan, _state: dict) -> dict[str, AgentResult]:
        return {"sourcing": _result_with_pending_action()}

    monkeypatch.setattr(supervisor_graph, "plan_agent_task", lambda *_: _plan())
    monkeypatch.setattr(supervisor_graph, "run_ready_tasks", run_ready_tasks)
    monkeypatch.setattr(
        agent_run_service,
        "create_supervisor_action_proposals",
        lambda _run_id, approvals: [
            {**approval, "expires_at": (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()}
            for approval in approvals
        ],
    )
    monkeypatch.setattr(
        agent_run_service, "approve_supervisor_action_proposal", lambda *_: None
    )
    monkeypatch.setattr(
        agent_run_service, "approve_supervisor_action_proposals", lambda *_: None
    )
    monkeypatch.setattr(
        agent_run_service,
        "persist_supervisor_snapshot",
        lambda run_id, task_status, event_type, snapshot: events.append(
            (task_status, event_type, snapshot)
        )
        or len(events),
    )
    return events


def test_supervisor_pauses_before_mutating_action(monkeypatch: pytest.MonkeyPatch) -> None:
    """Removing the approval interrupt would let a recommendation reach finalization unreviewed."""
    events = _install_graph_doubles(monkeypatch)
    write_boundary = Mock(side_effect=AssertionError("write before approval"))
    monkeypatch.setattr(
        agent_run_service, "execute_supervisor_approved_action", write_boundary
    )
    graph = supervisor_graph.build_agent_supervisor_graph(InMemorySaver())
    config = {"configurable": {"thread_id": "pause-run"}}

    result = asyncio.run(graph.ainvoke(_state("pause-run"), config))

    assert result["task_status"] == "WAITING_HUMAN_APPROVAL"
    assert result["pending_approvals"]
    assert result["__interrupt__"]
    assert result["__interrupt__"][0].value["type"] == "approval"
    assert [event_type for _, event_type, _ in events] == [
        "stage",
        "planning",
        "agent_start",
        "agent_result",
        "evidence_merge",
        "decision_ready",
        "approval_required",
    ]
    write_boundary.assert_not_called()


@pytest.mark.parametrize(
    "resume_payload",
    [
        {"approved": False, "reason": "不纳入"},
        {"approved": True, "status": "rejected", "reason": "审批记录拒绝"},
        {"approved": True, "status": "expired", "reason": "审批已过期"},
    ],
)
def test_supervisor_resume_without_valid_approval_never_writes(
    monkeypatch: pytest.MonkeyPatch, resume_payload: dict
) -> None:
    """Rejected or expired approvals must complete without entering a business write boundary."""
    _install_graph_doubles(monkeypatch)
    write_boundary = Mock(side_effect=AssertionError("rejected approval wrote data"))
    monkeypatch.setattr(
        agent_run_service, "execute_supervisor_approved_action", write_boundary
    )
    graph = supervisor_graph.build_agent_supervisor_graph(InMemorySaver())
    config = {"configurable": {"thread_id": "reject-run"}}
    asyncio.run(graph.ainvoke(_state("reject-run"), config))

    result = asyncio.run(graph.ainvoke(Command(resume=resume_payload), config))

    assert result["task_status"] == "COMPLETED"
    assert result["pending_approvals"][0]["status"] in {"rejected", "expired"}
    write_boundary.assert_not_called()


def test_supervisor_explicit_approval_uses_existing_approved_action_boundary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An approved resume must use the guarded V2 action boundary instead of a direct repository write."""
    _install_graph_doubles(monkeypatch)
    write_boundary = Mock(return_value=None)
    monkeypatch.setattr(
        agent_run_service, "execute_supervisor_approved_action", write_boundary
    )
    graph = supervisor_graph.build_agent_supervisor_graph(InMemorySaver())
    config = {"configurable": {"thread_id": "approve-run"}}
    paused = asyncio.run(graph.ainvoke(_state("approve-run"), config))

    result = asyncio.run(
        graph.ainvoke(
            Command(resume={"approved": True, "reason": "同意纳入"}), config
        )
    )

    assert result["task_status"] == "COMPLETED"
    write_boundary.assert_called_once_with(
        "approve-run", paused["pending_approvals"][0]["approval_id"]
    )


def test_supervisor_approval_uses_persisted_action_proposal_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The graph must execute the proposal ID returned by the durable action boundary."""
    _install_graph_doubles(monkeypatch)
    monkeypatch.setattr(
        agent_run_service,
        "create_supervisor_action_proposals",
        lambda *_: [{
            "approval_id": "proposal-1",
            "action_type": "import_external_supplier",
            "target": {"company_name": "外部供应商 A"},
            "reason": "候选供应商满足采购条件",
            "impact": "写入供应商主库",
            "status": "pending",
            "expires_at": (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(),
            "requires_approval": True,
        }],
    )
    write_boundary = Mock(return_value=None)
    monkeypatch.setattr(
        agent_run_service, "execute_supervisor_approved_action", write_boundary
    )
    graph = supervisor_graph.build_agent_supervisor_graph(InMemorySaver())
    config = {"configurable": {"thread_id": "proposal-run"}}

    paused = asyncio.run(graph.ainvoke(_state("proposal-run"), config))
    asyncio.run(graph.ainvoke(Command(resume={"approved": True}), config))

    assert paused["pending_approvals"][0]["approval_id"] == "proposal-1"
    write_boundary.assert_called_once_with("proposal-run", "proposal-1")


def test_supervisor_approves_multiple_proposals_before_ordered_execution(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An aggregate approval must keep every proposal executable in order."""
    approvals = [
        {"approval_id": "proposal-1", "expires_at": (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()},
        {"approval_id": "proposal-2", "expires_at": (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()},
    ]
    approved_batches: list[tuple[str, list[str]]] = []
    writes: list[str] = []
    monkeypatch.setattr(
        supervisor_graph,
        "request_supervisor_approval",
        lambda _approvals: {"approved": True, "status": "approved"},
    )
    monkeypatch.setattr(
        agent_run_service,
        "approve_supervisor_action_proposals",
        lambda run_id, proposal_ids: approved_batches.append((run_id, proposal_ids)),
    )
    monkeypatch.setattr(
        agent_run_service,
        "execute_supervisor_approved_action",
        lambda _run_id, proposal_id: writes.append(proposal_id),
    )
    monkeypatch.setattr(supervisor_graph, "_persist", AsyncMock())

    result = asyncio.run(
        supervisor_graph.approval_gate(
            {"run_id": "multi-proposal-run", "pending_approvals": approvals}
        )
    )

    assert approved_batches == [("multi-proposal-run", ["proposal-1", "proposal-2"])]
    assert writes == ["proposal-1", "proposal-2"]
    assert result["task_status"] == "DECISION_READY"


def test_supervisor_creates_one_watchlist_proposal_per_structured_target(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Monitoring requests must stay approval-gated for every referenced supplier."""
    async def completed_tasks(_plan: TaskPlan, _state: dict) -> dict[str, AgentResult]:
        return {"risk": AgentResult(agent="risk", status="completed", summary="完成")}

    persisted: list[dict] = []
    monkeypatch.setattr(supervisor_graph, "run_ready_tasks", completed_tasks)
    monkeypatch.setattr(supervisor_graph, "_persist", AsyncMock())
    # The current write contract requires a resolvable formal supplier identity
    # before an add-watchlist proposal is created. Keep this unit fixture
    # explicit so it tests approval fan-out rather than identity lookup.
    monkeypatch.setattr(
        "app.domains.sourcing.supplier_repo.resolve_supplier_id",
        lambda name: f"supplier-id:{name}",
    )

    result = asyncio.run(
        supervisor_graph.execute_ready_tasks(
            {
                "run_id": "run-1",
                "plan": {"tasks": [{"task_id": "risk", "agent": "risk"}]},
                "intent": {
                    "request_watchlist": True,
                    "target_supplier_names": ["供应商甲", "供应商乙"],
                },
            }
        )
    )

    assert [item["target"]["company_name"] for item in result["recommendations"]] == ["供应商甲", "供应商乙"]
    assert all(item["target"]["target_source"] == "conversation_state" for item in result["recommendations"])


def test_supervisor_resolves_formal_feishu_identity_for_watchlist_proposal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A current Feishu supplier must not be discarded as an unresolved company."""
    async def completed_tasks(_plan: TaskPlan, _state: dict) -> dict[str, AgentResult]:
        return {}

    monkeypatch.setattr(supervisor_graph, "run_ready_tasks", completed_tasks)
    monkeypatch.setattr(supervisor_graph, "_persist", AsyncMock())
    monkeypatch.setattr(
        "app.domains.supplier.access.formal_supplier_id_by_name",
        lambda name: f"supplier:feishu:{name}",
    )
    monkeypatch.setattr(
        "app.domains.sourcing.supplier_repo.resolve_supplier_id",
        lambda _name: None,
    )
    monkeypatch.setattr(
        "app.domains.alert.service._find_watchlist_target",
        lambda **_kwargs: None,
    )

    result = asyncio.run(
        supervisor_graph.execute_ready_tasks(
            {
                "run_id": "feishu-formal-write",
                "plan": {"tasks": []},
                "intent": {
                    "request_watchlist": True,
                    "target_supplier_names": ["上海海拉电子有限公司"],
                },
                "supplier_references": [],
            }
        )
    )

    assert len(result["recommendations"]) == 1
    target = result["recommendations"][0]["target"]
    assert target["target_type"] == "formal_supplier"
    assert target["supplier_id"] == "supplier:feishu:上海海拉电子有限公司"
    assert target["identity_status"] == "verified"


def test_identity_verification_does_not_create_watchlist_proposal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Mentioning a monitoring target is read-only unless add_watchlist is explicit."""
    async def completed_tasks(_plan: TaskPlan, _state: dict) -> dict[str, AgentResult]:
        return {"risk": AgentResult(agent="risk", status="completed", summary="主体核验资料待补充")}

    monkeypatch.setattr(supervisor_graph, "run_ready_tasks", completed_tasks)
    monkeypatch.setattr(supervisor_graph, "_persist", AsyncMock())

    result = asyncio.run(
        supervisor_graph.execute_ready_tasks(
            {
                "run_id": "identity-review-run",
                "plan": {"tasks": [{"task_id": "risk", "agent": "risk"}]},
                "intent": {
                    "requested_action": "none",
                    "target_supplier_names": ["北京经纬恒润科技股份有限公司"],
                },
                "user_query": "请核验监控对象的主体身份",
            }
        )
    )

    assert not any(item["action_type"] == "add_watchlist" for item in result["recommendations"])


def test_supervisor_final_answer_lists_worker_evidence_and_pending_monitoring() -> None:
    """The chat result must expose each dimension instead of a generic completion sentence."""
    answer = supervisor_graph._format_final_answer(
        "证据完整。",
        {
            "risk": AgentResult(
                agent="risk",
                status="completed",
                summary="已完成风险分析。",
                evidence=[{
                    "evidence_id": "risk:供应商甲",
                    "source": "本地风险记录",
                    "source_type": "internal",
                    "company_id": "供应商甲",
                    "dimension": "risk",
                    "claim": "供应商甲 综合风险：低风险。",
                }],
            ),
        },
        [{"approval_id": "proposal-1"}],
    )

    assert "风险：供应商甲 综合风险：低风险。" in answer
    assert "等待人工确认" in answer


def test_supervisor_final_answer_explains_idempotent_watchlist_add() -> None:
    answer = supervisor_graph._format_final_answer(
        "基于 0 条证据形成风险结论，综合可信度 0.00。",
        {},
        [],
        [{"company_name": "上海海拉电子有限公司", "status": "already_watching"}],
    )

    assert answer == "上海海拉电子有限公司 已在风险监控清单中，无需重复加入。"


def test_supervisor_rejects_past_persisted_expiration_even_if_client_approves(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A stale persisted expiry must override an approval flag from the resume client."""
    monkeypatch.setattr(
        "langgraph.types.interrupt",
        lambda *_: {"approved": True, "status": "approved"},
    )
    expired = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()

    result = approval.request_supervisor_approval(
        [{"approval_id": "proposal-1", "expires_at": expired}]
    )

    assert result["approved"] is False
    assert result["status"] == "expired"


def test_start_and_resume_reuse_durable_run_and_checkpointer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Using a process-local graph would make restart recovery lose the supervisor interrupt."""
    compiled_graph = Mock()
    compiled_graph.ainvoke = AsyncMock(return_value={"task_status": "COMPLETED"})
    checkpointer = object()
    get_checkpointer = AsyncMock(return_value=checkpointer)
    build_graph = Mock(return_value=compiled_graph)
    monkeypatch.setattr(
        supervisor_graph,
        "get_orchestration_run",
        lambda run_id: {
            "id": run_id,
            "requirement": {
                "requirement_text": "寻找电机供应商并评估风险",
                "intent": {"requirement": {"category": "电机"}},
            },
        },
    )
    monkeypatch.setattr(
        agent_run_service, "load_execution_snapshot", lambda *_: None
    )
    monkeypatch.setattr(
        supervisor_graph, "get_sourcing_risk_checkpointer", get_checkpointer
    )
    monkeypatch.setattr(
        supervisor_graph, "build_agent_supervisor_graph", build_graph
    )

    asyncio.run(supervisor_graph.start_agent_supervisor("durable-run"))
    asyncio.run(
        supervisor_graph.resume_agent_supervisor(
            "durable-run", {"approved": False, "reason": "拒绝"}
        )
    )

    assert get_checkpointer.await_count == 2
    assert build_graph.call_args_list == [
        call(checkpointer),
        call(checkpointer),
    ]
    start_input = compiled_graph.ainvoke.await_args_list[0].args[0]
    resume_input = compiled_graph.ainvoke.await_args_list[1].args[0]
    assert start_input == {
        "run_id": "durable-run",
        "user_query": "寻找电机供应商并评估风险",
        "intent": {"requirement": {"category": "电机"}},
    }
    assert resume_input == Command(
        resume={"approved": False, "reason": "拒绝"}
    )


def _parse_sse_event(event: str) -> tuple[str, dict]:
    lines = event.strip().splitlines()
    return lines[0].removeprefix("event: "), json.loads(
        lines[1].removeprefix("data: ")
    )


def test_composite_chat_auto_mode_invokes_harness_stream(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Read-only auto mode must enter the unified Harness Runtime."""

    async def harness_stream(session_id, message, _preference_context="", execution_context=None):
        assert message == "帮我找华东电机供应商并评估风险"
        assert session_id == "chat-run"
        assert execution_context["current_task"]["target_supplier_names"] == ["华东电机有限公司"]
        yield 'event: done\ndata: {"answer": "harness"}\n\n'

    monkeypatch.setattr(chat_api, "_langgraph_harness_stream", harness_stream)
    monkeypatch.setattr(
        "app.services.clarification.detect_clarification_needed", lambda *_args, **_kwargs: None
    )
    monkeypatch.setattr(
        "app.graphs.agent_core.adapter.load_execution_context",
        lambda *_args: {
            "history": [],
            "references": [{"name": "华东电机有限公司"}],
            "conversation_state": {"selected_supplier_names": ["华东电机有限公司"]},
            "current_task": {
                "target_supplier_names": ["华东电机有限公司"],
                "analysis_dimensions": ["risk"],
            },
        },
    )
    async def collect_events() -> list[str]:
        response = await chat_api.chat_stream_endpoint(
            chat_api.ChatRequest(
                message="帮我找华东电机供应商并评估风险", session_id="chat-run"
            ),
            SimpleNamespace(state=SimpleNamespace(user_id="")),
        )
        return [event async for event in response.body_iterator]

    events = asyncio.run(collect_events())

    assert events[-1] == 'event: done\ndata: {"answer": "harness"}\n\n'


def test_chat_supervisor_uses_a_persistent_agent_run_for_approval_actions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A chat session ID alone is not an auditable or executable action Run."""
    captured: dict[str, object] = {}
    graph = object()

    monkeypatch.setattr(
        supervisor_graph, "build_agent_supervisor_graph", lambda: graph
    )
    monkeypatch.setattr(
        "app.domains.agent_run.service.create_sourcing_risk_run",
        lambda request, user_id, user_role: captured.update(
            request=request, user_id=user_id, user_role=user_role
        ) or {"id": "durable-run"},
    )

    async def supervisor_stream(*args, **kwargs):
        captured["stream_args"] = args
        captured["graph_input"] = kwargs["graph_input"]
        yield 'event: done\ndata: {"answer": "supervisor"}\n\n'

    monkeypatch.setattr(streaming, "stream_agent_supervisor_graph", supervisor_stream)

    async def exercise() -> list[str]:
        return [event async for event in chat_api._langgraph_agent_supervisor_stream(
            "chat-session",
            "对供应商甲做风险和合规分析并加入监控",
            execution_context={
                "history": [],
                "references": [{"name": "供应商甲"}],
                "conversation_state": {},
                "current_task": {},
                "agent_user_id": "user-1",
            },
        )]

    assert asyncio.run(exercise())[-1] == 'event: done\ndata: {"answer": "supervisor"}\n\n'
    assert captured["user_id"] == "user-1"
    assert captured["graph_input"] == {
        "run_id": "durable-run",
        "user_query": "对供应商甲做风险和合规分析并加入监控",
        "supplier_references": [{"name": "供应商甲"}],
        "intent": {"current_task": {}},
        "conversation_state": {},
    }


def test_chat_recovers_agent_user_from_access_cookie(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Chat must create a durable run for a browser-authenticated user."""
    monkeypatch.setattr(
        "app.core.security.decode_token",
        lambda token: {"type": "access", "sub": "browser-user"} if token == "signed" else None,
    )

    user_id = chat_api._optional_agent_user_id(
        SimpleNamespace(cookies={"access_token": "signed"}, headers={})
    )

    assert user_id == "browser-user"


def test_supervisor_stream_maps_agent_results_to_public_sse_events() -> None:
    """Dropping update mapping would hide Supervisor stages and AgentResult data from chat clients."""

    agent_result = {
        "agent": "sourcing",
        "status": "completed",
        "summary": "找到 2 家候选供应商。",
        "findings": [],
        "evidence": [],
        "recommended_actions": [],
        "metrics": {"duration_ms": 12, "evidence_count": 0, "attempts": 1},
        "error": None,
    }

    class FakeGraph:
        async def astream(self, graph_input, config, *, stream_mode):
            assert graph_input == {
                "run_id": "stream-run",
                "user_query": "找供应商并评估风险",
                "intent": {},
            }
            assert config == {"configurable": {"thread_id": "stream-run"}}
            assert stream_mode == "updates"
            yield {
                "plan_task": {
                    "plan": {
                        "tasks": [
                            {
                                "task_id": "sourcing",
                                "agent": "sourcing",
                                "depends_on": [],
                                "required": True,
                            }
                        ]
                    },
                    "task_status": "PLANNING",
                }
            }
            yield {
                "execute_ready_tasks": {
                    "agent_results": {"sourcing": agent_result},
                    "task_status": "EXECUTING",
                }
            }
            yield {
                "finalize": {
                    "final_answer": "已完成供应商风险分析。",
                    "task_status": "COMPLETED",
                }
            }

    stream_fn = getattr(streaming, "stream_agent_supervisor_graph")
    async def collect_events() -> list[str]:
        return [
            event
            async for event in stream_fn(
                FakeGraph(), "找供应商并评估风险", "stream-run"
            )
        ]

    raw_events = asyncio.run(collect_events())
    events = [_parse_sse_event(event) for event in raw_events]
    event_types = [event_type for event_type, _ in events]

    business_event_types = [event_type for event_type in event_types if event_type != "workflow_status"]
    assert business_event_types == [
        "thinking",
        "thinking",
        "tool_call",
        "thinking",
        "tool_result",
        "thinking",
        "answer_chunk",
        "done",
    ]
    tool_call_events = [payload for event_type, payload in events if event_type == "tool_call"]
    tool_result_events = [payload for event_type, payload in events if event_type == "tool_result"]
    assert tool_call_events[0] == {
        "tool": "sourcing_agent",
        "args": {"task_id": "sourcing", "depends_on": []},
    }
    assert tool_result_events[0] == {"tool": "sourcing_agent", "result": agent_result}
    assert events[-1][1] == {"answer": "已完成供应商风险分析。"}


def test_supervisor_pause_uses_existing_approval_event_with_pause_flag(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Losing the pause flag would make an approval interrupt look terminal to clients."""
    pending_approvals = [
        {
            "approval_id": "proposal-1",
            "action_type": "import_external_supplier",
            "target": {"company_name": "外部供应商 A"},
            "reason": "满足采购条件",
            "impact": "写入供应商主库",
            "status": "pending",
            "requires_approval": True,
        }
    ]

    class InterruptValue:
        value = {
            "type": "approval",
            "tool": "agent_supervisor",
            "args": {"pending_approvals": pending_approvals},
            "message": "确认执行待审批的供应商操作？",
            "pending_approvals": pending_approvals,
        }

    class FakeGraph:
        async def astream(self, *_args, **_kwargs):
            yield {"__interrupt__": (InterruptValue(),)}

    stored: list[dict] = []
    monkeypatch.setattr(
        "app.graphs.interrupt_store.store", lambda **payload: stored.append(payload)
    )
    stream_fn = getattr(streaming, "stream_agent_supervisor_graph")
    async def collect_events() -> list[tuple[str, dict]]:
        return [
            _parse_sse_event(event)
            async for event in stream_fn(
                FakeGraph(), "找供应商并评估风险", "pause-run"
            )
        ]

    events = asyncio.run(collect_events())

    business_event_types = [event_type for event_type, _ in events if event_type != "workflow_status"]
    assert business_event_types == [
        "thinking",
        "approval_required",
        "done",
    ]
    assert events[-2][1] == {
        "message": "确认执行待审批的供应商操作？",
        "tool": "agent_supervisor",
        "args": {"pending_approvals": pending_approvals},
        "session_id": "pause-run",
        "requires_human_approval": True,
        "pending_approvals": pending_approvals,
    }
    assert events[-1][1] == {
        "answer": "确认执行待审批的供应商操作？",
        "status": "waiting_approval",
    }
    assert stored[0]["mode"] == "agent-supervisor"


def test_supervisor_resume_maps_final_answer_through_public_sse_schema(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Using the generic resume mapper would discard a Supervisor finalize update."""

    class FakeGraph:
        async def astream(self, graph_input, config, *, stream_mode):
            assert graph_input == Command(resume={"approved": True})
            assert config == {"configurable": {"thread_id": "resume-run"}}
            assert stream_mode == "updates"
            yield {
                "finalize": {
                    "final_answer": "审批完成，已生成最终分析。",
                    "task_status": "COMPLETED",
                }
            }

    monkeypatch.setattr(
        "app.domains.agent_run.chat_interrupt_repo.take_chat_interrupt",
        lambda _session_id: {
            "config": {"configurable": {"thread_id": "resume-run"}},
            "mode": "agent-supervisor",
            "user_message": "找供应商并评估风险",
        },
    )
    async def rebuild_paused_graph(_paused):
        return FakeGraph()

    monkeypatch.setattr(chat_api, "_rebuild_paused_graph", rebuild_paused_graph)

    async def collect_events() -> list[tuple[str, dict]]:
        response = await chat_api.resume_endpoint(
            chat_api.ResumeRequest(session_id="resume-run", approved=True)
        )
        return [
            _parse_sse_event(event) async for event in response.body_iterator
        ]

    events = asyncio.run(collect_events())

    business_event_types = [event_type for event_type, _ in events if event_type != "workflow_status"]
    assert business_event_types == [
        "thinking",
        "thinking",
        "answer_chunk",
        "done",
    ]
    answer_events = [payload for event_type, payload in events if event_type == "answer_chunk"]
    done_events = [payload for event_type, payload in events if event_type == "done"]
    assert answer_events[-1] == {"text": "审批完成，已生成最终分析。"}
    assert done_events[-1] == {"answer": "审批完成，已生成最终分析。"}
