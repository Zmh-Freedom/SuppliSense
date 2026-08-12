# 智能寻源与风险 Agent V2 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 交付可恢复、可审计、以本地供应商库优先且所有业务写操作均需人工审批的智能寻源与风险 Agent V2。

**Architecture:** 保留旧 `/api/v1/sourcing` 和 `/api/v1/chat/stream` 的兼容行为，新建独立 `agent_runs` 资源、领域服务和 `SourcingRiskGraph`。图只编排状态、暂停和受限并行；服务层以 `company_id`、策略快照、证据记录和确定性决策为输入输出，PostgreSQL 保存业务真相、事件和检查点，MongoDB 只保存原始外部证据。

**Tech Stack:** Python 3、FastAPI、Pydantic v2、LangGraph、`langgraph-checkpoint-postgres`、psycopg 3、PostgreSQL、MongoDB、Redis、Transactional Outbox、React 19、TypeScript、TanStack Query、原生 fetch/SSE、Tailwind CSS v4、pytest、Vitest。

## Global Constraints

- V2 的业务 API 固定在 `/api/v1/agent-runs`；现有 `/api/v1/sourcing` 和 `/api/v1/chat/stream` 的返回结构不可改变。
- service 层不得引入 LangGraph；LangGraph 只可位于 `backend/app/graphs/sourcing_risk_v2/`。
- V2 的正式候选、证据、评分和动作必须使用 `company_id`；`company_name` 仅作展示快照。
- 每个 Run 只允许一个采购品类和一组规格约束；检测到多品类必须进入澄清，不能自动拆单。
- 本地供应商库优先；外部候选在批准导入前只能是 `staged_candidate`，不能写入供应商主库。
- LLM 只可用于需求结构化、解释和摘要；身份绑定、制裁判断、硬门槛、最终评分、权限和写入必须由确定性代码完成。
- 缺失、过期、冲突和无风险必须是不同状态；制裁数据不可用或关键证据冲突时必须失败关闭进入人工复核。
- 业务状态写入必须生成 `ActionProposal` 并经审批；运行记录、审计、证据索引和图检查点可自动持久化。
- 所有恢复、澄清、审批和取消命令必须带 `expected_version`；乐观锁冲突返回统一 `DomainError` 409。
- V2 不使用 `backend/app/graphs/interrupt_store.py`；必须使用 PostgreSQL Checkpointer、持久化事件和业务审批记录恢复。
- 不新增 Kafka、RabbitMQ、Redux、Zustand、axios 或组件库；审批后的领域动作复用既有 PostgreSQL Transactional Outbox。
- Prometheus label 中不得放企业名称、需求文本、用户 ID 或 `run_id`。
- 单元测试中使用固定 LLM 响应或夹具；真实模型 Eval 只在受控环境运行。

---

## File Structure

### Backend — V2 边界与持久化

- Create: `backend/app/domains/agent_run/__init__.py` — V2 领域包。
- Create: `backend/app/domains/agent_run/models.py` — 状态、候选来源、证据、决策、审批和事件的枚举及纯数据模型。
- Create: `backend/app/domains/agent_run/schemas.py` — V2 请求、响应和 SSE payload 的 Pydantic 模型。
- Create: `backend/app/domains/agent_run/repo.py` — 仅 PostgreSQL 访问；Run、策略快照、候选、证据、决策、审批和事件。
- Create: `backend/app/domains/agent_run/service.py` — Run 生命周期、乐观锁、事件追加、授权边界和启动图的框架无关门面。
- Create: `backend/app/domains/agent_run/api.py` — 独立 FastAPI 路由与 SSE 订阅。
- Modify: `backend/app/db/init_pg.py` — V2 表、约束和索引的幂等 DDL。
- Modify: `backend/app/main.py` — 注册 V2 Router、OpenAPI tag 和 lifespan 中的 Checkpointer 初始化。
- Modify: `backend/app/core/config.py` — Checkpointer、事件保留和 V2 feature flag 配置。
- Modify: `backend/requirements.txt` — 添加 `psycopg[binary,pool]>=3.2` 与 `langgraph-checkpoint-postgres>=2.0`；保留 `psycopg2-binary` 给现有连接池。

### Backend — 领域能力与图

- Create: `backend/app/domains/sourcing_risk/__init__.py` — V2 领域服务包。
- Create: `backend/app/domains/sourcing_risk/requirement_service.py` — 需求解析、一次 LLM 修复和多品类校验。
- Create: `backend/app/domains/sourcing_risk/policy_service.py` — 默认模板、品类模板、不可变快照和策略校验。
- Create: `backend/app/domains/sourcing_risk/discovery_service.py` — 本地检索、充足度判断、外部候选暂存。
- Create: `backend/app/domains/sourcing_risk/identity_service.py` — 对 P1 `search_identity()` 的 `company_id` 适配。
- Create: `backend/app/domains/sourcing_risk/evidence_service.py` — 证据标准化、有效性、冲突和原始 Mongo 引用。
- Create: `backend/app/domains/sourcing_risk/decision_service.py` — 硬门槛、缺失/过期惩罚、稳定排序和原因码。
- Create: `backend/app/domains/sourcing_risk/action_service.py` — 动作提案、审批、Outbox 命令创建和消费处理器。
- Create: `backend/app/graphs/sourcing_risk_v2/__init__.py` — 图构造与运行入口。
- Create: `backend/app/graphs/sourcing_risk_v2/state.py` — 小型 TypedDict 图状态与路由结果。
- Create: `backend/app/graphs/sourcing_risk_v2/nodes.py` — 无持久化副作用的图节点适配。
- Create: `backend/app/graphs/sourcing_risk_v2/checkpointer.py` — `AsyncPostgresSaver` 单例、setup 和关闭。
- Create: `backend/app/graphs/sourcing_risk_v2/runner.py` — 异步图启动、续跑和结构化事件转发。
- Modify: `backend/app/domains/outbox/service.py` — 注册并派发 V2 动作事件；不改既有消费者语义。
- Modify: `backend/app/domains/outbox/worker.py` — 记录 V2 Action 状态事件并保持五次动作重试上限。

### Frontend — 任务工作台

- Create: `frontend/src/components/SourcingRiskWorkbench.tsx` — V2 任务创建、恢复和阶段总览。
- Create: `frontend/src/components/SourcingRiskCandidateCard.tsx` — 本地/暂存候选、身份确认、证据与分组展示。
- Create: `frontend/src/components/SourcingRiskApprovalCard.tsx` — 审批详情、意见和乐观锁版本提交。
- Create: `frontend/src/hooks/useSourcingRiskRun.ts` — Query、Mutation 与断线可恢复 SSE。
- Modify: `frontend/src/api.ts` — V2 API 调用和 `agentRunEventStream()`；不能影响 `chatStream()`。
- Modify: `frontend/src/types.ts` — V2 Run、候选、证据、决策、审批和事件类型。
- Modify: `frontend/src/query-keys.ts` — `agentRunDetail` 和 `agentRunEvents` 键。
- Modify: `frontend/src/components/SourcingPage.tsx` — 用 V2 工作台作为默认入口，保留旧历史只读入口以便回退。

### Tests and operations

- Create: `backend/tests/test_agent_run_models.py` — 枚举、状态转移和 schema 校验。
- Create: `backend/tests/test_agent_run_repo.py` — PostgreSQL DDL、乐观锁、事件序号和授权查询。
- Create: `backend/tests/test_agent_run_service.py` — 生命周期、澄清、取消、审批和幂等命令。
- Create: `backend/tests/test_sourcing_risk_requirement_service.py` — 需求与 LLM 修复夹具。
- Create: `backend/tests/test_sourcing_risk_policy_service.py` — 策略快照不可变与权重校验。
- Create: `backend/tests/test_sourcing_risk_discovery_service.py` — 本地优先、充足度和外部暂存。
- Create: `backend/tests/test_sourcing_risk_identity_service.py` — exact/candidates/pending 三态。
- Create: `backend/tests/test_sourcing_risk_evidence_service.py` — 缺失、过期和冲突。
- Create: `backend/tests/test_sourcing_risk_decision_service.py` — 黄金评分、制裁失败关闭和稳定排序。
- Create: `backend/tests/test_sourcing_risk_graph.py` — 路由、暂停恢复及无 `interrupt_store` 依赖。
- Create: `backend/tests/test_agent_run_api.py` — API、SSE 重放、权限和版本冲突。
- Create: `backend/tests/test_sourcing_risk_actions.py` — 审批、Outbox 幂等和死信。
- Create: `backend/tests/evals/sourcing_risk_cases.json` — 固定离线 Eval 用例集。
- Create: `backend/tests/test_sourcing_risk_evals.py` — 用固定响应运行 Eval 并输出指标。
- Create: `frontend/src/__tests__/SourcingRiskWorkbench.test.tsx` — 创建、SSE、身份复核、审批和刷新恢复。
- Modify: `README.md` — V2 配置、开发启动、Shadow/回退开关和上线验证命令。

## Shared Interfaces

All later tasks use these exact core types. Keep IDs serialized as `str` at repository and API boundaries; Pydantic schemas may expose `UUID` where the existing API convention does so.

```python
class AgentRunStatus(str, Enum):
    CREATED = "CREATED"
    CLARIFYING = "CLARIFYING"
    POLICY_LOCKED = "POLICY_LOCKED"
    LOCAL_SEARCHING = "LOCAL_SEARCHING"
    EXTERNAL_REVIEW = "EXTERNAL_REVIEW"
    IDENTITY_RESOLVING = "IDENTITY_RESOLVING"
    IDENTITY_REVIEW = "IDENTITY_REVIEW"
    INVESTIGATING = "INVESTIGATING"
    EVIDENCE_REVIEW = "EVIDENCE_REVIEW"
    SCORING = "SCORING"
    READY_FOR_REVIEW = "READY_FOR_REVIEW"
    ACTION_PENDING = "ACTION_PENDING"
    ACTION_EXECUTING = "ACTION_EXECUTING"
    COMPLETED = "COMPLETED"
    PARTIAL = "PARTIAL"
    NEEDS_REVIEW = "NEEDS_REVIEW"
    ACTION_FAILED = "ACTION_FAILED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"

class EvidenceRecord(BaseModel):
    evidence_id: UUID
    run_id: UUID
    company_id: UUID
    dimension: Literal["company", "financial", "judicial", "sentiment", "sanctions", "esg", "continuity"]
    claim_code: str
    source_type: str
    source_reference: str | None
    observed_at: datetime
    collected_at: datetime
    confidence: float
    freshness_status: Literal["fresh", "stale", "unknown"]
    conflict_status: Literal["none", "conflicting", "resolved"]
    raw_payload_ref: str | None
    summary: str

def create_sourcing_risk_run(
    request: CreateSourcingRiskRunRequest, user_id: str, user_role: str
) -> dict: ...

def submit_clarification(
    run_id: str, request: ClarificationRequest, user_id: str, user_role: str
) -> dict: ...

def decide_action_proposal(
    run_id: str, approval_id: str, request: ApprovalDecisionRequest,
    user_id: str, user_role: str,
) -> dict: ...

def decide_candidates(
    requirement: dict, policy_snapshot: dict, candidates: list[dict],
    evidence_by_company_id: dict[str, list[dict]],
) -> list[dict]: ...
```

## Phase A — Durable V2 runtime skeleton

### Task 1: Define V2 state, schemas, and allowed transitions

**Files:**
- Create: `backend/app/domains/agent_run/__init__.py`
- Create: `backend/app/domains/agent_run/models.py`
- Create: `backend/app/domains/agent_run/schemas.py`
- Test: `backend/tests/test_agent_run_models.py`

**Interfaces:**
- Produces: `AgentRunStatus`, `CandidateSource`, `CandidateStatus`, `ActionProposalStatus`, `ALLOWED_STATUS_TRANSITIONS`, `CreateSourcingRiskRunRequest`, `ClarificationRequest`, `ApprovalDecisionRequest`, `AgentRunResponse`, `AgentRunEventResponse`.

- [ ] **Step 1: Write failing schema and transition tests**

```python
def test_agent_run_status_allows_clarification_and_rejects_terminal_resume():
    assert AgentRunStatus.CLARIFYING in ALLOWED_STATUS_TRANSITIONS[AgentRunStatus.CREATED]
    assert AgentRunStatus.LOCAL_SEARCHING not in ALLOWED_STATUS_TRANSITIONS[AgentRunStatus.COMPLETED]

def test_create_request_rejects_multiple_categories():
    with pytest.raises(ValidationError, match="一个任务只能包含一个采购品类"):
        CreateSourcingRiskRunRequest(requirement_text="采购摄像头和网关", category="摄像头、网关", specification="IP67")
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd backend && pytest tests/test_agent_run_models.py -v`

Expected: FAIL during import because `app.domains.agent_run` does not exist.

- [ ] **Step 3: Implement immutable data contracts**

```python
class CreateSourcingRiskRunRequest(BaseModel):
    requirement_text: str = Field(min_length=1, max_length=4000)
    category: str | None = Field(default=None, max_length=128)
    specification: str | None = Field(default=None, max_length=2000)
    expected_candidate_count: int = Field(default=3, ge=1, le=20)

    @field_validator("category")
    @classmethod
    def reject_multi_category(cls, value: str | None) -> str | None:
        if value and any(separator in value for separator in ("、", ",", "，", "/")):
            raise ValueError("一个任务只能包含一个采购品类")
        return value.strip() if value else value
```

Define terminal states as an empty transition set, require `expected_version >= 1` for every mutation request, and define event payload fields `event_id`, `run_id`, `version`, `event_type`, `occurred_at`, and `data`.

- [ ] **Step 4: Run focused tests**

Run: `cd backend && pytest tests/test_agent_run_models.py -v`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/domains/agent_run backend/tests/test_agent_run_models.py
git commit -m "feat: define agent run v2 contracts"
```

### Task 2: Add idempotent PostgreSQL schema and repository primitives

**Files:**
- Modify: `backend/app/db/init_pg.py`
- Create: `backend/app/domains/agent_run/repo.py`
- Test: `backend/tests/test_agent_run_repo.py`

**Interfaces:**
- Consumes: Task 1 models and schemas.
- Produces: `insert_run`, `get_run_for_user`, `update_run_status`, `append_event`, `list_events_after`, `insert_policy_snapshot`, `insert_candidate`, `insert_evidence`, `insert_decision`, `insert_action_proposal`, `insert_approval_decision`.

- [ ] **Step 1: Write failing repository tests against real PostgreSQL**

```python
def test_append_event_assigns_monotonic_event_ids_for_one_run():
    run = _insert_run_for_test()
    first = repo.append_event(run["id"], 1, "stage", {"status": "CREATED"})
    second = repo.append_event(run["id"], 1, "stage", {"status": "POLICY_LOCKED"})
    assert (first["event_id"], second["event_id"]) == (1, 2)

def test_update_run_status_requires_expected_version():
    run = _insert_run_for_test()
    assert repo.update_run_status(run["id"], 99, "CANCELLED") is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && pytest tests/test_agent_run_repo.py -v`

Expected: FAIL because V2 tables and repository functions do not exist.

- [ ] **Step 3: Add tables, constraints, and indexes**

Append DDL to `DDL_STATEMENTS` for these tables:

```sql
CREATE TABLE IF NOT EXISTS agent_runs (
    id UUID PRIMARY KEY,
    run_type VARCHAR(32) NOT NULL CHECK (run_type = 'sourcing_risk_v2'),
    user_id UUID REFERENCES users(id) ON DELETE SET NULL,
    status VARCHAR(32) NOT NULL,
    version INTEGER NOT NULL DEFAULT 1 CHECK (version > 0),
    requirement JSONB NOT NULL DEFAULT '{}',
    policy_snapshot_id UUID,
    decision_id UUID,
    error_code VARCHAR(64),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    completed_at TIMESTAMPTZ
);
CREATE TABLE IF NOT EXISTS agent_run_events (
    run_id UUID NOT NULL REFERENCES agent_runs(id) ON DELETE CASCADE,
    event_id BIGINT NOT NULL,
    version INTEGER NOT NULL,
    event_type VARCHAR(48) NOT NULL,
    payload JSONB NOT NULL,
    occurred_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (run_id, event_id)
);
```

Add the remaining spec tables (`sourcing_policy_templates`, `sourcing_policy_snapshots`, `agent_run_candidates`, `agent_evidence`, `candidate_decisions`, `agent_action_proposals`, `agent_approval_decisions`) with UUID primary keys, foreign keys to `agent_runs`, JSONB for policy/score/reason snapshots, and a unique `agent_action_proposals(idempotency_key)` constraint. Add indexes on `(user_id, created_at DESC)`, `(run_id, event_id)`, `(run_id, status)`, and pending action execution state.

Implement `update_run_status` with `WHERE id = %s AND version = %s` and `RETURNING`; increment version in the same update. Implement `append_event` in the caller transaction with `SELECT COALESCE(MAX(event_id), 0) + 1 ... FOR UPDATE` on the Run row so a Run’s sequence is monotonic under concurrency.

- [ ] **Step 4: Run focused tests**

Run: `cd backend && pytest tests/test_agent_run_repo.py -v`

Expected: PASS, including cleanup of test rows in dependency order.

- [ ] **Step 5: Commit**

```bash
git add backend/app/db/init_pg.py backend/app/domains/agent_run/repo.py backend/tests/test_agent_run_repo.py
git commit -m "feat: persist agent run v2 state"
```

### Task 3: Implement lifecycle service, API, durable event replay, and cancellation

**Files:**
- Create: `backend/app/domains/agent_run/service.py`
- Create: `backend/app/domains/agent_run/api.py`
- Modify: `backend/app/main.py`
- Test: `backend/tests/test_agent_run_service.py`
- Test: `backend/tests/test_agent_run_api.py`

**Interfaces:**
- Consumes: Task 1 schemas and Task 2 repository functions.
- Produces: `create_sourcing_risk_run`, `get_sourcing_risk_run`, `submit_clarification`, `cancel_run`, `stream_events`, routes under `/agent-runs`.

- [ ] **Step 1: Write failing lifecycle and HTTP tests**

```python
def test_cancel_run_updates_only_the_expected_version(monkeypatch):
    monkeypatch.setattr(service, "get_run_for_user", lambda *_: {"status": "CLARIFYING", "version": 2})
    with pytest.raises(DomainError) as exc:
        service.cancel_run("run-id", expected_version=1, user_id="user-id")
    assert exc.value.code == "AGENT_RUN_VERSION_CONFLICT"

def test_event_endpoint_replays_events_after_last_event_id(client, auth_headers, monkeypatch):
    monkeypatch.setattr(api, "stream_events", lambda *_: iter([{"event_id": 4, "event_type": "stage", "data": {}}]))
    response = client.get("/api/v1/agent-runs/run-id/events", headers={**auth_headers, "Last-Event-ID": "3"})
    assert "id: 4" in response.text
    assert "event: stage" in response.text
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && pytest tests/test_agent_run_service.py tests/test_agent_run_api.py -v`

Expected: FAIL because the service and router do not exist.

- [ ] **Step 3: Implement state mutation and SSE contract**

Use the existing `DomainError` envelope. `get_sourcing_risk_run` must restrict by creator unless an administrator is explicitly authorized by a `can_read_all_agent_runs()` helper. Emit SSE as:

```python
def _agent_run_sse_event(event: dict) -> str:
    return (
        f"id: {event['event_id']}\n"
        f"event: {event['event_type']}\n"
        f"data: {json.dumps(event['data'], ensure_ascii=False, default=str)}\n\n"
    )
```

Expose these handlers: create, detail, events, clarification, approval decision, cancel. Add `Last-Event-ID` as an optional header. When no new events exist, send a 15-second `: keepalive` comment while the run is non-terminal; stop after sending terminal `done` or `error`. Register `agent_run_router` in `main.py` and add the `agent-runs` OpenAPI tag.

- [ ] **Step 4: Run focused tests**

Run: `cd backend && pytest tests/test_agent_run_service.py tests/test_agent_run_api.py -v`

Expected: PASS; API tests assert unauthenticated 401, foreign Run 404, and stale version 409.

- [ ] **Step 5: Commit**

```bash
git add backend/app/domains/agent_run backend/app/main.py backend/tests/test_agent_run_service.py backend/tests/test_agent_run_api.py
git commit -m "feat: add durable agent run v2 api"
```

### Task 4: Add PostgreSQL LangGraph checkpoint infrastructure

**Files:**
- Modify: `backend/requirements.txt`
- Modify: `backend/app/core/config.py`
- Create: `backend/app/graphs/sourcing_risk_v2/checkpointer.py`
- Modify: `backend/app/main.py`
- Test: `backend/tests/test_sourcing_risk_graph.py`

**Interfaces:**
- Produces: `async def get_sourcing_risk_checkpointer() -> AsyncPostgresSaver`, `async def close_sourcing_risk_checkpointer() -> None`.

- [ ] **Step 1: Write a failing checkpointer lifecycle test**

```python
@pytest.mark.asyncio
async def test_checkpointer_setup_is_idempotent(monkeypatch):
    fake = AsyncMock()
    monkeypatch.setattr(checkpointer, "_new_checkpointer", AsyncMock(return_value=fake))
    assert await checkpointer.get_sourcing_risk_checkpointer() is fake
    assert await checkpointer.get_sourcing_risk_checkpointer() is fake
    fake.setup.assert_awaited_once()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && pytest tests/test_sourcing_risk_graph.py::test_checkpointer_setup_is_idempotent -v`

Expected: FAIL because the V2 checkpointer module does not exist.

- [ ] **Step 3: Implement the persistent saver**

Construct a URI from `PG_HOST`, `PG_PORT`, `PG_USER`, `PG_PASSWORD`, and `PG_DB`, URL-encoding credentials. Add `AGENT_RUN_CHECKPOINT_SCHEMA: str = "agent_checkpoint"` and `AGENT_RUN_V2_ENABLED: bool` to settings. Initialize `AsyncPostgresSaver.from_conn_string()` once in lifespan, call `await saver.setup()` once, compile graphs with `thread_id=str(run_id)`, and close it during shutdown. Never reuse the `psycopg2` pool for psycopg 3 objects.

- [ ] **Step 4: Run focused test**

Run: `cd backend && pytest tests/test_sourcing_risk_graph.py::test_checkpointer_setup_is_idempotent -v`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/requirements.txt backend/app/core/config.py backend/app/graphs/sourcing_risk_v2/checkpointer.py backend/app/main.py backend/tests/test_sourcing_risk_graph.py
git commit -m "feat: add persistent agent graph checkpoints"
```

## Phase B — Requirement, policy, local discovery, and identity

### Task 5: Build deterministic requirement parsing and clarification

**Files:**
- Create: `backend/app/domains/sourcing_risk/requirement_service.py`
- Create: `backend/app/domains/sourcing_risk/__init__.py`
- Test: `backend/tests/test_sourcing_risk_requirement_service.py`

**Interfaces:**
- Produces: `SourcingRequirement`, `parse_requirement(raw_text: str, provided: dict | None = None) -> dict`, `missing_requirement_fields(requirement: dict) -> list[str]`.

- [ ] **Step 1: Write failing tests with a fixed LLM adapter**

```python
def test_parse_requirement_requests_clarification_when_specification_is_missing(monkeypatch):
    monkeypatch.setattr(requirement_service, "extract_requirement", lambda *_: {"category": "摄像头"})
    result = requirement_service.parse_requirement("找摄像头供应商")
    assert result["status"] == "clarification_required"
    assert result["missing"] == ["specification"]

def test_parse_requirement_repairs_invalid_llm_output_once(monkeypatch):
    monkeypatch.setattr(requirement_service, "extract_requirement", _responses([{"category": 1}, {"category": "摄像头", "specification": "IP67"}]))
    assert requirement_service.parse_requirement("采购 IP67 摄像头")["requirement"]["category"] == "摄像头"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && pytest tests/test_sourcing_risk_requirement_service.py -v`

Expected: FAIL because `requirement_service` does not exist.

- [ ] **Step 3: Implement validated extraction boundary**

Define `SourcingRequirement(BaseModel)` with `category`, `specification`, optional region/quantity/budget/qualifications/delivery/risk limit, and `candidate_count`. The adapter must ask only for JSON matching the model, validate it, call `repair_requirement()` once with validation errors, and return `{status: "clarification_required", missing: [...]}` rather than throwing after a second invalid answer. Reject any parsed category containing a multi-category separator.

- [ ] **Step 4: Run focused tests**

Run: `cd backend && pytest tests/test_sourcing_risk_requirement_service.py -v`

Expected: PASS without a network or real LLM call.

- [ ] **Step 5: Commit**

```bash
git add backend/app/domains/sourcing_risk backend/tests/test_sourcing_risk_requirement_service.py
git commit -m "feat: parse sourcing risk requirements"
```

### Task 6: Version default and category policy snapshots

**Files:**
- Create: `backend/app/domains/sourcing_risk/policy_service.py`
- Test: `backend/tests/test_sourcing_risk_policy_service.py`

**Interfaces:**
- Produces: `DEFAULT_POLICY`, `resolve_policy_template(category: str) -> dict`, `freeze_policy_snapshot(run_id: str, category: str) -> dict`, `validate_policy(policy: dict) -> None`.

- [ ] **Step 1: Write failing policy tests**

```python
def test_default_policy_has_one_total_weight_and_required_sanctions_gate():
    policy = policy_service.resolve_policy_template("未知品类")
    assert sum(policy["weights"].values()) == pytest.approx(1.0)
    assert policy["hard_gates"]["sanctions_available"] == "needs_review"

def test_snapshot_does_not_change_when_template_changes(monkeypatch):
    snapshot = policy_service.freeze_policy_snapshot("run-id", "摄像头")
    monkeypatch.setattr(policy_service, "resolve_policy_template", lambda _: {"version": "changed"})
    assert snapshot["template_version"] != "changed"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && pytest tests/test_sourcing_risk_policy_service.py -v`

Expected: FAIL because policy service does not exist.

- [ ] **Step 3: Implement default and category policy contract**

Set default weights exactly to `match: 0.25`, `capacity: 0.20`, `performance: 0.15`, `quality: 0.15`, `risk: 0.15`, `commercial: 0.10`. Define `minimum_candidate_count: 3`, per-dimension missing/stale penalties, required `sanctions` evidence, and hard gates for unmatched category, non-active supplier, missing mandatory qualification, unverified identity, sanctions hit/unavailable, and key evidence conflict. Persist a full JSON snapshot with `template_id`, `template_version`, `effective_at`, `scoring_version` and checksum; never mutate a snapshot.

- [ ] **Step 4: Run focused tests**

Run: `cd backend && pytest tests/test_sourcing_risk_policy_service.py -v`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/domains/sourcing_risk/policy_service.py backend/tests/test_sourcing_risk_policy_service.py
git commit -m "feat: add sourcing risk policy snapshots"
```

### Task 7: Implement local-first discovery and staged external candidates

**Files:**
- Create: `backend/app/domains/sourcing_risk/discovery_service.py`
- Modify: `backend/app/domains/sourcing/supplier_repo.py`
- Test: `backend/tests/test_sourcing_risk_discovery_service.py`

**Interfaces:**
- Consumes: `SourcingRequirement`, frozen policy.
- Produces: `discover_local_candidates(requirement: dict, policy: dict) -> list[dict]`, `is_candidate_supply_sufficient(candidates: list[dict], requirement: dict, policy: dict) -> bool`, `stage_external_candidates(run_id: str, candidates: list[dict]) -> list[dict]`.

- [ ] **Step 1: Write failing behavior tests**

```python
def test_local_candidates_prevent_external_provider_call(monkeypatch):
    monkeypatch.setattr(discovery_service, "search_local_suppliers", lambda *_: [_candidate("a"), _candidate("b"), _candidate("c")])
    external = Mock()
    monkeypatch.setattr(discovery_service, "search_external_provider", external)
    result = discovery_service.discover_candidates(_requirement(), _policy())
    assert result["source"] == "local"
    external.assert_not_called()

def test_external_candidates_are_staged_without_supplier_or_company_id(monkeypatch):
    staged = discovery_service.stage_external_candidates("run-id", [{"name": "外部公司", "source_reference": "tyc:1"}])
    assert staged[0]["status"] == "staged_candidate"
    assert staged[0]["supplier_id"] is None
    assert staged[0]["company_id"] is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && pytest tests/test_sourcing_risk_discovery_service.py -v`

Expected: FAIL because discovery service does not exist.

- [ ] **Step 3: Implement matching and sufficiency rules**

Add a read-only `search_for_sourcing_v2()` in `supplier_repo.py` that returns normalized candidate dictionaries containing `supplier_id`, `supplier_name`, `categories`, `regions`, `status`, `qualifications`, `capacity`, `updated_at`, and `match_reasons`. It must not invoke `resolve_supplier_id(..., auto_create=True)`. Filter by category/specification, region, explicit qualifications and active supplier state. Sufficiency requires `len(candidates) >= minimum_candidate_count` and every required constraint represented by at least one candidate. Only then skip the external provider. Provider exceptions follow 20-second timeout/retry policy in Task 11; the discovery result must retain local candidates and mark `external_status`.

- [ ] **Step 4: Run focused tests**

Run: `cd backend && pytest tests/test_sourcing_risk_discovery_service.py -v`

Expected: PASS, including the no-write invariant.

- [ ] **Step 5: Commit**

```bash
git add backend/app/domains/sourcing_risk/discovery_service.py backend/app/domains/sourcing/supplier_repo.py backend/tests/test_sourcing_risk_discovery_service.py
git commit -m "feat: add local first supplier discovery"
```

### Task 8: Adapt P1 identity resolution for V2 candidates

**Files:**
- Create: `backend/app/domains/sourcing_risk/identity_service.py`
- Test: `backend/tests/test_sourcing_risk_identity_service.py`

**Interfaces:**
- Consumes: `app.domains.company.service.search_identity`.
- Produces: `resolve_candidate_identity(candidate: dict) -> dict` with `identity_status` of `exact`, `candidates`, or `pending_verification`.

- [ ] **Step 1: Write failing identity tests**

```python
def test_exact_identity_binds_only_the_canonical_company_id(monkeypatch):
    monkeypatch.setattr(identity_service, "search_identity", lambda *_: {"resolution": "exact", "exact": {"company_id": "company-id"}})
    result = identity_service.resolve_candidate_identity({"supplier_name": "示例科技"})
    assert result == {"identity_status": "exact", "company_id": "company-id", "identity_candidates": []}

def test_ambiguous_identity_never_receives_a_company_id(monkeypatch):
    monkeypatch.setattr(identity_service, "search_identity", lambda *_: {"resolution": "candidates", "candidates": [{"company_id": "a"}, {"company_id": "b"}]})
    assert identity_service.resolve_candidate_identity({"supplier_name": "示例科技"})["company_id"] is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && pytest tests/test_sourcing_risk_identity_service.py -v`

Expected: FAIL because identity service does not exist.

- [ ] **Step 3: Implement the exact adapter**

Call `search_identity(candidate["unified_social_credit_code"] or candidate["supplier_name"])`. Copy the P1 returned canonical `company_id` only for `exact`; record P1 candidates and source snapshots for ambiguous results; set no score eligibility for ambiguous/pending candidates. Do not create or merge a company here.

- [ ] **Step 4: Run focused tests**

Run: `cd backend && pytest tests/test_sourcing_risk_identity_service.py -v`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/domains/sourcing_risk/identity_service.py backend/tests/test_sourcing_risk_identity_service.py
git commit -m "feat: bind sourcing candidates to company identity"
```

## Phase C — Evidence, deterministic decisions, and graph orchestration

### Task 9: Normalize evidence and enforce evidence safety states

**Files:**
- Create: `backend/app/domains/sourcing_risk/evidence_service.py`
- Test: `backend/tests/test_sourcing_risk_evidence_service.py`

**Interfaces:**
- Produces: `normalize_evidence(run_id: str, company_id: str, dimension: str, provider_result: dict) -> EvidenceRecord`, `validate_evidence_set(evidence: list[dict], policy: dict) -> dict`.

- [ ] **Step 1: Write failing evidence tests**

```python
def test_missing_sanctions_evidence_requires_review():
    outcome = evidence_service.validate_evidence_set([], _policy_requiring_sanctions())
    assert outcome["status"] == "needs_review"
    assert outcome["reason_codes"] == ["SANCTIONS_DATA_UNAVAILABLE"]

def test_conflicting_key_evidence_requires_review():
    outcome = evidence_service.validate_evidence_set([_sanctions("clear"), _sanctions("hit")], _policy_requiring_sanctions())
    assert outcome["status"] == "needs_review"
    assert "KEY_EVIDENCE_CONFLICT" in outcome["reason_codes"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && pytest tests/test_sourcing_risk_evidence_service.py -v`

Expected: FAIL because evidence service does not exist.

- [ ] **Step 3: Implement standard evidence conversion**

Use the `EvidenceRecord` contract from this plan. Persist structured fields through `agent_run.repo.insert_evidence`; persist raw external payloads in Mongo collection `agent_evidence_payloads` keyed by `raw_payload_ref`. `freshness_status` must use policy freshness windows. `validate_evidence_set()` returns `clear`, `incomplete`, or `needs_review` and reason codes; it must never convert absent evidence into `clear`.

- [ ] **Step 4: Run focused tests**

Run: `cd backend && pytest tests/test_sourcing_risk_evidence_service.py -v`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/domains/sourcing_risk/evidence_service.py backend/tests/test_sourcing_risk_evidence_service.py
git commit -m "feat: add sourcing risk evidence model"
```

### Task 10: Implement decision engine with gates, penalties, and stable ranking

**Files:**
- Create: `backend/app/domains/sourcing_risk/decision_service.py`
- Test: `backend/tests/test_sourcing_risk_decision_service.py`

**Interfaces:**
- Produces: `decide_candidates(requirement, policy_snapshot, candidates, evidence_by_company_id) -> list[dict]`.

- [ ] **Step 1: Write failing golden-case tests**

```python
def test_sanctions_hit_is_rejected_even_with_high_other_scores():
    decisions = decide_candidates(_requirement(), _policy(), [_candidate("a")], {"a": [_sanctions("hit")]})
    assert decisions[0]["group"] == "rejected"
    assert decisions[0]["final_score"] is None

def test_missing_financial_data_penalizes_but_does_not_claim_low_risk():
    decision = decide_candidates(_requirement(), _policy(), [_candidate("a")], {"a": [_sanctions("clear")]})[0]
    assert decision["group"] == "alternative"
    assert "MISSING_FINANCIAL_DATA" in decision["reason_codes"]
    assert decision["confidence"] < 1

def test_equal_scores_sort_by_company_id():
    decisions = decide_candidates(_requirement(), _policy(), [_candidate("b"), _candidate("a")], _clear_evidence())
    assert [item["company_id"] for item in decisions] == ["a", "b"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && pytest tests/test_sourcing_risk_decision_service.py -v`

Expected: FAIL because decision service does not exist.

- [ ] **Step 3: Implement the pure decision function**

Evaluate identity and minimum evidence first. Then apply hard gates in policy order. For eligible candidates calculate exactly:

```python
final_score = sum(
    policy_snapshot["weights"][name] * dimension_scores[name]
    for name in policy_snapshot["weights"]
) - missing_data_penalty - stale_data_penalty
```

Clamp to `0..100`, set `group` to `recommended`, `alternative`, `rejected`, or `needs_review`, and sort by `(group_order, -final_score, company_id)`. Return dimension scores, penalty values, confidence, reason codes, and evidence IDs. No LLM import is allowed in this module.

- [ ] **Step 4: Run focused tests**

Run: `cd backend && pytest tests/test_sourcing_risk_decision_service.py -v`

Expected: PASS with exact score assertions for every golden fixture.

- [ ] **Step 5: Commit**

```bash
git add backend/app/domains/sourcing_risk/decision_service.py backend/tests/test_sourcing_risk_decision_service.py
git commit -m "feat: add deterministic sourcing decision engine"
```

### Task 11: Compose SourcingRiskGraph and controlled provider execution

**Files:**
- Create: `backend/app/graphs/sourcing_risk_v2/state.py`
- Create: `backend/app/graphs/sourcing_risk_v2/nodes.py`
- Create: `backend/app/graphs/sourcing_risk_v2/__init__.py`
- Create: `backend/app/graphs/sourcing_risk_v2/runner.py`
- Test: `backend/tests/test_sourcing_risk_graph.py`

**Interfaces:**
- Produces: `build_sourcing_risk_graph(checkpointer)`, `start_sourcing_risk_graph(run_id: str) -> None`, `resume_sourcing_risk_graph(run_id: str) -> None`.

- [ ] **Step 1: Write failing graph routing tests**

```python
@pytest.mark.asyncio
async def test_ambiguous_identity_interrupts_before_investigation(monkeypatch):
    graph = build_sourcing_risk_graph(InMemorySaver())
    monkeypatch.setattr(nodes, "resolve_candidate_identity", lambda _: {"identity_status": "candidates"})
    result = await graph.ainvoke(_state_with_candidate(), {"configurable": {"thread_id": "run-id"}})
    assert result["status"] == "IDENTITY_REVIEW"
    assert result["next_action"] == "identity_review_required"

def test_v2_graph_never_imports_process_local_interrupt_store():
    source = Path(nodes.__file__).read_text()
    assert "interrupt_store" not in source
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && pytest tests/test_sourcing_risk_graph.py -v`

Expected: FAIL because the V2 graph does not exist.

- [ ] **Step 3: Implement graph nodes and routes**

Define graph state containing only `run_id`, `status`, `requirement_id`, `policy_snapshot_id`, candidate IDs, pending review IDs, event cursor, and error code. Build this route:

```text
load_run → parse_requirement → lock_policy → local_discovery
→ external_discovery? → identity_resolution → investigate_parallel
→ validate_evidence → score_candidates → ready_for_review → END
```

Use `asyncio.gather` with explicit per-provider `asyncio.wait_for(..., timeout=20)` for independent financial, judicial, sentiment, sanctions, ESG, and continuity fetches. Retry transient provider errors at most three times with exponential backoff. If sanctions fails, set candidate to `NEEDS_REVIEW`; for noncritical failures, preserve successful evidence and complete `PARTIAL`. Every node calls service methods that append a typed event; nodes themselves do not issue direct SQL/Mongo writes.

- [ ] **Step 4: Run focused tests**

Run: `cd backend && pytest tests/test_sourcing_risk_graph.py -v`

Expected: PASS for complete local flow, clarification, identity review, sanctions failure-closed, conflict review, and partial provider failure.

- [ ] **Step 5: Commit**

```bash
git add backend/app/graphs/sourcing_risk_v2 backend/tests/test_sourcing_risk_graph.py
git commit -m "feat: orchestrate sourcing risk agent graph"
```

## Phase D — External import approval and Outbox actions

### Task 12: Add approved action gateway and idempotent Outbox dispatch

**Files:**
- Create: `backend/app/domains/sourcing_risk/action_service.py`
- Modify: `backend/app/domains/outbox/service.py`
- Modify: `backend/app/domains/outbox/worker.py`
- Test: `backend/tests/test_sourcing_risk_actions.py`

**Interfaces:**
- Produces: `create_action_proposal`, `decide_action_proposal`, `execute_sourcing_risk_action(event: dict) -> None`.

- [ ] **Step 1: Write failing approval and replay tests**

```python
def test_unapproved_import_never_enqueues_outbox_event(monkeypatch):
    enqueue = Mock()
    monkeypatch.setattr(action_service, "enqueue_event", enqueue)
    action_service.create_action_proposal(_import_proposal())
    enqueue.assert_not_called()

def test_same_approved_action_is_enqueued_once(monkeypatch):
    enqueue = Mock()
    monkeypatch.setattr(action_service, "enqueue_event", enqueue)
    action_service.decide_action_proposal("run", "approval", _approved(version=3), "reviewer", "admin")
    action_service.decide_action_proposal("run", "approval", _approved(version=3), "reviewer", "admin")
    assert enqueue.call_count == 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && pytest tests/test_sourcing_risk_actions.py -v`

Expected: FAIL because action service does not exist.

- [ ] **Step 3: Implement proposal, approval, and consumers**

Support action types `import_external_supplier`, `add_watchlist`, `submit_access_application`, and `export_report`. `create_action_proposal()` only persists the proposal. `decide_action_proposal()` requires expected Run version, an authorized reviewer, a pending proposal, and non-self approval for high-risk imports; on approval it inserts both decision and an `agent.action.approved` Outbox event in one PostgreSQL transaction. Register `execute_sourcing_risk_action` as an Outbox consumer. The import consumer first checks the action idempotency key and then calls an explicit supplier-master import adapter; it must not call legacy `resolve_supplier_id(auto_create=True)`.

Cap the V2 action event at five attempts independently of the global worker default. On success/failure/dead letter append `action_status`; mark Run `ACTION_FAILED` only after a dead letter, never when analysis succeeded.

- [ ] **Step 4: Run focused tests**

Run: `cd backend && pytest tests/test_sourcing_risk_actions.py -v`

Expected: PASS, including rejected actions, stale approvals, duplicate replay, and five-attempt dead letter behavior.

- [ ] **Step 5: Commit**

```bash
git add backend/app/domains/sourcing_risk/action_service.py backend/app/domains/outbox/service.py backend/app/domains/outbox/worker.py backend/tests/test_sourcing_risk_actions.py
git commit -m "feat: approve and execute sourcing risk actions"
```

## Phase E — Frontend, Eval, rollout, and release gates

### Task 13: Build the V2 sourcing and risk workbench

**Files:**
- Modify: `frontend/src/api.ts`
- Modify: `frontend/src/types.ts`
- Modify: `frontend/src/query-keys.ts`
- Create: `frontend/src/hooks/useSourcingRiskRun.ts`
- Create: `frontend/src/components/SourcingRiskWorkbench.tsx`
- Create: `frontend/src/components/SourcingRiskCandidateCard.tsx`
- Create: `frontend/src/components/SourcingRiskApprovalCard.tsx`
- Modify: `frontend/src/components/SourcingPage.tsx`
- Test: `frontend/src/__tests__/SourcingRiskWorkbench.test.tsx`

**Interfaces:**
- Consumes: V2 API contracts from Tasks 1 and 3.
- Produces: default `/sourcing` V2 workbench while preserving legacy history navigation.

- [ ] **Step 1: Write failing UI tests**

```tsx
it('shows an identity review card and does not display a recommendation', async () => {
  server.use(http.get('/api/v1/agent-runs/run-1', () => HttpResponse.json(identityReviewRun)));
  render(<SourcingRiskWorkbench initialRunId="run-1" />);
  expect(await screen.findByText('请确认企业主体')).toBeInTheDocument();
  expect(screen.queryByText('推荐供应商')).not.toBeInTheDocument();
});

it('sends expected_version when approving an action', async () => {
  const request = await captureApprovalRequest(render(<SourcingRiskApprovalCard proposal={proposal} runVersion={4} />));
  expect(request.body).toContain('"expected_version":4');
});
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd frontend && npm test -- SourcingRiskWorkbench.test.tsx --run`

Expected: FAIL because the workbench components do not exist.

- [ ] **Step 3: Implement query and SSE resume behavior**

Add `agentRunEventStream(runId, lastEventId, callbacks)` using `fetch`, `credentials: 'same-origin'`, and `Last-Event-ID`. Store the last event ID per Run in `sessionStorage` under `agent_run_event_cursor:<runId>`. On each event update the Query cache; on reconnect begin from that ID; on `done` invalidate `queryKeys.agentRunDetail(runId)`. Render requirement, stage timeline, source badge (`local`/`staged_external`), identity review, evidence freshness/conflict labels, decision groups, and approval cards. Use existing Tailwind tokens and no component library.

- [ ] **Step 4: Run focused tests and static checks**

Run: `cd frontend && npm test -- SourcingRiskWorkbench.test.tsx --run && npm run lint && npm run build`

Expected: PASS; no TypeScript unused-local error.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/api.ts frontend/src/types.ts frontend/src/query-keys.ts frontend/src/hooks/useSourcingRiskRun.ts frontend/src/components/SourcingRiskWorkbench.tsx frontend/src/components/SourcingRiskCandidateCard.tsx frontend/src/components/SourcingRiskApprovalCard.tsx frontend/src/components/SourcingPage.tsx frontend/src/__tests__/SourcingRiskWorkbench.test.tsx
git commit -m "feat: add sourcing risk agent workbench"
```

### Task 14: Add offline Eval suite, metrics, feature flags, and release runbook

**Files:**
- Create: `backend/tests/evals/sourcing_risk_cases.json`
- Create: `backend/tests/test_sourcing_risk_evals.py`
- Modify: `backend/app/core/metrics.py`
- Modify: `backend/app/core/config.py`
- Modify: `README.md`

**Interfaces:**
- Produces: `run_sourcing_risk_evals(cases_path: str) -> dict` and low-cardinality metrics for run status, stage duration, provider outcome, candidate group, action outcome and Eval result.

- [ ] **Step 1: Write failing Eval and metric tests**

```python
def test_golden_evals_have_no_critical_misrecommendation(tmp_path):
    report = run_sourcing_risk_evals(_fixture_path())
    assert report["scoring_pass_rate"] == 1.0
    assert report["critical_missing_evidence_recommendations"] == 0

def test_metrics_do_not_use_company_or_run_as_labels():
    assert "company_name" not in AGENT_RUNS_TOTAL._labelnames
    assert "run_id" not in AGENT_RUNS_TOTAL._labelnames
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && pytest tests/test_sourcing_risk_evals.py -v`

Expected: FAIL because Eval runner and V2 metrics do not exist.

- [ ] **Step 3: Implement fixed fixtures and rollout controls**

Create at least twelve JSON fixtures matching the design acceptance scenarios: complete local flow, clarification, external staging without import, approved import, ambiguous identity, sanctions unavailable, missing financial data, conflicting evidence, approval replay, restart resume, foreign authorization, and V2 fallback. Add flags `AGENT_RUN_V2_ENABLED`, `AGENT_RUN_V2_ROLLOUT` (`shadow|internal|canary|default`), and `AGENT_RUN_V2_CANARY_PERCENT` (`0..100`). In Shadow, execute and persist V2 but return legacy sourcing UI data; in Internal only admin/analyst may create; in Canary route a deterministic hash of user ID; Default enables all. Document rollback as setting rollout to `shadow` or `internal`, without deleting Runs/checkpoints.

- [ ] **Step 4: Run focused tests**

Run: `cd backend && pytest tests/test_sourcing_risk_evals.py -v`

Expected: PASS with a JSON report that includes requirement extraction, tool selection, evidence support and scoring pass rates.

- [ ] **Step 5: Commit**

```bash
git add backend/tests/evals/sourcing_risk_cases.json backend/tests/test_sourcing_risk_evals.py backend/app/core/metrics.py backend/app/core/config.py README.md
git commit -m "test: add sourcing risk agent release gates"
```

### Task 15: Execute release verification and acceptance checklist

**Files:**
- Modify: `README.md`
- Test: all files created in Tasks 1–14.

**Interfaces:**
- Consumes: the complete V2 implementation.
- Produces: recorded evidence that the phase is eligible for `Shadow`; no default-route cutover yet.

- [ ] **Step 1: Run backend unit, graph, action, API, and Eval suites**

Run: `cd backend && pytest -q`

Expected: PASS, including 100% scoring golden cases and zero critical missing-evidence recommendations.

- [ ] **Step 2: Run frontend verification**

Run: `cd frontend && npm run lint && npm run build && npm test -- --run`

Expected: PASS.

- [ ] **Step 3: Verify the Compose configuration without starting production services**

Run: `docker compose config`

Expected: exit code 0 and resolved service configuration.

- [ ] **Step 4: Run the controlled manual acceptance matrix**

Use an admin account and a viewer account to verify all twelve design scenarios. Record for each case: Run ID, policy snapshot version, final status, event replay result, whether an action proposal was generated, and evidence IDs. Confirm that a viewer cannot read or approve an unrelated Run, no external staged candidate appears in the supplier library before approval, and submitting the same approval twice creates only one Outbox effect.

- [ ] **Step 5: Enable Shadow rollout and commit operational documentation**

Set `AGENT_RUN_V2_ROLLOUT=shadow` only in the local/development environment documented in `README.md`; do not alter production environment files. Commit the completed runbook and acceptance evidence template.

```bash
git add README.md
git commit -m "docs: document agent v2 shadow rollout"
```

## Final implementation handoff checks

- `agent_runs` remains the business source of truth; the LangGraph checkpoint is only execution recovery state.
- V2 has no imports from `backend/app/graphs/interrupt_store.py`.
- All business writes originate only from approved proposals and execute through Outbox.
- Every `candidate_decisions` row contains its policy snapshot/version, reason codes, score inputs and evidence IDs.
- All V2 SSE events include monotonically increasing `id:` values and can replay after `Last-Event-ID`.
- Old sourcing and chat endpoints pass their existing test suites unchanged.
- Shadow precedes Internal, Canary and Default; moving to the next rollout stage requires the quantitative gates in the confirmed design specification.
