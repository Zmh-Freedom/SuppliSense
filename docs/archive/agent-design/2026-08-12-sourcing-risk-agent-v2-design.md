# 智能寻源与风险 Agent V2 重构设计

> **归档说明（2026-08-18）：** 本文档已由 `docs/superpowers/specs/2026-08-18-agent-unified-architecture.md` 取代；仅保留用于追溯 V2 领域能力的原始决策。

**日期：** 2026-08-12

**状态：** 已确认，待实施计划

**范围：** 新建独立的智能寻源与风险 Agent V2；旧 Agent 保留兼容，达到上线门槛后再逐步接管智能寻源默认入口

## 1. 背景与结论

SuppliSense 已具备 ReAct、Plan-Execute、Supervisor、Parallel、Reflection 和 Sourcing 等 LangGraph 编排，以及风险、财务、舆情、合规、知识库和寻源工具。但当前能力更接近“多模式工具调用助手”，还没有形成可靠的智能寻源与风险决策闭环。主要问题是：

- Agent 和多数工具仍以 `company_name` 作为业务关联参数，未完整接入 P1 企业身份与 `company_id`。
- 需求理解、外部扩源、风险调查、排序和业务动作分散在多个图和 Prompt 中，规则容易漂移。
- 工具结果缺少统一证据模型，结论来源、时间、置信度和冲突难以系统追溯。
- 评分和淘汰规则没有统一的版本化策略模型，历史推荐难以复现。
- Human-in-the-Loop 暂停状态保存在进程内字典中，不支持重启恢复、多 Worker 和长期审计。
- Agent 图、路由、审批恢复和端到端业务行为的自动化测试覆盖不足。

本次采用独立 V2 路径：建设一个面向单品类采购任务的“智能寻源与风险 Agent”，旧 Agent 继续承担普通问答和兼容流量。V2 成熟后，再逐步成为智能寻源默认入口。

## 2. 已确认的产品决策

1. Agent 第一阶段聚焦智能寻源与风险，不承担多品类或复杂 BOM 任务。
2. 一个 Agent Run 只对应一个采购品类和一组规格约束，可以返回多家候选供应商。
3. 候选来源采用两阶段策略：本地供应商库优先；候选不足时查询天眼查等已接入外部数据源。
4. 外部候选先作为待确认候选展示，用户批准后才可导入供应商主库。
5. 排序采用“硬性门槛 + 可解释加权评分”。命中关键门槛的候选被淘汰或转人工复核，其余候选再排序。
6. 缺失数据必须降低数据完整性和结论置信度，不得解释为低风险。
7. 策略采用“默认策略 + 品类策略模板”，每次运行冻结不可变策略快照。
8. Agent 可以提出和执行领域动作，但所有改变业务状态的动作都必须人工审批。
9. 系统运行记录、审计、证据索引和检查点属于基础运行持久化，可以自动写入；它们不等同于业务状态变更。

## 3. 目标与非目标

### 3.1 目标

- 将自然语言采购需求转成可校验、可追踪的单品类寻源任务。
- 优先使用本地供应商库，并在候选不足时受控扩展外部候选。
- 使用 P1 企业身份能力把正式候选绑定到稳定 `company_id`。
- 并行收集财务、司法、舆情、制裁、ESG 和供应连续性证据。
- 以确定性规则执行门槛、缺失惩罚、加权评分和排序。
- 输出推荐、备选和淘汰结果，并让每个关键结论可追溯到证据。
- 通过持久化检查点、审批记录、乐观锁和幂等命令支持安全暂停与恢复。
- 建立可量化的 Agent Eval、安全测试和渐进上线门槛。

### 3.2 非目标

- 第一阶段不处理跨品类任务、复杂 BOM 拆解和组合寻源优化。
- 不一次性替换现有六种 Agent 模式，也不删除旧 `/api/v1/chat/stream` 能力。
- 不让 LLM 直接计算最终分数、判断制裁命中、选择法定主体或绕过审批执行写操作。
- 不在 service 层引入 LangGraph 依赖。
- 不在第一阶段建设通用 BPM 审批引擎；只实现 Agent 动作需要的统一审批协议。
- 不引入 Kafka 或 RabbitMQ；可靠领域动作复用现有 PostgreSQL Transactional Outbox。

## 4. 总体架构

V2 使用一条领域化主图 `SourcingRiskGraph`：

```text
需求理解
  → 策略选择与快照冻结
  → 本地供应商检索
  → 候选充足度判断
  → 外部只读扩源（按需）
  → 外部候选导入审批（按需）
  → 企业身份解析
  → 并行风险调查
  → 证据校验
  → 硬性门槛
  → 缺失惩罚与加权评分
  → 推荐/备选/淘汰解释
  → 业务动作审批（按需）
  → Outbox 可靠执行
```

架构遵循以下依赖方向：

```text
API → graphs → services → repositories / providers
                 ↓
        PostgreSQL / MongoDB / Redis / Outbox
```

- `graphs/` 只负责节点编排、并行、路由、暂停和恢复。
- `services/` 负责框架无关的需求、策略、寻源、证据、评分、审批和动作逻辑。
- `repositories/` 负责数据库访问，不包含业务判断。
- LangGraph tools 只是 service 的薄包装，不复制业务规则。
- LLM 用于语言理解、信息归纳和用户可读解释；身份、评分、权限和写入由确定性代码控制。

## 5. 组件职责

### 5.1 Agent API

负责鉴权、创建 Agent Run、查询状态、SSE 订阅、提交澄清和审批决定。API 不实现寻源规则或评分逻辑。

V2 使用独立资源 API，避免把长任务强绑定到一次 HTTP 连接：

- `POST /api/v1/agent-runs/sourcing-risk`：创建任务，返回 `run_id`、`status` 和 `version`。
- `GET /api/v1/agent-runs/{run_id}`：返回当前阶段、候选摘要、决策和待处理事项。
- `GET /api/v1/agent-runs/{run_id}/events`：SSE 订阅运行事件；断线后使用事件游标续传。
- `POST /api/v1/agent-runs/{run_id}/clarifications`：提交结构化补充信息和 `expected_version`。
- `POST /api/v1/agent-runs/{run_id}/approvals/{approval_id}/decisions`：批准或拒绝动作，必须提交 `expected_version` 和意见。
- `POST /api/v1/agent-runs/{run_id}/cancel`：取消尚未完成的任务。

现有 `/api/v1/chat/stream` 保持响应兼容。迁移期可以显式选择 V2，或由智能寻源入口直接创建 V2 Run。

### 5.2 SourcingRiskGraph

V2 唯一领域主图，负责：

- 根据持久化业务状态选择下一节点；
- 对独立候选和风险维度执行受限并行；
- 在澄清、身份复核和业务动作审批点暂停；
- 从 PostgreSQL Checkpointer 恢复；
- 产出结构化领域事件，不直接写数据库。

图状态只保存 ID、小型结构化摘要和控制字段，不保存外部原始响应或大段证据正文。

### 5.3 Requirement Service

将自然语言输入转换为 `SourcingRequirement`，并执行确定性校验：

- 必填：`category`、`specification`。
- 可选：地区、数量、预算、资质、交付约束、风险上限和期望候选数量。
- 第一阶段检测到多个品类时进入澄清，不自动拆成多个任务。
- LLM 输出必须通过 Pydantic 校验；修复一次仍失败时进入确定性澄清。

### 5.4 Policy Service

根据品类选择策略模板，找不到时使用默认模板。每次 Run 创建 `PolicySnapshot`，保存：

- 模板 ID、模板版本和生效时间；
- 硬性门槛与门槛动作；
- 各维度权重；
- 缺失数据惩罚；
- 数据新鲜度要求；
- 最低证据要求和排序版本。

运行开始后不受模板后续修改影响，保证历史推荐可复现。

### 5.5 Discovery Service

负责两阶段候选发现：

1. 按品类、规格、地区、资质和供应商状态检索本地供应商库。
2. 使用策略中的 `minimum_candidate_count` 和约束覆盖率判断是否充足。
3. 不足时查询外部 Provider，但不直接导入供应商主库。
4. 外部结果保存为当前 Run 的 `staged_candidate`，展示来源和有限摘要。
5. 用户批准选中候选后，通过 Outbox 导入主库，再进入企业身份和风险调查。

### 5.6 Company Identity Service

复用 P1 企业身份服务，统一返回：

- `exact`：唯一可靠命中，绑定 `company_id`；
- `candidates`：多个候选或置信度不足，进入 `IDENTITY_REVIEW`；
- `pending_verification`：没有可靠主体，不得进入正式评分和推荐。

LLM 可以解释候选差异，但不能决定法定主体。正式候选、证据、评分和动作全部使用 `company_id`；名称仅作为快照展示。

### 5.7 Evidence Service

统一收集、标准化和校验证据。结构化证据至少包含：

```python
class EvidenceRecord(BaseModel):
    evidence_id: UUID
    run_id: UUID
    company_id: UUID
    dimension: Literal[
        "company", "financial", "judicial", "sentiment",
        "sanctions", "esg", "continuity"
    ]
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
```

PostgreSQL 保存结构化索引和审计字段；MongoDB 保存外部原始响应、舆情正文和长文本。Agent 图只持有 `evidence_id`。

Evidence Service 必须区分：无风险、无数据、数据过期和来源冲突。只有第一种可以被当作低风险信号。

### 5.8 Decision Engine

Decision Engine 是纯确定性领域服务，输入需求、策略快照、候选档案和证据索引，输出可复现的 `CandidateDecision`。

执行顺序：

1. 校验企业身份和最低证据要求；
2. 执行硬性门槛，产生 `eligible`、`rejected` 或 `needs_review`；
3. 对缺失或过期维度执行策略定义的惩罚；
4. 对合格候选执行加权评分；
5. 使用稳定排序键解决同分；
6. 生成结构化原因码和证据引用；
7. LLM 只能把结构化原因转成自然语言，不得改变分数和结果分组。

建议分数采用统一的“越高越适合采购”语义：

```text
final_score = Σ(weight_i × normalized_dimension_score_i)
              - missing_data_penalty
              - stale_data_penalty
```

维度和权重由策略快照定义，权重总和必须为 1。硬性门槛先于评分，命中制裁等门槛的候选不会因其他维度高分重新进入推荐。

### 5.9 Approval Service 与 Action Service

所有业务写操作先生成 `ActionProposal`，内容包括动作类型、目标、参数、影响范围、证据摘要、`run_version` 和幂等键。审批决定保存审批人、角色、结果、意见、时间和预期版本。

首批必须审批的动作：

- 从外部候选导入供应商主库；
- 加入或移出监控清单；
- 提交供应商准入申请；
- 创建、修改或删除定时任务；
- 修改或合并企业主数据；
- 导出并分发正式报告。

批准后，Action Service 在 PostgreSQL 事务中写入领域命令和 Outbox 事件。Outbox Worker 使用幂等键执行，记录成功、重试和死信。审批接口不能直接绕过 Action Service 修改业务状态。

## 6. Agent Run 与持久化模型

### 6.1 业务状态与图检查点分离

- `agent_runs` 是业务真相，保存可展示、可审计的阶段和结果引用。
- LangGraph PostgreSQL Checkpointer 保存图执行位置和恢复上下文。
- Redis 只用于运行锁、短期事件分发、幂等缓存和并行协调，不作为任务真相源。
- 原进程内 `interrupt_store` 不用于 V2，旧 Agent 在迁移完成前继续保留现状。

### 6.2 核心表

#### `agent_runs`

| 字段 | 说明 |
|---|---|
| `id UUID PK` | `run_id` |
| `run_type VARCHAR(32)` | 固定为 `sourcing_risk_v2` |
| `user_id UUID` | 创建人 |
| `status VARCHAR(32)` | 当前业务状态 |
| `version INTEGER` | 乐观锁版本 |
| `requirement JSONB` | 已校验需求快照 |
| `policy_snapshot_id UUID` | 使用的策略快照 |
| `decision_id UUID NULL` | 最终决策引用 |
| `error_code VARCHAR(64) NULL` | 安全错误码 |
| `created_at / updated_at / completed_at` | 生命周期时间 |

#### `sourcing_policy_templates` 与 `sourcing_policy_snapshots`

模板可由管理员维护；快照不可修改。模板字段包括品类、版本、状态、门槛、权重、缺失惩罚、新鲜度和候选数量配置。

#### `agent_run_candidates`

保存本地或外部候选、`supplier_id`、`company_id`、来源、导入状态、身份状态和排序结果。外部候选未批准前 `supplier_id` 和 `company_id` 可以为空。

#### `agent_evidence`

保存 EvidenceRecord 的结构化字段及 Mongo 原始内容引用。一个证据可支持多个结构化原因，但每个引用必须保持来源和时间。

#### `candidate_decisions`

保存分组、最终分数、维度分、原因码、证据 ID、策略快照和评分引擎版本。

#### `agent_action_proposals` 与 `agent_approval_decisions`

保存待审批动作、幂等键、Run 版本、审批结果和执行状态。幂等键建议由 `run_id + action_type + target_ref` 形成稳定摘要，并建立唯一约束。

### 6.3 状态机

```text
CREATED
  → CLARIFYING
  → POLICY_LOCKED
  → LOCAL_SEARCHING
  → EXTERNAL_REVIEW        （按需）
  → IDENTITY_RESOLVING
  → IDENTITY_REVIEW        （按需）
  → INVESTIGATING
  → EVIDENCE_REVIEW        （按需）
  → SCORING
  → READY_FOR_REVIEW
  → ACTION_PENDING         （按需）
  → ACTION_EXECUTING       （按需）
  → COMPLETED
```

允许的终态：

- `COMPLETED`：结果完整，且用户无需动作或审批动作成功。
- `PARTIAL`：非关键来源失败，结果带明确缺口但仍可使用。
- `NEEDS_REVIEW`：身份、制裁或关键证据冲突需要人工处理。
- `ACTION_FAILED`：分析已完成，但审批后的领域动作最终失败。
- `FAILED`：核心流程失败，无法形成安全结果。
- `CANCELLED`：用户主动取消。

所有恢复和审批请求必须携带 `expected_version`。版本不一致返回业务冲突，防止过期审批和并发重复执行。

## 7. SSE 事件契约

V2 不暴露内部思维链，只暴露任务阶段、可验证依据和动作状态。事件类型：

- `session`：`run_id`、当前版本；
- `stage`：阶段编码、用户可读说明和进度；
- `clarification_required`：缺失字段和结构化问题；
- `candidate_batch`：候选摘要及来源；
- `identity_review_required`：企业候选主体；
- `evidence`：证据摘要、来源、时间和置信度；
- `candidate_scored`：分组、维度分、原因码和证据引用；
- `approval_required`：动作提案、影响范围、审批 ID 和版本；
- `action_status`：排队、执行、重试、成功或死信；
- `done`：任务终态和决策引用；
- `error`：安全错误码、阶段和用户可行动建议。

每个事件包含单调递增的 `event_id`。客户端断线重连时提交最后事件 ID，服务端从持久化事件或短期 Redis 流中续传；事件不可只依赖当前 HTTP 进程内存。

## 8. 异常与降级策略

| 场景 | 策略 | 结果 |
|---|---|---|
| 需求字段缺失或冲突 | 进入 `CLARIFYING`，补充后继续 | 可恢复 |
| LLM 结构化输出无效 | 根据校验错误修复一次；仍失败则结构化澄清 | 不执行动作 |
| 外部数据源失败 | 单次 20 秒超时，指数退避最多重试 3 次；保留本地候选 | `PARTIAL` |
| 企业身份无法唯一确认 | 进入 `IDENTITY_REVIEW` | 未确认前停止调查和评分 |
| 制裁或黑名单数据不可用 | 失败关闭，候选进入 `NEEDS_REVIEW` | 不得进入推荐组 |
| 财务、舆情或 ESG 单维度缺失 | 记录缺失原因并执行缺失惩罚 | 带警告排名 |
| 证据冲突影响硬门槛 | 保存双方证据并进入 `EVIDENCE_REVIEW` | 不自动选边 |
| 策略或评分引擎异常 | 终止评分 | `FAILED` |
| 审批后领域动作失败 | Outbox 最多尝试 5 次，之后进入死信 | `ACTION_FAILED` |

统一原则：

- 分析完成与动作完成分别记录，不把分析成功误报为业务执行成功。
- LLM 不得为缺失数据、冲突证据或评分故障编造替代结果。
- API 只返回错误码、阶段和可行动建议；密钥、SQL 和原始异常只进入受控日志，且必须脱敏。

## 9. 权限、安全与审计

- 所有 API 均绑定当前用户，用户只能读取自己有权限的 Run。
- 创建业务动作提案和批准动作使用独立权限检查；审批人角色在决策时快照保存。
- 同一用户是否允许审批自己创建的动作由动作类型策略控制；高风险动作默认禁止自审。
- 外部内容视为不可信数据，不得作为系统指令拼接；在 Prompt 中使用明确的数据边界和长度限制。
- 工具采用 allowlist；V2 图只能使用与当前节点匹配的只读能力或 Action Gateway。
- 原始工具异常、Provider 响应和 Prompt 输入必须经过密钥及个人信息脱敏。
- 每次身份绑定、策略选择、门槛命中、分数生成、审批和动作执行均产生审计记录。

## 10. 前端任务工作台

V2 应作为“智能寻源”任务工作台，而不是仅显示聊天气泡。第一阶段保留自然语言输入，同时展示：

- 结构化采购需求和待补充字段；
- 当前阶段、已完成步骤和可恢复状态；
- 本地候选与外部待确认候选；
- 企业身份确认卡；
- 推荐、备选、淘汰分组及维度得分；
- 证据来源、数据时间、置信度、缺失和冲突标识；
- 待审批动作、影响范围和批准/拒绝入口；
- 动作执行、重试和失败状态。

前端继续使用 React、TanStack Query、原生 fetch 和 Tailwind。服务端任务状态通过 Query 获取，实时增量通过 SSE 获取；页面刷新后可以按 `run_id` 恢复。

## 11. 可观测性

每个 Run 统一关联 `run_id`、`user_id`、`graph_version`、`policy_snapshot_id` 和 `scoring_version`。新增低基数指标：

- Run 数量、终态和各阶段耗时；
- 需求澄清率、外部扩源率和身份复核率；
- 各 Provider 成功率、超时率和数据新鲜度；
- 候选数量、推荐/复核/淘汰比例；
- 审批通过率、动作成功率、重试和死信数量；
- 首事件延迟、任务完成延迟、LLM 调用次数和估算成本；
- Eval 版本、通过率和回归失败数。

任意企业名称、查询文本、`run_id` 或用户 ID 不得作为 Prometheus label；它们进入结构化日志或追踪属性。

## 12. 测试体系与上线门槛

### 12.1 四层测试

1. 领域单元与属性测试：门槛、权重、缺失惩罚、稳定排序、幂等、版本并发和 Pydantic 校验。
2. 图与契约集成测试：节点路由、并行汇聚、暂停恢复、SSE 事件、PostgreSQL、MongoDB、Redis 和 Outbox。
3. 离线 Agent Eval：固定案例集验证需求解析、工具选择、证据忠实度、推荐分组和自然语言解释。
4. 人工验收与红队：采购可用性、提示注入、越权写入、审批重放、外部恶意内容和证据误导。

所有 LLM 测试分两类：CI 使用固定响应或可重放夹具验证确定性流程；受控环境定期运行真实模型 Eval，避免把网络波动变成普通单元测试的不稳定因素。

### 12.2 上线量化门槛

正确性：

- 后端、前端和 Compose 全量验证通过；
- 评分引擎黄金案例通过率 100%；
- 相同证据和策略产生完全一致的分数及排序；
- 企业身份唯一命中精确率不低于 99%；
- 身份歧义静默绑定次数为 0。

Agent 质量：

- 需求字段提取准确率不低于 95%；
- 工具选择成功率不低于 95%；
- 关键结论有证据支持率不低于 98%；
- 关键证据缺失情况下的误推荐率为 0%；
- Top-N 推荐经采购人员盲评通过率不低于 85%。

安全与运行：

- 未审批业务写入次数为 0；
- 重复审批或事件重放造成的重复业务动作次数为 0；
- 断点恢复成功率不低于 99%；
- P95 首事件延迟不高于 2 秒；
- P95 仅使用本地候选的任务完成时间不高于 90 秒。

### 12.3 渐进上线

1. `Shadow`：V2 与旧路径并行运行，但不对用户展示或执行领域动作，离线比较结果。
2. `Internal`：仅管理员和内部采购用户使用，所有业务动作仍需审批。
3. `Canary`：按用户白名单或 10% 智能寻源流量开放，监测人工接管率、回退率和投诉。
4. `Default`：所有门槛达标后成为智能寻源默认入口，旧 Agent 保留配置化回退开关。

以下任一情况阻断上线：企业主体错绑、未审批业务写入、制裁筛查失败仍进入推荐、评分不可复现、任务恢复后重复执行、关键结论与证据不一致。

## 13. 迁移与交付阶段

### 阶段 A：V2 运行骨架

- 新建 Agent Run、策略模板和快照、候选、证据、决策、动作提案与审批模型。
- 接入 PostgreSQL Checkpointer 和持久化 SSE 事件。
- 建立独立 V2 API、状态机、权限、乐观锁和基础指标。
- 暂不接入实际推荐，先验证创建、暂停、恢复、取消和断线重连。

### 阶段 B：企业身份与本地寻源

- 需求结构化与澄清。
- 默认策略和品类策略解析。
- 本地供应商检索与候选充足度判断。
- 全链路接入 `company_id`，身份歧义暂停确认。

### 阶段 C：证据、门槛与排序

- 并行风险调查和 Evidence 标准化。
- 制裁失败关闭、证据冲突和缺失惩罚。
- 确定性 Decision Engine 和自然语言解释。
- 前端展示推荐、备选、淘汰和证据。

### 阶段 D：外部扩源与业务动作

- 外部只读候选与 staged candidate。
- 导入审批、Action Gateway 和 Outbox 执行。
- 加入监控、提交准入和正式报告等审批动作。

### 阶段 E：Eval 与渐进切换

- 建立黄金案例、离线 Eval、红队和采购盲评流程。
- 依次经过 Shadow、Internal 和 Canary。
- 达到门槛后将智能寻源默认入口切换到 V2，保留旧路径回退。

每个阶段必须产生可独立验证的软件增量，不以“所有功能一次完成”为合并条件。

## 14. 兼容与清理原则

- 旧 Agent 图和现有 SSE 事件在 V2 验证期不删除。
- V2 复用现有风险和寻源 service，但通过新的领域接口适配 `company_id` 和 Evidence；不在 Prompt 中补偿服务缺陷。
- 不继续向旧 `interrupt_store` 增加能力；V2 直接使用持久化检查点和审批记录。
- V2 成为默认入口且稳定运行一个完整观察周期后，再单独设计旧 Sourcing 图、重复 Prompt 和重复工具集合的清理计划。
- 任何兼容适配都必须有移除条件和监控指标，避免形成永久双实现。

## 15. 验收场景

至少覆盖以下端到端场景：

1. 用户输入完整单品类需求，本地候选充足，Agent 直接完成身份解析、调查和排序。
2. 用户缺少规格或地区，Agent 暂停澄清，刷新页面后补充并继续。
3. 本地候选不足，Agent 展示外部候选；未批准前供应商主库无新增记录。
4. 用户批准部分外部候选，Outbox 幂等导入后继续调查。
5. 企业名称命中多个法定主体，未确认前不生成正式评分。
6. 制裁筛查不可用，候选进入复核而不是推荐。
7. 财务数据缺失，候选仍可排序但降低分数和置信度，并明确缺失原因。
8. 多来源关键证据冲突，任务暂停并展示冲突双方。
9. 用户批准加入监控，重复提交审批不会产生重复监控记录。
10. 服务在审批前后重启，任务可以恢复且不会重复执行动作。
11. 用户无权限读取或审批他人的 Run，API 返回统一安全错误。
12. V2 失败时可以通过配置回退旧智能寻源路径，不影响普通 Agent 问答。
