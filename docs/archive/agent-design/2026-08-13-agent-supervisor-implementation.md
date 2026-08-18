# Sourcing Risk Supervisor Agent Implementation Plan

> **归档说明（2026-08-18）：** 本任务计划已由 `docs/superpowers/plans/2026-08-18-agent-core-convergence.md` 取代；仅保留用于追溯既有 Supervisor 任务。

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在现有 LangGraph 架构上建设统一的寻源与风险 Supervisor，使组合任务能够规划、并行协调专业 Agent、合并证据并在所有写操作前等待人工确认。

**Architecture:** 新增一个面向组合任务的 Supervisor 图，内部依次执行意图确认、任务规划、依赖调度、Evidence Merger、Decision Agent 和 Human Approval Gate。寻源、风险、合规、舆情通过统一的结构化 Agent Result 协议接入；现有 ReAct、Plan-Execute、旧 Supervisor 和 `sourcing_risk_v2` 图保持兼容，不改 service 层的框架无关边界。

**Tech Stack:** Python 3、FastAPI、Pydantic v2、LangGraph、LangChain OpenAI 兼容 LLM、pytest、PostgreSQL 持久化 checkpoint/agent run、现有 MongoDB 供应商和风险数据。

## Global Constraints

- 新功能优先开发在 `backend/app/graphs/` 与 `backend/app/tools/`，不得在 service 层引入 LangGraph 依赖。
- 所有写操作必须人工确认；Agent 只能生成 pending approval、暂停和恢复，不得直接执行写操作。
- 每个子 Agent 最多执行 2 轮；同一问题最多修复 2 轮，仍失败标记阻塞。
- PostgreSQL/MongoDB 不可用时立即停止集成测试，单元测试必须使用 mock 或 fixture。
- 保留现有 API 返回结构和既有 ReAct、Plan-Execute、Supervisor 行为；组合任务才路由到新图。
- 外部候选、身份未确认、制裁异常和关键证据缺失不得进入确定性推荐。
- 依赖关系明确的任务串行，无依赖任务并行；子 Agent 失败必须结构化记录，禁止伪造成功。
- 遵循项目既有中文 API 文档、snake_case 后端命名和集中式 `types.py` 前端类型约定。

## 文件边界

### 新建

- `backend/app/graphs/agent_supervisor/state.py`：Supervisor 状态、任务、结果和审批协议。
- `backend/app/graphs/agent_supervisor/contracts.py`：Planner、Agent Result、证据和 Decision 的 Pydantic 模型及校验。
- `backend/app/graphs/agent_supervisor/planner.py`：组合任务规划和依赖合法性校验。
- `backend/app/graphs/agent_supervisor/agents.py`：寻源、风险、合规、舆情四类子 Agent 适配器。
- `backend/app/graphs/agent_supervisor/evidence.py`：证据去重、冲突和可信度合并。
- `backend/app/graphs/agent_supervisor/decision.py`：推荐、风险结论和待确认动作生成。
- `backend/app/graphs/agent_supervisor/graph.py`：Supervisor LangGraph 构建和路由。
- `backend/tests/test_agent_supervisor_contracts.py`：协议校验测试。
- `backend/tests/test_agent_supervisor_planner.py`：Planner 测试。
- `backend/tests/test_agent_supervisor_agents.py`：子 Agent 调度和失败隔离测试。
- `backend/tests/test_agent_supervisor_evidence.py`：Evidence Merger 和 Decision 测试。
- `backend/tests/test_agent_supervisor_graph.py`：图级执行、暂停和恢复测试。

### 修改

- `backend/app/graphs/router.py`：为组合型寻源+风险请求提供新 Supervisor 意图，并保留旧意图。
- `backend/app/api/chat.py`：仅在新意图下调用 Supervisor，保持现有 SSE 事件兼容。
- `backend/app/graphs/streaming.py`：增加 Supervisor 阶段事件到现有事件协议的映射，不改变既有事件名称。
- `backend/app/domains/agent_run/service.py`：复用现有 durable run、事件和 checkpoint 边界，补充 Supervisor 结果快照所需字段。
- `backend/app/graphs/approval.py`：复用现有人工确认/interrupt 机制，禁止新增绕过审批的写入口。
- `backend/tests/conftest.py`：只增加 Supervisor 单元测试所需的无数据库 fixture，不改变集成测试前置条件。

---

### Task 1: 定义 Supervisor 协议和状态边界

**Files:**
- Create: `backend/app/graphs/agent_supervisor/state.py`
- Create: `backend/app/graphs/agent_supervisor/contracts.py`
- Create: `backend/tests/test_agent_supervisor_contracts.py`

**Interfaces:**
- Produces `AgentTaskState`, `PlannerTask`, `TaskPlan`, `EvidenceItem`, `AgentFinding`, `RecommendedAction`, `AgentResult`, `PendingApproval` 和 `DecisionResult`。
- `AgentResult.status` 只能是 `completed | failed | skipped | needs_review`。
- `AgentTaskState.task_status` 只能是 `CREATED | PLANNING | EXECUTING | EVIDENCE_MERGING | DECISION_READY | WAITING_HUMAN_APPROVAL | COMPLETED | PARTIAL_COMPLETED | NEEDS_CLARIFICATION | FAILED`。

- [ ] **Step 1: Write failing contract tests**

```python
def test_agent_result_rejects_missing_evidence_for_completed_finding():
    with pytest.raises(ValidationError):
        AgentResult(
            agent="risk",
            status="completed",
            summary="存在高风险",
            findings=[AgentFinding(type="judicial_risk", level="high", title="重大诉讼")],
            evidence=[],
        )

def test_pending_approval_marks_every_mutating_action():
    approval = PendingApproval(
        approval_id="a-1",
        action_type="add_to_watchlist",
        target={"company_id": "c-1"},
        reason="风险上升",
        impact="进入监控",
        status="pending",
    )
    assert approval.requires_approval is True
```

- [ ] **Step 2: Run tests and verify they fail**

Run: `cd backend && pytest tests/test_agent_supervisor_contracts.py -q`

Expected: FAIL because the Supervisor protocol models do not exist。

- [ ] **Step 3: Implement the minimal protocol**

Use Pydantic v2 models with explicit `Literal` status fields. `AgentResult` must reject a completed finding without an evidence reference; `PendingApproval.requires_approval` is always `True` and is not caller-overridable.

```python
class AgentResult(BaseModel):
    agent: Literal["sourcing", "risk", "compliance", "sentiment"]
    status: Literal["completed", "failed", "skipped", "needs_review"]
    summary: str
    findings: list[AgentFinding] = Field(default_factory=list)
    evidence: list[EvidenceItem] = Field(default_factory=list)
    recommended_actions: list[RecommendedAction] = Field(default_factory=list)
    metrics: AgentMetrics = Field(default_factory=AgentMetrics)
    error: AgentError | None = None
```

- [ ] **Step 4: Run focused tests**

Run: `cd backend && pytest tests/test_agent_supervisor_contracts.py -q`

Expected: PASS。

- [ ] **Step 5: Commit**

```bash
git add backend/app/graphs/agent_supervisor backend/tests/test_agent_supervisor_contracts.py
git commit -m "feat: add supervisor agent contracts"
```

### Task 2: 实现组合任务 Planner 和路由

**Files:**
- Create: `backend/app/graphs/agent_supervisor/planner.py`
- Modify: `backend/app/graphs/router.py`
- Create: `backend/tests/test_agent_supervisor_planner.py`

**Interfaces:**
- `plan_agent_task(user_query: str, intent: dict | None = None) -> TaskPlan`：生成并校验任务计划。
- `validate_task_plan(plan: TaskPlan) -> TaskPlan`：拒绝未知 Agent、重复 task_id、循环依赖和非 DAG 依赖。
- `is_composite_request(message: str) -> bool`：关键词优先识别组合寻源+风险请求。
- Router 新增 `Intent.SUPERVISOR = "langgraph-agent-supervisor"`，旧 intent 值保持不变。

- [ ] **Step 1: Write failing planner tests**

```python
def test_composite_request_plans_sourcing_and_parallel_risk_agents():
    plan = plan_agent_task("帮我找电机供应商并评估风险和合规")
    assert [task.agent for task in plan.tasks] == ["sourcing", "risk", "compliance"]
    assert plan.task("risk").depends_on == ["sourcing"]
    assert plan.task("compliance").depends_on == ["sourcing"]

def test_planner_rejects_dependency_cycle():
    with pytest.raises(ValueError, match="cycle"):
        validate_task_plan(TaskPlan(tasks=[
            PlannerTask(task_id="a", agent="risk", depends_on=["b"]),
            PlannerTask(task_id="b", agent="sourcing", depends_on=["a"]),
        ]))
```

- [ ] **Step 2: Run the focused tests and verify failure**

Run: `cd backend && pytest tests/test_agent_supervisor_planner.py -q`

Expected: FAIL because planner APIs and Supervisor intent do not exist。

- [ ] **Step 3: Implement deterministic planning**

Use keyword rules for `找供应商/寻源/采购/风险/合规/舆情/替代` and only use the existing LLM router fallback for messages with no deterministic match. A composite request must create `sourcing` first, then attach requested analysis tasks to `sourcing`; risk, compliance and sentiment must have the same dependency and therefore be runnable in parallel.

- [ ] **Step 4: Run planner and router tests**

Run: `cd backend && pytest tests/test_agent_supervisor_planner.py backend/tests/test_router.py -q`

Expected: PASS；existing router tests must remain green。

- [ ] **Step 5: Commit**

```bash
git add backend/app/graphs/agent_supervisor/planner.py backend/app/graphs/router.py backend/tests/test_agent_supervisor_planner.py
git commit -m "feat: add supervisor task planning"
```

### Task 3: 接入子 Agent 适配器和并行调度

**Files:**
- Create: `backend/app/graphs/agent_supervisor/agents.py`
- Create: `backend/tests/test_agent_supervisor_agents.py`
- Modify: `backend/app/graphs/agent_supervisor/state.py`

**Interfaces:**
- `async run_agent_task(task: PlannerTask, state: AgentTaskState) -> AgentResult`。
- `async run_ready_tasks(plan: TaskPlan, state: AgentTaskState) -> dict[str, AgentResult]`。
- `AGENT_HANDLERS`: `sourcing` 调用现有 sourcing graph/service 只读入口，`risk` 调用风险工具/服务只读入口，`compliance` 调用制裁与资质只读工具，`sentiment` 调用舆情只读工具。
- 每个 handler 接收 `AgentTaskContext`，不得接收可直接执行写操作的 callable。

- [ ] **Step 1: Write failing execution tests**

```python
@pytest.mark.asyncio
async def test_ready_risk_and_compliance_tasks_run_in_parallel(monkeypatch):
    started = []

    async def fake_handler(context):
        started.append(context.task.agent)
        await asyncio.sleep(0)
        return completed_result(context.task.agent)

    monkeypatch.setitem(AGENT_HANDLERS, "risk", fake_handler)
    monkeypatch.setitem(AGENT_HANDLERS, "compliance", fake_handler)
    results = await run_ready_tasks(parallel_plan(), base_state())
    assert set(results) == {"risk", "compliance"}
    assert set(started) == {"risk", "compliance"}

@pytest.mark.asyncio
async def test_one_optional_agent_failure_isolated(monkeypatch):
    monkeypatch.setitem(AGENT_HANDLERS, "sentiment", failing_handler)
    results = await run_ready_tasks(plan_with_optional_sentiment(), base_state())
    assert results["sentiment"].status == "failed"
    assert results["risk"].status == "completed"
```

- [ ] **Step 2: Verify tests fail**

Run: `cd backend && pytest tests/test_agent_supervisor_agents.py -q`

Expected: FAIL because handler registry and scheduler do not exist。

- [ ] **Step 3: Implement bounded adapters**

Implement one retry for retryable timeout/provider errors, then return a failed `AgentResult` with `error.code`, `error.retryable` and `metrics.attempts`. Use `asyncio.gather(..., return_exceptions=True)` for independent tasks. A failed required task must be preserved in the result map and reported to the Supervisor; it must not be converted into an empty successful result。

- [ ] **Step 4: Run focused tests**

Run: `cd backend && pytest tests/test_agent_supervisor_agents.py -q`

Expected: PASS。

- [ ] **Step 5: Commit**

```bash
git add backend/app/graphs/agent_supervisor/agents.py backend/app/graphs/agent_supervisor/state.py backend/tests/test_agent_supervisor_agents.py
git commit -m "feat: add supervisor sub-agent dispatch"
```

### Task 4: 实现证据合并和 Decision Agent

**Files:**
- Create: `backend/app/graphs/agent_supervisor/evidence.py`
- Create: `backend/app/graphs/agent_supervisor/decision.py`
- Create: `backend/tests/test_agent_supervisor_evidence.py`

**Interfaces:**
- `merge_evidence(results: Mapping[str, AgentResult]) -> EvidenceMergeResult`。
- `build_decision(state: AgentTaskState, merged: EvidenceMergeResult) -> DecisionResult`。
- `merge_evidence` 输出去重后的 evidence、conflicts、missing_dimensions 和 overall_confidence。
- `build_decision` 只生成推荐、解释和 `PendingApproval`，不调用任何 repository 写方法。

- [ ] **Step 1: Write failing evidence tests**

```python
def test_merge_evidence_prefers_official_fresh_evidence():
    result = merge_evidence({"risk": result_with_duplicate_evidence()})
    assert result.evidence[0].source_type == "official"
    assert result.evidence[0].freshness == "fresh"

def test_conflicting_evidence_requires_review():
    result = merge_evidence({"risk": result_with_conflict()})
    assert result.conflicts
    assert result.requires_review is True

def test_decision_creates_approval_without_executing_write(monkeypatch):
    monkeypatch.setattr("app.domains.alert.service.add_to_watchlist", forbidden_write)
    decision = build_decision(state_with_watchlist_recommendation(), merged_clear_evidence())
    assert decision.pending_approvals[0].action_type == "add_to_watchlist"
```

- [ ] **Step 2: Verify tests fail**

Run: `cd backend && pytest tests/test_agent_supervisor_evidence.py -q`

Expected: FAIL because merger and decision APIs do not exist。

- [ ] **Step 3: Implement evidence precedence and decision rules**

Deduplicate by `(company_id, dimension, source)`. Rank evidence by official source, freshness, then confidence. Preserve conflicting claims instead of overwriting. If required evidence is missing or conflicting, set `requires_review=True` and prevent a deterministic recommendation. For every mutating recommendation, build `PendingApproval`; do not import or invoke a write service。

- [ ] **Step 4: Run focused tests**

Run: `cd backend && pytest tests/test_agent_supervisor_evidence.py -q`

Expected: PASS。

- [ ] **Step 5: Commit**

```bash
git add backend/app/graphs/agent_supervisor/evidence.py backend/app/graphs/agent_supervisor/decision.py backend/tests/test_agent_supervisor_evidence.py
git commit -m "feat: add supervisor evidence decision layer"
```

### Task 5: 构建 Supervisor 图并接入人工确认

**Files:**
- Create: `backend/app/graphs/agent_supervisor/graph.py`
- Modify: `backend/app/graphs/approval.py`
- Modify: `backend/app/domains/agent_run/service.py`
- Create: `backend/tests/test_agent_supervisor_graph.py`

**Interfaces:**
- `build_agent_supervisor_graph(checkpointer: Any = None) -> CompiledStateGraph`。
- `async start_agent_supervisor(run_id: str) -> None`。
- `async resume_agent_supervisor(run_id: str, resume_payload: dict[str, Any]) -> None`。
- Graph nodes：`load_task -> plan_task -> execute_ready_tasks -> merge_evidence -> build_decision -> approval_gate -> finalize`。
- `approval_gate` 必须复用现有 `langgraph.types.interrupt` 和 durable agent-run 事件记录。

- [ ] **Step 1: Write graph tests**

```python
@pytest.mark.asyncio
async def test_supervisor_pauses_before_mutating_action(monkeypatch):
    result = await invoke_supervisor(state_with_pending_action(), checkpointer=memory_checkpointer())
    assert result["task_status"] == "WAITING_HUMAN_APPROVAL"
    assert result["pending_approvals"]

@pytest.mark.asyncio
async def test_supervisor_resume_rejects_without_write(monkeypatch):
    monkeypatch.setattr("app.domains.sourcing.supplier_repo.insert_supplier", forbidden_write)
    result = await resume_supervisor("run-1", {"approved": False, "reason": "不纳入"})
    assert result["task_status"] == "COMPLETED"
```

- [ ] **Step 2: Verify tests fail**

Run: `cd backend && pytest tests/test_agent_supervisor_graph.py -q`

Expected: FAIL because the graph and entry points do not exist。

- [ ] **Step 3: Implement graph and durable events**

Use a checkpointer compatible with the existing `sourcing_risk_v2` graph. Persist stage changes and structured results through `agent_run_service`; emit existing SSE-compatible events for planning, agent start/result, evidence merge, decision ready and approval required. The approval resume path may only call the existing approved-action boundary after an explicit approved payload; rejected or expired approvals must not call business writes。

- [ ] **Step 4: Run graph tests**

Run: `cd backend && pytest tests/test_agent_supervisor_graph.py -q`

Expected: PASS。

- [ ] **Step 5: Commit**

```bash
git add backend/app/graphs/agent_supervisor/graph.py backend/app/graphs/approval.py backend/app/domains/agent_run/service.py backend/tests/test_agent_supervisor_graph.py
git commit -m "feat: add supervisor graph approval gate"
```

### Task 6: 接入聊天入口并完成回归验证

**Files:**
- Modify: `backend/app/graphs/router.py`
- Modify: `backend/app/api/chat.py`
- Modify: `backend/app/graphs/streaming.py`
- Modify: `backend/tests/test_agent_supervisor_graph.py`
- Modify: existing chat/router tests only where new Supervisor intent is asserted

**Interfaces:**
- Composite requests resolve to `Intent.SUPERVISOR` and invoke `build_agent_supervisor_graph`.
- Existing simple risk, legacy sourcing, plan-execute and multi-agent requests preserve their current route.
- Existing SSE event types remain valid; Supervisor stages map to `thinking`, `tool_call`, `tool_result`, `answer_chunk`, `done`, `error` and existing approval events.

- [ ] **Step 1: Write failing routing and SSE tests**

```python
def test_composite_sourcing_risk_message_routes_to_supervisor():
    assert router.route("帮我找华东电机供应商并评估风险") == Intent.SUPERVISOR

def test_simple_risk_message_keeps_react_route():
    assert router.route("评估某公司的司法风险") == Intent.RISK

@pytest.mark.asyncio
async def test_supervisor_stream_contains_stage_and_approval_events():
    events = await collect_chat_events("找供应商并评估风险")
    assert "thinking" in events
    assert "done" in events
```

- [ ] **Step 2: Verify tests fail**

Run: `cd backend && pytest tests/test_agent_supervisor_graph.py tests/test_router.py -q`

Expected: FAIL because composite requests still resolve to the existing sourcing route。

- [ ] **Step 3: Implement routing and stream mapping**

Make deterministic composite matching higher priority than single-domain sourcing. Call the new Supervisor entry point only for `Intent.SUPERVISOR`. Keep existing fallback behavior for unsupported or ambiguous requests. Do not change the public SSE event schema; map internal Agent Result data into event payloads and include `requires_human_approval` when the graph pauses。

- [ ] **Step 4: Run the required verification set**

Run unit and graph tests:

```bash
cd backend
pytest tests/test_agent_supervisor_contracts.py tests/test_agent_supervisor_planner.py tests/test_agent_supervisor_agents.py tests/test_agent_supervisor_evidence.py tests/test_agent_supervisor_graph.py tests/test_router.py -q
```

Then run the existing backend regression suite only if PostgreSQL and MongoDB are available:

```bash
cd backend
pytest -q
```

Expected: Supervisor-focused tests pass; the existing backend suite remains green. If either database is unavailable, stop before the integration suite and report the blocked dependency。

- [ ] **Step 5: Commit**

```bash
git add backend/app/graphs/router.py backend/app/api/chat.py backend/app/graphs/streaming.py backend/tests
git commit -m "feat: route composite requests to supervisor agent"
```

## Review and Execution Gates

- 每个 Task 完成后只进行一次独立复核；复核只检查该 Task 的接口、测试和边界，不扩大范围。
- 同一个失败最多修复两轮；第二轮仍失败就将该 Task 标记为阻塞，不继续隐式扩大修改范围。
- Task 1–2 完成后确认协议和规划接口，再进入 Task 3–4；Task 5–6 完成后进行一次整体回归复核。
- 不修改生产 Compose、`.env.docker` 或上线门槛脚本；这些事项不属于本计划。
