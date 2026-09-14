# Agent Harness 统一运行时设计与实施计划

日期：2026-09-01
状态：历史 Harness 契约骨架建设计划，Task 1-10 已完成；2026-09-03 起由 2026-09-03-agent-harness-hardening-plan.md 替代
适用分支：`refactor/agent-harness-runtime` 及其后续 Agent 功能分支
适用范围：AI 工作台、智能寻源、供应商风险/ESG/舆情/合规分析、风险监控提案与人工审批

## 1. 文档治理

本文件保留当时的 Agent 契约骨架、实施顺序和验收记录。实际代码审计发现多项契约尚未贯穿生产活动链路，后续修复、任务顺序和验收门槛统一以 2026-09-03-agent-harness-hardening-plan.md 为准。

- `docs/superpowers/plans/2026-08-27-sourcing-risk-feishu-plan.md` 已完成并转为历史实施记录。
- 原 2026-08-18 统一架构设计已被清理；当前架构、任务顺序和验收门槛统一以 2026-09-03 加固计划为准。
- `docs/release/agent-acceptance-and-evaluation.md` 保留历史验收记录；本文件第 12 节是 Harness 改造后的活动验收门槛。
- 历史 V2、Supervisor 设计与实施文档已清理；需要追溯时以 Git 历史、问题日志和本计划的验收记录为准。
- 实施中发现的问题必须先写入 `docs/issue-log.md`，再修复并回填验证结果和提交号。

## 2. 背景与审计结论

当前项目已经具备 LangGraph、会话快照、供应商引用、Agent Run、证据模型、审批、Outbox、Trace 和大量测试，但这些能力分散在多套执行路径中：

- 聊天 `auto` 可路由到 ReAct、Plan-Execute、旧 Supervisor、Sourcing、Parallel、Reflection 和 Agent Supervisor。
- Sourcing Risk V2 另有独立 Run、图状态、API 和恢复路径。
- Mongo 会话、PostgreSQL Agent Run、LangGraph checkpoint、Redis 会话锁和进程内审批暂停同时保存部分运行状态。
- Supervisor/V2 具备证据校验，而普通 ReAct 等路径仍可直接由模型根据原始工具结果形成答案。
- 现有 Agent/V2 定向测试 360 项通过，`agent_e2e` 仅 9 项，尚不能证明完整真实链路稳定。

对应问题：

- `ISS-20260901-020`：多套执行内核与状态模型。
- `ISS-20260901-021`：实体记忆缺少稳定身份和作用域。
- `ISS-20260901-022`：工具缺少统一结果信封和写策略。
- `ISS-20260901-023`：真实性校验未覆盖全部活动路径。
- `ISS-20260901-024`：E2E/Eval 尚不能作为 Harness 门槛。

## 3. 已确认的产品与架构边界

### 3.1 产品边界

- Agent 负责供应商推荐、风险分析、ESG、舆情、合规和风险监控建议。
- Agent 不执行供应商准入、注册、主数据编辑或飞书回写。
- 外部候选只作为待核验候选，不自动成为正式供应商。
- 所有业务写操作必须先生成提案并由人工确认。
- 当前以开发环境可运行和稳定验收为目标，不扩展生产部署任务。

### 3.2 Harness 边界

- 所有聊天请求进入唯一 `Agent Harness Runtime`。
- 寻源、风险、ESG、舆情和合规是能力模块，不再拥有独立完整运行时。
- PostgreSQL 是 Agent 会话、Turn、Run、Task、审批、事件和状态版本的唯一事实源。
- MongoDB 只保存供应商业务快照、外部原始证据、风险结果和对话展示记录。
- Redis 只承担会话租约、缓存和限流，不承担不可替代的运行状态。
- LangGraph checkpoint 只保存图执行位置，不作为业务会话事实源。
- 所有工具必须经过统一 `ToolExecutor`。
- 所有确定性结论必须引用有效 Evidence。
- 最终回答必须由 `AgentAnswer` 契约生成。

## 4. 目标架构

```text
Frontend AI Workbench
  -> POST /api/v1/chat/stream
  -> Agent Harness API
  -> SessionStateStore (PostgreSQL, versioned)
  -> Turn Resolver
       -> Intent Resolver
       -> Entity Resolver + Focus Set
  -> Task Planner
  -> Unified LangGraph Runtime
       -> execute_ready_tasks
       -> ToolExecutor
            -> Tool Registry / Policy
            -> Domain Services
            -> Provider Adapters
       -> Evidence Ledger
       -> Claim Validator
       -> bounded remediation loop
       -> Action Proposal / Approval Gate
       -> AgentAnswer Renderer
  -> Run Event Stream / SSE
  -> Persist Turn + Final State
```

依赖方向保持：

```text
api -> harness/graphs -> tool executor -> services -> repositories/providers
```

Service 层继续保持框架无关，不依赖 LangGraph。

## 5. 统一状态模型

### 5.1 身份层级

```text
Session：一段连续用户对话
  -> Turn：一次不可变的用户请求
      -> Run：本次请求的一次执行实例
          -> Task/Subtask：可独立执行、重试和验收的工作单元
```

- `session_id` 不再直接充当 `run_id`。
- `turn_id` 在同一用户请求重试时保持不变。
- `run_id` 每次执行唯一，可恢复、取消或重新运行。
- `task_id` 对应一个实体和一个能力维度，或一个明确的寻源阶段。

### 5.2 AgentRunContext

```python
class AgentRunContext(BaseModel):
    schema_version: int
    session_id: str
    turn_id: str
    run_id: str
    state_version: int
    user_id: str
    user_message: str
    intent: dict
    entity_memory: dict
    focus_entity_ids: list[str]
    current_task: dict
    tool_calls: list[dict]
    evidence_refs: list[str]
    pending_actions: list[dict]
    execution_budget: dict
    status: str
```

约束：

- 每个 HTTP 请求只加载一次上下文快照。
- 图节点不得自行重新读取对话历史并推导另一份状态。
- 节点只能返回契约允许的状态增量。
- 状态使用 `state_version` 乐观锁更新；版本冲突时停止当前 Run。
- PostgreSQL 状态提交成功后才能进入下一个关键阶段。

### 5.3 PostgreSQL 控制面

计划新增或统一以下逻辑实体；具体迁移优先复用现有 `agent_runs`、事件和审批表：

| 实体 | 作用 |
|---|---|
| `agent_sessions` | 会话所有者、状态版本、当前焦点与时间 |
| `agent_turns` | 不可变用户输入、解析快照和最终回答引用 |
| `agent_runs` | 执行状态、预算、错误、开始/结束时间 |
| `agent_tasks` | 任务矩阵、依赖、状态、尝试次数 |
| `agent_entities` | 会话实体记忆、身份状态和别名 |
| `agent_tool_calls` | 工具调用输入哈希、结果、证据和耗时 |
| `agent_action_proposals` | 待审批写操作、动作哈希和幂等键 |
| `agent_run_events` | 可重放、无隐藏推理的执行事件 |

## 6. 实体记忆与焦点解析

### 6.1 实体身份

| 实体类型 | 稳定主键 | 身份状态 |
|---|---|---|
| 正式供应商 | `supplier_id` | `verified` |
| 外部待核验候选 | `candidate_id` | `candidate` |
| 尚未完成解析的企业 | `temp_entity_id` | `ambiguous` / `unresolved` |

企业名称、简称、供应商代码都只是别名，不作为状态主键。

```python
class SupplierEntity(BaseModel):
    entity_id: str
    entity_type: str
    canonical_name: str
    aliases: list[str]
    supplier_code: str | None
    identity_status: str
    source_refs: list[str]
    first_seen_turn_id: str
    last_seen_turn_id: str
```

### 6.2 焦点规则

目标解析按以下顺序执行：

1. 当前消息明确出现的企业全称或已知别名；
2. 当前消息明确出现的供应商代码；
3. “这两家、上述企业、它们”等引用上一轮 `focus_entity_ids`；
4. “这家、它”等引用上一轮主焦点实体；
5. 无法唯一确定时生成结构化澄清，不猜测。

附加约束：

- 当前显式实体优先于历史推荐结果。
- “这些企业”只引用上一轮焦点集合，不引用会话全部历史企业。
- LLM 可提出实体候选，确定性 Resolver 负责绑定或标记待核验。
- 不再从助手自然语言回答正则回填已验证身份。
- 跨会话只复用全局供应商身份和用户偏好，不复用焦点集合。

## 7. Tool Registry 与 ToolExecutor

### 7.1 ToolSpec

```python
class ToolSpec(BaseModel):
    name: str
    version: str
    capability: str
    side_effect: Literal["read", "write"]
    approval_policy: Literal["none", "required"]
    timeout_seconds: int
    max_attempts: int
    idempotent: bool
    evidence_required: bool
```

每个注册工具还必须绑定明确的 Pydantic 输入和输出模型。LangChain `@tool` 只暴露调用入口，实际执行统一委托给 `ToolExecutor`。

### 7.2 ToolOutcome

```python
class ToolOutcome(BaseModel):
    schema_version: int = 1
    call_id: str
    tool_name: str
    tool_version: str
    status: Literal[
        "success", "partial", "not_found", "unavailable",
        "invalid", "denied", "failed"
    ]
    data: dict
    evidence_refs: list[str]
    error: dict | None
    side_effect_receipt: dict | None
    metrics: dict
```

执行顺序固定为：

1. 输入 schema 校验；
2. 用户权限和工具策略校验；
3. 副作用与审批令牌校验；
4. Run 预算、超时和并发限制；
5. 调用领域 Service；
6. 输出 schema 校验；
7. Evidence 标准化与保存；
8. 写入 ToolCall 事件；
9. 返回 `ToolOutcome`。

### 7.3 写操作安全

- 模型只能生成 `ActionProposal`，不能直接执行写操作。
- 用户审批令牌必须绑定 `run_id + proposal_id + action_hash + user_id + expires_at`。
- 无有效令牌时返回 `denied / approval_required`。
- 写操作必须具有幂等键；重复确认最多产生一次业务效果。
- 成功回答必须引用 `SideEffectReceipt`；没有回执不得说“已完成”。
- 现有审批上下文异常后默认放行的路径必须删除。

## 8. Evidence、Claim 与 AgentAnswer

### 8.1 EvidenceRecord

证据状态固定为：

- `available`：成功获得有效数据；
- `confirmed_empty`：提供方明确确认没有记录；
- `missing`：尚未取得或尚未查询；
- `unavailable`：提供方失败、无权限或超时；
- `stale`：超过新鲜度策略；
- `conflicting`：可信来源结论冲突；
- `synthetic`：演示数据，不可用于正式决策。

```python
class EvidenceRecord(BaseModel):
    evidence_id: str
    entity_id: str
    dimension: str
    provider: str
    source_type: str
    endpoint: str | None
    query: dict
    status: str
    collected_at: datetime
    valid_until: datetime | None
    data_mode: Literal["formal", "synthetic"]
    content_hash: str
    raw_payload_ref: str | None
```

`missing`、`unavailable` 和 `confirmed_empty` 必须严格区分；缺失数据不得按零风险处理。

### 8.2 ValidatedClaim

```python
class ValidatedClaim(BaseModel):
    claim_id: str
    entity_id: str
    dimension: str
    statement: str
    value: str | float | int | None
    evidence_refs: list[str]
    confidence: float
    validation_status: Literal[
        "supported", "partial", "conflicting", "unsupported"
    ]
```

- 只有 `supported` 可以表达确定性结论。
- `partial` 必须显示覆盖度和限制。
- `conflicting` 必须列出冲突来源并转人工复核。
- `unsupported` 不得进入最终事实列表。
- Claim 中的企业、数值、等级必须能从 Evidence 精确追溯。

### 8.3 AgentAnswer

```python
class AgentAnswer(BaseModel):
    status: Literal["completed", "partial", "needs_review", "failed"]
    summary: str
    claims: list[ValidatedClaim]
    limitations: list[str]
    action_proposals: list[dict]
    action_receipts: list[dict]
    evidence_refs: list[str]
```

前端卡片直接消费结构化结果。LLM 只能根据锁定后的 Claim 润色摘要，不得增加不存在的企业、数字、风险等级或执行状态。

## 9. 唯一 LangGraph Runtime

```text
START
  -> load_session
  -> resolve_turn
  -> resolve_entities
  -> build_plan
  -> execute_ready_tasks
  -> validate_evidence
       -> remediation_loop（仅缺失维度且预算允许）
       -> build_claims
       -> partial_answer / needs_review
  -> propose_actions
  -> approval_gate
       -> INTERRUPT（持久化到 PostgreSQL）
       -> execute_approved_actions
  -> render_answer
  -> persist_turn
END
```

LLM 负责意图理解、受约束计划和基于已验证 Claim 的语言表达；状态更新、工具成功判定、Evidence、Claim、审批和完成状态由确定性代码负责。

## 10. 有限 Loop、预算与失败语义

### 10.1 允许的 Loop

| Loop | 触发条件 | 上限 | 停止条件 |
|---|---|---:|---|
| 寻源补充 | 当前候选不足 | 本地/飞书、天眼查、联网各一次 | 候选足够或来源耗尽 |
| 证据补全 | 关键维度缺失或过期 | 1 轮，最多 2 个补采工具 | 通过、部分完成或转人工 |
| 提供方重试 | 超时、连接失败、429 | 1 次 | 成功或结构化失败 |

不保留通用 Self-Reflection Loop；确定性评分、审批和写操作不进入自动 Loop。

### 10.2 默认预算

```python
ExecutionBudget(
    max_llm_calls=4,
    max_tool_calls=12,
    max_loop_iterations=2,
    max_duration_seconds=120,
    max_parallel_tasks=4,
)
```

寻源任务可单独放宽到 180 秒。预算耗尽后返回 `partial` 或 `needs_review`，不得无限扩张任务。

### 10.3 失败语义

| 失败 | 结果 |
|---|---|
| 非关键工具失败 | 保留其他证据，返回 `partial` |
| 关键证据缺失 | `needs_review`，不输出确定性结论 |
| 状态、schema 或身份契约损坏 | `failed`，立即停止 |
| 等待人工确认 | `waiting_approval`，不是失败 |
| 状态提交失败 | 不返回 `completed`，保留可恢复 Run |
| 写操作无回执 | 不返回写入成功声明 |

## 11. SSE、Trace 与前端职责

统一事件至少包含：

```text
session
workflow_status
entity_resolved
plan_created
tool_call
tool_result
evidence_status
claim_validated
approval_required
action_receipt
answer_chunk
done
error
```

Trace 保存 `session_id`、`turn_id`、`run_id`、`state_version`、节点、工具版本、输入哈希、结果状态、证据引用、审批引用、写入回执、耗时和错误码；不保存模型隐藏推理。

前端不再根据工具名称猜测 Agent 状态，只消费统一事件和 `AgentAnswer`：

- 明确显示 `completed / partial / needs_review / failed / waiting_approval`。
- 风险数字和结论提供证据入口。
- 演示数据固定显示“不可用于正式决策”。
- 审批卡只展示 `ActionProposal`，完成后展示真实回执。
- 浏览器刷新后通过 `run_id` 恢复状态和事件游标。

## 12. E2E Harness 与质量门槛

### 12.1 Harness 组成

```text
Scenario
  -> 真实 FastAPI/SSE
  -> 真实 Unified LangGraph Runtime
  -> 真实 ToolExecutor
  -> 可编程 LLM Double
  -> 录制并脱敏的飞书/天眼查/联网 Provider Double
  -> PostgreSQL + MongoDB + Redis 测试实例
  -> 状态、Evidence、Claim、事件和写入联合断言
```

外部服务可替换；Harness Runtime、工具执行器、审批、数据库和 SSE 不替换。

### 12.2 P0 固定场景

1. 推荐供应商后对“这两家”进行风险分析；
2. 再对“它们”进行 ESG 和舆情分析；
3. 当前消息出现新企业时不依赖推荐历史；
4. 同名或身份不明确时要求人工选择；
5. 本地不足后依次进入天眼查和联网发现；
6. 缺少风险数据时不得输出低风险；
7. 多来源冲突时转人工复核；
8. 提供方超时后只重试一次；
9. 加入监控必须等待人工确认；
10. 浏览器刷新或后端重启后继续审批；
11. 重复确认只产生一次业务写入；
12. 同会话并发请求不污染工具消息；
13. PostgreSQL提交失败时不得返回完成；
14. 工具非法输出被 schema 拒绝；
15. LLM 生成无证据企业、数字或结论时被拒绝。

### 12.3 CI 门槛

| 指标 | P0 门槛 |
|---|---:|
| 固定场景通过率 | 100% |
| 实体焦点正确率 | 100% |
| 工具结果契约合规率 | 100% |
| 确定性 Claim 证据支持率 | 100% |
| 缺失数据误判为低风险 | 0 |
| 未审批写操作 | 0 |
| 错误成功声明 | 0 |
| 重复写入 | 0 |
| 重启恢复成功率 | 100% |
| 未完成 tool call 进入下一轮 | 0 |

同一自然语言场景应支持重复运行 10～20 次，比较实体、计划、工具和 `AgentAnswer` 结构稳定性，不比较表面措辞。

## 13. 实施任务

### Task 1：冻结 Harness 核心契约与 PostgreSQL 控制面（P0）

- [x] 定义 Session、Turn、Run、Task、Entity、ToolCall、ActionProposal 契约。
- [x] 设计并迁移 PostgreSQL 表，优先复用现有 Agent Run 表和事件表。
- [x] 实现 `SessionStateStore`、状态版本和乐观锁。
- [x] 明确 Mongo 会话文档的兼容读取和退出路径。

验收：状态模型可序列化、可迁移、可并发冲突检测；PostgreSQL 是唯一运行状态来源。已通过 5 项离线契约测试、1 项 PostgreSQL 控制面集成测试，以及既有 Agent Run/API/Service/Model 97 项回归测试。

### Task 2：实现统一实体记忆与 Turn Resolver（P0）

- [x] 实现 EntityMemory、Mention、FocusSet 和身份状态。
- [x] 实现显式名称、供应商代码、别名、单复数指代和序数解析。
- [x] LLM 解析只返回候选，确定性 Resolver 完成绑定。
- [x] 删除“最近助手引用覆盖累计状态”的活动行为。

验收：P0 多轮实体场景 100% 通过；新会话不继承旧焦点。已通过实体记忆、上下文、适配器、控制面和 Agent Run/API/Service/Model 共 131 项定向测试，并通过 PostgreSQL 实体记忆持久化集成测试。

### Task 3：实现 Tool Registry 与 ToolExecutor（P0）

- [x] 定义 ToolSpec、ToolContext、ToolOutcome、ToolError、ToolMetrics。
- [x] 集中实现权限、审批、预算、超时、重试、输出校验和结果回调。
- [x] 建立工具注册完整性测试矩阵。
- [x] 先迁移寻源、风险、ESG、舆情、合规全部只读工具。

验收：ReAct 与 Plan-Execute 的工具调用经过统一 Registry/ToolExecutor；非法输入、非法输出、未授权和无审批写操作均 fail closed。Supervisor 和统一 Harness 的全面接入留待 Task 5。已通过 ToolExecutor、ReAct、寻源、审批、Supervisor、上下文和 Agent Run/API 共 71 项定向测试。

### Task 4：实现 Evidence Ledger、Claim Validator 与 AgentAnswer（P0）

- [x] 标准化 Evidence 状态、来源、时间、数据模式和原始引用。
- [x] 建立 Claim-Evidence 绑定、冲突和覆盖度校验。
- [x] 实现 AgentAnswer 渲染和模型摘要约束。
- [x] 禁止缺失数据按零风险处理，隔离 synthetic 结论。

验收：所有确定性数字、等级和事实均可追溯；无依据结论率为 0。已通过 91 项证据、答案、上下文、工具和 Agent Run/API 定向测试。统一 Harness 的活动路径接入留待 Task 5。

### Task 5：实现唯一 LangGraph Harness Runtime（P0）

- [x] 建立统一状态图和节点状态机。
- [x] Planner 只生成任务计划，不切换执行引擎。
- [x] 接入 ToolExecutor、Evidence、Claim、有限 Loop 和预算。
- [x] 每个关键节点先持久化再推进。

验收：单一 Runtime 已完成只读寻源计划、单/多企业风险和多维组合分析；通过 6 项 Harness 场景测试及 97 项跨阶段定向回归。聊天 API 活动入口切换与旧图清理纳入 Task 7/Task 9。

### Task 6：迁移写操作提案、审批和恢复（P0）

- [x] 建立 Harness ActionProposal 与统一 ActionGate，监控增删等写工具先生成提案。
- [x] 审批令牌绑定 Session、Run、Proposal、动作哈希、审批人和有效期。
- [x] 复用现有 PostgreSQL Proposal/Outbox 和幂等机制生成可追踪的副作用状态。
- [x] 将 `interrupt_store` 降为兼容缓存，恢复元数据和消费以 PostgreSQL 为准；旧图活动入口清理归入 Task 7/Task 9。

当前验收：ActionGate 和 PostgreSQL Proposal/Outbox 适配已通过审批、持久化重建、幂等和回执安全测试；未带有效签名令牌、错误审批人、动作被篡改、过期提案和缺失副作用回执均 fail closed。旧聊天恢复路径已改为 PostgreSQL 元数据优先、进程内图对象仅作兼容缓存；统一 Chat API 的活动入口切换与旧图清理仍归 Task 7/Task 9。

### Task 7：统一 Chat API、SSE 与前端工作台（P1）

- [x] `/chat/stream` 默认只进入 Harness Runtime；涉及写动作时暂保留持久化 Agent Supervisor 兼容路径。
- [x] 统一 SSE 事件和恢复消费协议，新增 `agent_answer`、`evidence` 事件并兼容现有 `/resume`。
- [x] 前端直接展示 AgentAnswer、Evidence、状态和回执摘要。
- [x] 短期回退观察完成后移除 Chat API 中的旧只读活动入口，普通 `auto` 和旧 mode 均进入 Harness。

当前验收：后端 Harness/Agent E2E 与聊天契约回归通过，前端 53 项测试、Lint、TypeScript 和生产构建通过；真实浏览器已验证供应商库、供应商画像、智能寻源、风险监控和 AI 工作台只读风险查询。

### Task 8：建立真实 E2E Harness 与故障注入（P1）

- [x] 建立可编程 LLM Double 和脱敏 Provider 录制回放。
- [x] 实现第 12.2 节全部 P0 场景。
- [x] 注入超时、429、空结果、冲突、非法输出、数据库提交失败和重启。
- [x] CI 输出固定质量指标并强制门槛。

验收：P0 固定 15 场景重复运行 10 次，共 150 次执行全部通过；实体焦点、工具契约、证据支持和重启恢复率均为 100%，缺失数据误判低风险、未审批写入、错误成功声明、重复写入、未完成 tool call 和稳定性失败均为 0。新增 `backend/tests/test_agent_harness_p0.py` 已纳入 `agent_e2e` CI 集合。

### Task 9：切换默认入口并清理重复路径（P1）

- [x] 在短期观察阶段完成旧入口与新 Harness 对照；观察结束后移除旧聊天兼容开关。
- [x] P0 Harness 通过后将 `auto` 及未授权旧 mode 收敛到唯一 Harness Runtime；涉及人工写操作的 `agent-supervisor` 保留为持久化审批兼容入口。
- [x] 完成短期回退验证后删除旧 ReAct/Plan-Execute/Parallel/旧 Supervisor 的 Chat API 活动适配；底层历史图模块仅保留给不影响运行时的定向回归。
- [x] 删除进程内审批图缓存；恢复入口直接消费 PostgreSQL 元数据并按 mode 重建图。

当前验收：默认聊天路由只有 Harness；只读旧 mode 不再有活动适配，写操作继续走持久化 Supervisor 审批路径；`interrupt_store` 不再缓存编译图。旧图源文件暂作为历史定向回归夹具保留，不再由 Chat API 选择或恢复。

### Task 10：浏览器验收、问题回填与文档收口（P2）

- [x] 使用真实开发环境执行完整浏览器场景：供应商库、画像基础信息/风险/财务/关联/日志、智能寻源、风险监控和 AI 工作台只读风险查询。
- [x] 将 `ISS-20260901-020` 至 `ISS-20260901-024` 回填验证和关联提交；Task10 新发现的画像日期、供货产品聚合和工作流终态问题已修复并回填。
- [x] 更新 README、AGENTS、API 与测试说明，明确 Harness 唯一聊天活动入口、Agent Supervisor 仅用于人工审批写操作，以及供应商准入由外部系统负责。
- [x] 确认仓库中只有本文件声明为活动 Agent 计划；其他计划均为历史实施记录或归档材料。

验收：文档、代码、测试、问题状态和工作台行为一致。Task10 浏览器验收使用本地 FastAPI `8002` + Vite `5174`，固定账号 `admin/admin123`；只读风险查询在缺少有效 Evidence 时返回 `needs_review`，证据 `0/1`，Loop 退出原因 `evidence_incomplete`，不生成确定性风险结论。

## 14. 迁移策略

采用“旁路建设、一次切换、短期回退、最终删除”：

1. 新 Harness 使用独立内部入口开发，复用现有领域 Service。
2. 只读工具优先迁移，不重复调用真实付费提供方做双轨执行。
3. 使用录制 Evidence 对旧/新结果做结构对照。
4. P0 Harness 达标后切换 `auto`。
5. 旧入口只保留一个短期显式回退开关。
6. 验收稳定后删除旧活动图，禁止长期双轨维护。

数据库迁移必须向前兼容；旧 Mongo 会话只读迁移，不执行破坏性批量删除。

## 15. 执行纪律

- 每个阶段最多一次实现和一次复核。
- 同一问题最多修复两轮，仍失败则标记阻塞。
- PostgreSQL、MongoDB 或 Redis 任一不可用时立即停止集成测试，继续不依赖数据库的任务并记录阻塞。
- 子任务只在完成、失败或阻塞时汇报；每 30～60 分钟汇总一次。
- Task 7、Task 8 保持独立，避免前端联调和 Harness 扩展为无限任务。
- 不修改现有公开 API 返回结构；新增结构通过兼容字段或新版本接口引入。
- 不在 Service 层引入 LangGraph 依赖。
- 所有问题先登记，修复后回填验证结果和提交号。

## 16. 总完成标准

本计划完成需要同时满足：

- `auto` 只进入唯一 Agent Harness Runtime。
- PostgreSQL 是 Agent 状态唯一事实源。
- 多轮实体解析不依赖助手自然语言回填。
- 所有活动工具经过 ToolExecutor 并返回 ToolOutcome。
- 所有确定性结论引用有效 Evidence，并由 AgentAnswer 输出。
- 所有写操作经过提案、人工审批、幂等执行和真实回执。
- 只存在三类有限 Loop，预算耗尽可解释退出。
- 后端重启和浏览器刷新后 Run 可恢复。
- 第 12 节 P0 Harness 指标全部达标。
- 旧活动图、重复状态和进程内审批路径完成清理。
- 问题日志、测试、README 和本文档状态一致。
