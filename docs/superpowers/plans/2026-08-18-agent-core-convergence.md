# Agent 核心收敛与受控 Loop 实施计划

日期：2026-08-18  
状态：实施完成，持续回归（2026-08-20）
范围：Agent 核心能力、智能寻源、供应商风险分析、对话工作流；不包含生产部署环境改造

## 1. 目标

将当前多个可调用工具的 LangGraph Agent 收敛为一个统一、有状态、可规划、能补证、可校验、可恢复且所有业务写操作受人工审批保护的采购决策 Agent。

本计划不以增加 Agent 数量为目标，而是优先解决以下问题：

- 会话实体、当前任务和分析范围在不同执行图之间不一致；
- 多企业、多维度请求依赖 LLM 临场理解，缺少确定性任务模型；
- 寻源、证据补全和失败恢复没有统一 Loop 预算与退出条件；
- 子 Agent 输出、证据和完成状态不完全一致；
- 规则预检、意图路由和 LLM 规划存在职责重叠；
- 缺少覆盖真实多轮对话的回归评测和运行轨迹。

## 2. 与现有设计的关系

本计划是当前唯一的 Agent 实施计划，并以以下活动文档为准：

- `docs/superpowers/specs/2026-08-18-agent-unified-architecture.md`：架构、职责和业务边界；
- `docs/release/agent-acceptance-and-evaluation.md`：测试、评测与质量门槛。

历史 V2 和 Supervisor 设计/计划已移动至 `docs/archive/agent-design/`，仅用于实现追溯，不再与本计划竞争。

复用现有能力：

- `agent_run` 持久化运行、事件、审批和恢复边界；
- `agent_supervisor` 的 Planner、Agent Result、Evidence、Decision 和 Approval Gate；
- `sourcing_risk_v2` 的需求、策略、发现、身份、证据和决策服务；
- 现有 ReAct、Sourcing、Parallel、Plan-Execute、Supervisor 图；
- 现有 SSE 工作流、审批卡片和 Agent 工作台。

新增的核心是统一 `ConversationState`、确定性目标解析、任务矩阵、受控 Loop 和全图适配层。

## 3. 当前基线

截至本文档创建时，以下基础已经实现但尚未形成完整收敛：

- [x] 工具结果中的供应商引用可结构化保存；
- [x] 支持“这些企业、上述企业、它们、前两家”等基础指代；
- [x] 初版 `ConversationState` 已包含活跃供应商、选中供应商和当前分析维度；
- [x] ReAct 上下文可注入多个供应商并逐家调用工具；
- [x] 外部供应商发现支持天眼查、联网搜索、官网与联系方式补全；
- [x] 所有业务写操作保持人工确认边界；
- [x] 所有执行图统一消费同一个会话状态；
- [x] 多企业、多维度请求形成持久化任务矩阵；
- [x] 寻源、证据补全、校验和降级使用统一受控 Loop；
- [x] Trace、端到端 Eval 和质量门槛形成闭环。

## 4. 目标架构

```text
用户输入
  -> Conversation State Loader
  -> Entity / Target Resolver
  -> Intent & Task Parser
  -> Task Matrix Planner
  -> Supervisor
       -> Sourcing Loop
       -> Risk / ESG / Sentiment / Compliance Workers
       -> Evidence Completion Loop
       -> Result Validator Loop
  -> Evidence Merger
  -> Decision Builder
  -> Human Approval Gate（仅业务写操作）
  -> Final Answer + Trace + Conversation State Update
```

### 4.1 职责边界

| 组件 | 负责 | 不负责 |
|---|---|---|
| Conversation State | 保存会话事实、目标、当前任务与引用 | 不判断风险结论 |
| Target Resolver | 解析明确名称、单复数指代、序数和排除条件 | 不调用 LLM 补造企业 |
| Task Parser | 识别任务类型和分析维度 | 不执行工具 |
| Task Matrix Planner | 生成企业 × 维度子任务及依赖 | 不生成业务结论 |
| Supervisor | 调度、预算、暂停、恢复和完成判断 | 不直接查询业务库 |
| Worker Agent | 执行一个有边界的专业子任务 | 不扩大任务范围，不写业务数据 |
| Loop Controller | 判断继续、降级、停止或转人工 | 不覆盖工具结果 |
| Validator | 校验证据、覆盖率、一致性和时效 | 不凭空补充证据 |
| Decision Builder | 排序、结论、限制和行动建议 | 不执行建议动作 |
| Approval Gate | 暂停并等待人工批准写操作 | 不自动批准 |

## 5. 统一数据协议

### 5.1 ConversationState

目标结构：

```python
class ConversationState(BaseModel):
    schema_version: int = 1
    session_id: str
    active_suppliers: list[SupplierReference]
    selected_supplier_names: list[str]
    current_requirement: dict | None
    current_task: AgentTask | None
    recent_tasks: list[TaskSummary]
    pending_clarification: dict | None
    pending_approvals: list[str]
    updated_at: datetime
```

约束：

- 结构化状态是唯一事实源；自然语言历史仅用于语义背景；
- 不再以重新解析 assistant 回答作为正常状态恢复方式；
- 旧会话允许一次兼容提取，提取后写入新状态版本；
- 外部候选必须保留来源和 `unverified` 状态；
- 联系方式等扩展字段在状态迁移中不得丢失。

### 5.2 AgentTask 与任务矩阵

```python
class AgentTask(BaseModel):
    task_id: str
    task_type: Literal["sourcing", "analysis", "comparison", "action_draft"]
    target_supplier_names: list[str]
    analysis_dimensions: list[str]
    requirement: dict | None
    subtasks: list[AgentSubtask]
    status: Literal[
        "pending", "running", "completed", "partial",
        "needs_clarification", "waiting_approval", "failed"
    ]

class AgentSubtask(BaseModel):
    subtask_id: str
    supplier_name: str | None
    dimension: str
    depends_on: list[str]
    status: Literal["pending", "running", "completed", "insufficient_evidence", "failed"]
    attempts: int = 0
    evidence_refs: list[str] = Field(default_factory=list)
    error: dict | None = None
```

同一企业、同一维度、同一任务中只能存在一个子任务。失败必须记录，不能转换为空成功结果。

### 5.3 Worker Result

统一复用并扩展现有 `AgentResult`：

```python
{
    "status": "completed",
    "summary": "...",
    "findings": [],
    "evidence": [],
    "confidence": 0.86,
    "data_freshness": "fresh",
    "limitations": [],
    "recommended_actions": [],
    "metrics": {"duration_ms": 800, "attempts": 1},
    "error": None
}
```

完成状态的关键结论必须引用 Evidence；证据不足使用 `insufficient_evidence` 或 `needs_review`，禁止用分析模板伪装完成。

### 5.4 LoopState

```python
class LoopState(BaseModel):
    loop_type: Literal["sourcing", "evidence", "validation", "fallback"]
    iteration: int
    max_iterations: int
    tool_call_count: int
    max_tool_calls: int
    started_at: datetime
    timeout_seconds: int
    previous_fingerprint: str | None
    evidence_count_before: int
    stop_reason: str | None
```

所有 Loop 必须显式记录预算和退出原因。

## 6. 受控 Loop 策略

### 6.1 智能寻源 Loop

```text
本地历史数据
  -> 候选充足度判断
  -> 天眼查发现
  -> 联网发现
  -> 官网与企业详情补全
  -> 企业身份去重
  -> 适配度、风险、证据完整度排序
```

继续条件：候选数量或关键字段未达到品类策略阈值，且本轮有新增有效信息。  
退出条件：达到阈值、连续一轮无新增候选、数据源耗尽、最多 3 轮、达到总时长或工具预算。

### 6.2 证据补全 Loop

根据缺失维度选择对应只读数据源，不允许无目标地重复搜索。

继续条件：存在影响结论的关键证据缺口，且仍有未使用的数据源。  
退出条件：证据达到最低门槛、最多 2 轮、无新增证据、来源耗尽或出现身份冲突。

### 6.3 结果校验 Loop

Validator 检查：

- 目标供应商是否全部覆盖；
- 分析维度是否全部完成；
- 关键结论是否引用证据；
- 风险分与风险等级是否一致；
- 数据时效是否满足策略；
- 是否混淆外部候选和正式供应商；
- 是否存在工具失败被隐藏；
- 是否包含未经审批的写操作结果。

只允许一次补救执行；仍不通过则输出 `partial`、`needs_review` 或 `failed`。

### 6.4 错误降级 Loop

允许路径：

- 临时网络错误：原工具重试一次；
- 本地无结果：进入外部发现；
- 天眼查不可用：降级联网搜索；
- 官网不可访问：保留候选并标记联系方式未核验；
- LLM 失败：保留确定性工具结果并输出部分完成。

禁止相同工具与相同参数无限重复。使用输入参数与工具名生成 fingerprint，重复且无新增证据时立即停止。

### 6.5 不进入 Loop 的操作

- 确定性风险评分计算；
- 低置信度企业身份合并；
- 纳入供应商主库；
- 加入或移出监控；
- 供应商准入、淘汰和处置；
- 发送通知或报告。

上述业务写操作只能生成 Action Proposal，经人工确认后执行并记录审计。

## 7. 文件边界

### 7.1 新建

- `backend/app/services/conversation_state.py`：统一会话状态和兼容迁移；当前已有初版，后续扩展。
- `backend/app/graphs/agent_core/__init__.py`：共享 Agent 核心包。
- `backend/app/graphs/agent_core/contracts.py`：AgentTask、AgentSubtask、LoopState 和解析结果协议。
- `backend/app/graphs/agent_core/resolver.py`：企业目标、指代、序数和排除条件解析。
- `backend/app/graphs/agent_core/planner.py`：任务矩阵和依赖计划。
- `backend/app/graphs/agent_core/loop.py`：统一 Loop Controller、预算和 fingerprint。
- `backend/app/graphs/agent_core/validator.py`：完成度和证据校验。
- `backend/app/graphs/agent_core/trace.py`：阶段、状态变化、Loop 和验证事件。
- `backend/tests/test_agent_core_contracts.py`
- `backend/tests/test_agent_core_resolver.py`
- `backend/tests/test_agent_core_planner.py`
- `backend/tests/test_agent_core_loop.py`
- `backend/tests/test_agent_core_validator.py`
- `backend/tests/evals/agent_conversation_cases.json`
- `backend/tests/test_agent_conversation_evals.py`

### 7.2 修改

- `backend/app/services/agent.py`：只保留会话状态加载、保存和兼容门面。
- `backend/app/api/chat.py`：先解析状态，再澄清或进入统一调度。
- `backend/app/graphs/context.py`：从结构化状态构建模型上下文。
- `backend/app/graphs/router.py`：接收已解析任务，不再重复猜测目标企业。
- `backend/app/graphs/react_graph.py`：接入统一任务与 Worker Result 适配。
- `backend/app/graphs/agents/sourcing.py`：使用 Sourcing Loop。
- `backend/app/graphs/parallel_graph.py`：消费任务矩阵，不自行重新提取企业。
- `backend/app/graphs/plan_execute_graph.py`：兼容统一任务计划。
- `backend/app/graphs/supervisor_graph.py`：逐步转为兼容入口。
- `backend/app/graphs/agent_supervisor/state.py`：引用共享核心协议。
- `backend/app/graphs/agent_supervisor/graph.py`：负责统一调度和 Loop 节点。
- `backend/app/graphs/agent_supervisor/agents.py`：按单个 AgentSubtask 执行。
- `backend/app/graphs/agent_supervisor/evidence.py`：接入统一 Validator。
- `backend/app/graphs/streaming.py`：增加状态、Loop、校验阶段的 SSE 映射。
- `backend/app/domains/agent_run/service.py`：保存任务矩阵、Loop 和验证快照。
- `frontend/src/types.ts`：新增任务矩阵、Loop 和验证摘要类型。
- `frontend/src/components/ChatView.tsx`：展示目标企业、执行覆盖率和不足项。
- `frontend/src/components/AgentWorkflowPanel.tsx`：展示 Loop 轮次、退出原因和子任务状态。
- `frontend/src/__tests__/AgentWorkflowPanel.test.tsx`：任务矩阵、Loop 和部分完成状态测试。

## 8. 实施阶段与任务

### Phase 0：基线冻结和变更拆分

#### Task 1：完成当前基础变更的验证与提交拆分

- [x] 复核外部供应商联系方式补全边界，确认只读且不会自动导入主数据；
- [x] 复核初版 ConversationState 与澄清机制；
- [x] 执行后端相关测试、前端测试和构建；
- [x] 将联系方式补全与会话状态基础拆成语义清晰的 Conventional Commits；
- [x] 记录当前端到端基线用例及结果。

建议提交：

```text
feat(sourcing): enrich external supplier contacts
feat(agent): add structured conversation state
```

验收：工作区无混合职责修改；当前“寻源 → 这些企业风险评估”用例通过。

### Phase 1：核心协议与目标解析（P0）

#### Task 2：扩展 ConversationState 和核心协议

- [x] 先写 `test_agent_core_contracts.py` 失败测试；
- [x] 增加版本化 ConversationState、AgentTask、AgentSubtask、LoopState；
- [x] 增加旧会话兼容迁移；
- [x] 验证联系方式和外部来源字段迁移不丢失；
- [x] 运行聚焦测试并复核一次。

验收：新状态可 JSON 序列化、持久化、恢复；旧会话首次读取后得到结构化状态。

建议提交：`feat(agent): define unified execution contracts`

#### Task 3：实现确定性 Target Resolver

覆盖：

- 明确企业全称和简称；
- “这家、它、这些企业、上述供应商、它们”；
- “前两家、前三家、排名第一”；
- “A 和 B”；
- “除了 A、其余企业”；
- “低风险的这些企业”等基于已有结果的选择；
- 无上下文时正确进入澄清。

- [x] 先写 resolver 参数化测试；
- [x] 实现规则优先解析和置信度；
- [x] 低置信度不猜测，返回 clarification；
- [x] API 预检改为消费 resolver 结果；
- [x] 删除重复指代词表或保留单一兼容门面。

验收：目标解析 Eval 正确率达到 100% 的固定规则用例，未知指代不误选企业。

建议提交：`feat(agent): resolve conversational supplier targets`

### Phase 2：任务矩阵与全图状态接入（P0）

#### Task 4：实现 Task Matrix Planner

- [x] 将企业集合 × 分析维度生成唯一子任务；
- [x] 根据依赖决定串行和并行；
- [x] 生成 required/optional 和 evidence requirements；
- [x] 支持 partial、insufficient_evidence 和 failed；
- [x] 防止重复子任务和循环依赖。

验收：7 家企业 × 风险、ESG、舆情生成 21 个唯一子任务；任一失败不丢失其余结果。

建议提交：`feat(agent): plan supplier analysis task matrix`

#### Task 5：接入全部聊天执行图

执行顺序：

1. ReAct；
2. Sourcing；
3. Agent Supervisor；
4. Parallel；
5. Plan-Execute；
6. 旧 Supervisor 兼容入口。

- [x] 每个图只接收 `ConversationState + AgentTask`；
- [x] 每个图通过统一适配器返回 Worker Result；
- [x] 每轮完成后统一更新会话状态；
- [x] 不再由各图分别从自然语言回答提取供应商；
- [x] 保持现有 API 和 SSE 事件向后兼容。

验收：同一会话切换 mode 后，目标企业、当前任务和证据引用保持一致。

建议提交：`refactor(agent): unify graph conversation state`

### Phase 3：受控 Loop（P0/P1）

#### Task 6：实现通用 Loop Controller

- [x] 实现 iteration、tool budget、timeout 和 fingerprint；
- [x] 实现新增信息检测；
- [x] 实现 continue、complete、partial、needs_review、blocked 决策；
- [x] 所有退出记录 `stop_reason`；
- [x] 同一工具相同参数无新增信息时停止。

验收：单元测试证明不存在无限循环；超过预算时保留已有结果并明确停止原因。

建议提交：`feat(agent): add bounded execution loop controller`

#### Task 7：接入 Sourcing Loop

- [x] 本地候选充足度判断；
- [x] 天眼查和联网发现分阶段执行；
- [x] 官网、电话、邮箱补全；
- [x] 企业身份去重和冲突标记；
- [x] 根据默认策略 + 品类策略模板判断是否继续；
- [x] 外部候选保持 staged/unverified。

验收：本地充足时不产生不必要外部调用；本地不足时能扩展并在三轮内结束。

建议提交：`feat(sourcing): add bounded supplier discovery loop`

#### Task 8：接入 Evidence Completion 与 Validation Loop

- [x] 根据缺失维度选择数据源；
- [x] 证据新增量为零时停止；
- [x] Validator 生成覆盖率、冲突和不足项；
- [x] 最多允许一次校验补救；
- [x] 失败结果不进入确定性推荐。

验收：缺少风险或寻源证据时能精确指出缺口；不会再输出“待填充分析模板”作为完成结果。

建议提交：`feat(agent): validate and complete supplier evidence`

### Phase 4：Supervisor、路由与恢复收敛（P1）

#### Task 9：收敛路由和澄清顺序

统一顺序：

```text
加载 ConversationState
-> Target Resolver
-> Task Parser
-> 缺失信息判断
-> Task Matrix Planner
-> Supervisor
```

- [x] 规则预检只作为解析结果后的兜底；
- [x] Router 不重复解析企业目标；
- [x] LLM 仅处理规则无法判断的任务语义；
- [x] 澄清问题附带明确 missing fields；
- [x] 已有上下文时不得重复询问公司名称。

验收：真实多轮场景不再因模式不同而出现不同澄清结果。

建议提交：`refactor(agent): converge routing and clarification`

#### Task 10：持久化任务、Loop 和恢复快照

- [x] `agent_run` 保存 Task Matrix 和每个子任务结果；
- [x] 保存 Loop iteration、预算和退出原因；
- [x] SSE 断线后从已持久化阶段恢复；
- [x] 恢复时不重复已成功工具调用；
- [x] 审批后仅恢复被暂停的动作分支。

验收：中途重启后能恢复任务；已完成子任务不会再次执行。

建议提交：`feat(agent): persist resumable execution state`

### Phase 5：Trace、前端工作流和评测（P1）

#### Task 11：统一 Agent Trace

记录：

- 状态解析结果；
- 路由和计划；
- 子任务开始、完成和失败；
- 工具调用摘要与耗时；
- Loop iteration 和 stop reason；
- Validator 结果；
- 证据引用；
- 审批暂停与恢复；
- 最终完成度。

不得记录或展示模型私有思维链。前端只展示用户可理解的任务计划、工具摘要、证据和状态变化。

验收：可从 run_id 定位“为何澄清、为何降级、为何停止、哪些企业未完成”。

建议提交：`feat(agent): expose auditable execution trace`

#### Task 12：升级前端 Agent 工作流

- [x] 展示当前目标企业和分析维度；
- [x] 展示任务矩阵完成进度；
- [x] 展示 Loop 类型、轮次和退出原因；
- [x] 展示证据不足、冲突和失败项；
- [x] 保留现有联系人卡片和审批卡片；
- [x] 不展示模型内部思考文本。

验收：用户能区分“正在执行、证据不足、失败、等待审批、已完成”。

建议提交：`feat(agent-ui): show task and evidence progress`

#### Task 13：建立多轮端到端 Eval

- [x] 固定 12 个离线多轮 Eval，并新增 CI 自动执行的 `agent_e2e`：本地候选 → 审批暂停 → API 批准恢复 → 成功回执，以及寻源模式会话快照复用。

固定用例至少包含：

1. 寻源后询问“这些企业风险如何”；
2. “前两家做 ESG 和舆情分析”；
3. “除了 A，其余企业做风险评估”；
4. “只看低风险的企业”；
5. 本地无结果后进入外部发现；
6. 天眼查失败后联网降级；
7. 官网联系方式补全失败；
8. 外部候选不得自动入库；
9. 某个企业工具失败后部分完成；
10. 长对话压缩后保持目标企业；
11. 服务重启后恢复任务；
12. 所有写操作必须人工确认。

质量门槛：

- 目标企业解析正确率：100%；
- 任务矩阵覆盖率：100%；
- 有依据结论证据覆盖率：100%；
- 未审批业务写操作：0；
- 相同工具参数无效重复率：0；
- 端到端场景通过率：初始不低于 90%，合并主分支前达到 100%。

建议提交：`test(agent): add multi-turn execution evals`

### Phase 6：兼容清理与文档（P2）

#### Task 14：清理重复状态和旧解析路径

独立执行，避免与 Task 15 扩张为一个长期任务。

- [x] 通过调用关系确认旧路径无流量：`chat.py` 只构建一次请求级 `execution_context`，全部流函数显式接收该快照；
- [x] 删除各图重复供应商提取逻辑，统一由 `agent_core.adapter.collect_supplier_references()` 合并；澄清仅在 API 的 Target Resolver 之后执行；
- [x] 保留 `AGENT_RUN_V2_*` rollout flags、旧 mode alias 和同步回退入口作为兼容门面；统一会话状态不另设可关闭开关，以免回退到会话不一致路径；
- [x] 未删除仍由同步回退端点使用的 `agent.py` 持久化与工具注册代码；
- [x] 执行核心图、上下文、审批与 `agent_e2e` 回归。

建议提交：`refactor(agent): remove legacy context duplication`

#### Task 15：更新架构与开发文档

独立执行。

- [x] 更新 `AGENTS.md` 的 Agent 状态、新功能边界和 E2E 约定；
- [x] 更新 README 的开发验证命令；
- [x] 更新活动架构图，标明请求级快照、共享引用收集和审批恢复；
- [x] 记录 Loop 默认预算和品类策略覆盖方式；
- [x] 增加故障定位和回放说明。

建议提交：`docs(agent): document converged execution architecture`

## 9. 测试策略

### 9.1 单元测试

- 状态协议、版本迁移；
- Target Resolver；
- Task Matrix Planner；
- Loop 预算和 fingerprint；
- Evidence Validator；
- Worker Result 契约；
- 路由与澄清边界。

### 9.2 图级测试

- 子任务依赖和并行；
- 单个 Worker 失败隔离；
- Loop 继续与退出；
- Human Approval interrupt/resume；
- checkpoint 恢复和幂等。

### 9.3 集成测试

- MongoDB 会话状态和原始证据；
- PostgreSQL agent run、checkpoint、审批和审计；
- Redis 缓存不影响业务真相；
- SSE 重连和事件重放；
- 前后端任务状态一致。

PostgreSQL 或 MongoDB 任一不可用时立即停止集成测试并标记环境阻塞，不使用假成功代替。

### 9.4 前端测试

- 多企业引用展示；
- 任务矩阵进度；
- Loop 状态与退出原因；
- 部分完成和证据不足；
- 审批确认与拒绝；
- SSE 重连后状态恢复。

## 10. 迁移与回退

### 10.1 迁移策略

- ConversationState 使用 `schema_version`；
- 旧会话首次读取时兼容提取供应商，并保存为新结构；
- 新状态写入失败不得阻断只读回答，但必须记录 Trace 和降级原因；
- 先让 ReAct/Sourcing 双读新旧状态，再逐图切换；
- 全图切换并通过 Eval 后，停止写旧状态字段；
- 清理旧逻辑必须单独任务执行。

### 10.2 Feature Flags

当前生效的灰度开关为 `AGENT_RUN_V2_ENABLED`、`AGENT_RUN_V2_ROLLOUT`、`AGENT_RUN_V2_CANARY_PERCENT` 和 `AGENT_RUN_V2_ROLLOUT_STATE`。统一会话状态、任务矩阵、受控 Loop 与校验器为所有聊天图的安全基线，不提供关闭后退回重复解析路径的开关。

历史建议的开关名称保留在此处，仅用于设计追溯：

```text
AGENT_UNIFIED_STATE_ENABLED
AGENT_TASK_MATRIX_ENABLED
AGENT_BOUNDED_LOOP_ENABLED
AGENT_VALIDATOR_ENABLED
```

开发阶段默认启用；出现回归时可按能力回退，不回滚数据库业务数据。

### 10.3 回退条件

- 目标解析正确率下降；
- 任务重复执行；
- 未审批写操作；
- Loop 超出预算；
- 状态迁移导致供应商引用丢失；
- SSE 兼容性破坏。

任何未审批业务写操作都属于 P0 阻断，必须立即关闭新执行路径。

## 11. 执行纪律

沿用已确认的 Subagent-Driven 方式：

- 每个任务独立实现与复核；
- 每个阶段最多 1 次实现 + 1 次复核；
- 同一问题最多修复 2 轮，仍失败标记阻塞；
- PostgreSQL/MongoDB 不可用时立即停止集成测试；
- 子任务只在完成、失败或阻塞时汇报；
- 每 30～60 分钟汇总一次，不逐次播报等待；
- Task 14、Task 15 必须保持独立；
- 每个任务完成后运行聚焦测试，阶段结束运行相关回归；
- 相关修改形成一个 Conventional Commit，不混入无关工作区变更；
- 未经用户明确要求不执行合并、推送或业务数据删除。

## 12. 完成定义

本计划完成必须同时满足：

- 所有聊天执行图使用统一 ConversationState；
- 多企业、多维度请求由任务矩阵确定性执行；
- 寻源、证据补全、校验和降级 Loop 均有预算与退出原因；
- 所有关键结论具有证据引用或明确标记不足；
- 所有业务写操作仍由 Human Approval Gate 拦截；
- 任务可持久化、恢复且不重复已完成操作；
- 前端能展示任务覆盖率、Loop 和证据状态；
- 固定端到端 Eval 全部通过；
- 旧状态和重复解析逻辑完成安全清理；
- 架构、开发和故障定位文档同步更新。
