# Agent Harness 生产链路加固与修复计划

日期：2026-09-03
状态：当前唯一有效的 Agent 修复与验收计划
适用分支：refactor/agent-harness-runtime 及后续分支
历史基线：2026-09-01-agent-harness-runtime-plan.md

## 1. 目标与边界

本计划不继续横向增加 Agent 功能，而是把已经建立的 Harness 契约骨架接成真实生产活动链路：

- PostgreSQL 是 Session、Turn、Run、Task、Entity、ToolCall、Proposal 和恢复状态的唯一事实源。
- 所有生产工具经过统一 ToolExecutor，并具备严格输入/输出、预算、超时、重试和证据门禁。
- 确定性结论不仅引用 Evidence，还能验证声明字段和值确实来自对应 Evidence。
- 寻源真正执行需求解析、本地/飞书匹配、天眼查和联网扩源、核验、排序与来源展示。
- 风险覆盖财务、商务、质量、交付、合规；ESG 和舆情作为扩展能力。缺数据只能输出覆盖度和局限。
- 风险监控写操作必须经过 Proposal、人工确认、签名令牌、幂等执行和真实回执。
- SSE、Trace、前端状态与最终 AgentAnswer 来自同一 Run，前端不自行推断成功。
- E2E 使用生产 Runtime、生产 Tool Registry 和真实测试数据库，不再依靠“完美模拟工具”自证。

当前仍不执行供应商准入、主数据编辑或飞书回写；这些属于外部供应商管理系统。

## 2. 审计结论

当前 Harness 核心模型已存在，但大部分仍是“契约已建、生产接线不完整”，综合完成度约 45%。在可信执行基础补齐前，不继续增加新的 Agent 能力。

### 2.1 全量缺陷覆盖矩阵

| ID | 缺陷 | 优先级 | 问题记录 | 修复任务 |
|---|---|---|---|---|
| D01 | Chat 仍从 MongoDB 加载执行上下文 | P0 | ISS-20260903-001 | Task 11 |
| D02 | Harness 未完整持久化 Session/Turn/Run/Task/ToolCall | P0 | ISS-20260903-001 | Task 11 |
| D03 | 28 个生产工具共用宽松 ToolPayload | P0 | ISS-20260903-002 | Task 12 |
| D04 | evidence_required 不执行，空证据可成功 | P0 | ISS-20260903-002 | Task 12 |
| D05 | Claim 未与 Evidence facts 做字段值校验 | P0 | ISS-20260903-003 | Task 13 |
| D06 | Evidence 覆盖度只按维度，未按企业和维度 | P0 | ISS-20260903-003 | Task 13 |
| D07 | 寻源未提取品类、规格、地域等需求 | P0 | ISS-20260903-004 | Task 14 |
| D08 | 活动 Harness 只列出前 20 家正式供应商 | P0 | ISS-20260903-004 | Task 14 |
| D09 | 风险意图未完整覆盖财务、商务、质量、交付 | P0 | ISS-20260903-005 | Task 15 |
| D10 | Planner 只是固定矩阵展开，不支持趋势、比较和依赖 | P1 | ISS-20260903-005 | Task 15/17 |
| D11 | 生产链路不生成可执行 remediation spec | P1 | ISS-20260903-005 | Task 15/17 |
| D12 | LLM、并行任务和总时长预算未完整执行 | P1 | ISS-20260903-005 | Task 17 |
| D13 | 只读 Harness 与写 Supervisor 是两个执行内核 | P0 | ISS-20260903-006 | Task 16 |
| D14 | Supervisor 可绕过 ToolExecutor 直接调用 service | P0 | ISS-20260903-006 | Task 16 |
| D15 | ToolExecutor 接受任意非空审批字符串 | P0 | ISS-20260903-006 | Task 16 |
| D16 | 恢复未完整绑定用户、提案、版本和动作 | P0 | ISS-20260903-007/009 | Task 16 |
| D17 | 恢复前删除暂停记录，失败后不可重试 | P0 | ISS-20260903-007 | Task 16 |
| D18 | 同步 Mongo/OpenAI 调用阻塞异步 Chat API | P1 | ISS-20260903-008 | Task 17 |
| D19 | SSE 批量伪流式、Trace 不完整、前端乐观显示成功 | P1 | ISS-20260903-008/009 | Task 18 |
| D20 | E2E 使用完美替身、自报指标和低覆盖率门槛 | P0 | ISS-20260903-010 | Task 19/20 |

## 3. 修复后的唯一活动链路

    Frontend AI Workbench
      -> Chat API / authenticate user and session ownership
      -> PostgreSQL SessionStateStore
           -> create versioned Turn and Run
           -> resolve EntityMemory and FocusSet
           -> build CapabilityPlan
      -> Unified LangGraph Harness
           -> ToolExecutor
                -> strict ToolRegistry contracts
                -> permission, budget, timeout and retry
                -> ActionGate for write tools
                -> service -> repository/provider
                -> ToolOutcome, Evidence and Receipt
           -> EvidenceLedger
           -> ClaimValidator (entity x dimension x fact)
           -> bounded remediation loop
           -> AgentAnswer
      -> persist Run/Task/ToolCall/Event/Answer in PostgreSQL
      -> project display messages/evidence to MongoDB
      -> stream persisted events through SSE

约束：

- LangGraph checkpoint 只保存图位置，不替代 PostgreSQL 业务状态。
- MongoDB 不参与路由、上下文解析、审批恢复或终态判断。
- Redis 只承担租约、缓存和限流。
- LLM 负责意图/实体候选、受约束计划和语言表达；确定性代码负责成功判定、证据、审批和完成状态。

## 4. 分阶段实施

### 阶段 0：基线冻结与可重放样本

- [x] 登记 ISS-20260903-001 至 ISS-20260903-010。
- [x] 建立 D01 至 D20 全量缺陷映射。
- [x] 将本次审计探针固化为失败回归，不在生产修复前改成假通过。
- [x] 记录当前 Task 11 定向 27 项、非集成回归 560 项和三数据库控制面集成 4 项基线。

验收：每个缺陷都有失败证据、责任 Task 和终态门槛。

### 阶段 1：可信执行基础（P0）

#### Task 11：接通 PostgreSQL 唯一控制面（已完成）

实施：

- Chat 先在 PostgreSQL 创建或加载归属当前用户的 Session，通过乐观锁创建 Turn 和 Run。
- 从 PostgreSQL 加载 EntityMemory/FocusSet，每轮只生成一份不可变 execution_context。
- 在图节点边界持久化 Task、ToolCall、状态版本、错误和终态；提交失败不得返回 completed。
- Mongo 会话改为提交后的展示投影，旧记录可兼容读取，但不反向驱动执行。
- Session、Run 和恢复查询全部强制 user_id 所有权。

复核：后端重启恢复、同会话并发版本冲突、跨用户读取、PostgreSQL 提交失败。

门槛：生产 Chat 不再从 Mongo 构建执行上下文；每个 run_id 都能关联完整控制面；状态提交失败时错误成功声明为 0。

验收结果：已通过 Task 11 定向回归 27 项、PostgreSQL/MongoDB/Redis 集成前置与控制面集成 4 项、Python 编译检查和 git diff --check。真实 PostgreSQL 临时闭环已验证 Session → Turn → Run → Task → ToolCall → Event → COMPLETED，并清理测试数据。关联问题：ISS-20260903-001。

#### Task 12：收紧生产 Tool Registry 与 ToolExecutor（已完成）

实施：

- 为寻源、五维风险、ESG、舆情、合规和写回执分别定义 Pydantic v2 输出模型。
- ToolPayload 只作历史兼容类型，逐个迁移现有 28 个生产工具。
- ToolExecutor 强制执行输出 schema、Evidence 状态、来源、时间和 synthetic 标记。
- 明确定义 not_found、partial、unavailable、invalid_output、denied、timeout 和 success；空字典不再代表成功。
- 同步 service/provider 通过统一异步包装执行，不在 service 层引入 LangGraph。

复核：自动运行全部注册工具的输入、输出、证据、读写、审批、超时矩阵。

门槛：严格契约率 100%，Evidence 要求执行率 100%，非法输出放行率 0。

验收结果：28/28 生产工具使用命名 Pydantic 输出契约，Task 12 定向 11 项、Task 11/Harness/证据回归 22 项、非集成后端回归 566 项通过；空结果、未知字段、缺证据和证据元数据不完整均 fail closed。关联问题：ISS-20260903-002。

#### Task 13：字段级 Claim-Evidence 真实性门禁

实施：

- Claim 增加 fact_path、operator、value、unit、entity_id 和 dimension。
- Validator 对 Evidence facts 做类型化比较；文本结论只能来自可审计规则或已验证结构化字段。
- 覆盖度改成 entity_id x dimension x required_fact 的期望任务矩阵。
- 明确 stale、conflicting、synthetic、missing 和 unavailable 是否允许形成 Claim。
- AgentAnswer 只渲染 supported Claim，其余进入 limitations 和 next_actions。

复核：注入“Evidence=10、Claim=99”、多企业仅一家有证据、单位不一致、过期和冲突数据。

门槛：确定性 Claim 事实值支持率 100%，跨企业证据污染为 0，缺失/冲突/synthetic 误判正式结论为 0。

### 阶段 2：核心能力接线（P0）

#### Task 14：完整寻源链路迁入 Harness

实施：

- 定义 SourcingRequirement：品类、产品/物料、规格、地域、供货区域、资质、数量和必须/可选条件。
- LLM 生成需求候选，确定性解析器完成 schema 校验、品类别名和上下文绑定。
- 固定扩源顺序：本地历史/Mongo -> 飞书正式供应商与能力快照 -> 天眼查 -> 受限联网发现。
- 候选按统一 identity 归一，区分 formal、external 和 pending_verification；外部候选不写主库。
- 排序拆分需求匹配、供货能力、身份可信度、风险约束和数据完整度，每个分量都绑定证据。
- 官网、电话、邮箱、来源和更新时间进入候选契约；缺失时明确标记，不生成内容。

复核：钢材、工业相机、电机、不存在品类、本地足够、外部扩源、天眼查/联网失败和候选冲突。

门槛：需求字段准确率至少 95%，无关品类推荐为 0，来源可追溯率 100%，外部失败仍保留已有候选。

#### Task 15：五维风险与证据补全迁入 Harness

实施：

- 定义统一 RiskAssessmentRequest，支持单/多企业、财务、商务、质量、交付、合规；ESG 和舆情为扩展能力。
- 每个维度定义最小证据集、时间窗口、正式/synthetic 边界、评分规则和“数据不足”终态。
- 财务按季度主视图和年度趋势；商务 P0 正式启用供应依赖与可替代性；质量/交付缺内部数据时展示缺口。
- 供应商 x 维度矩阵作为任务期望，一家成功不得覆盖其他企业缺失。
- 仅对缺失或过期且存在允许数据源的格子生成 remediation spec；每个源最多调用一次。
- 风险比较、历史趋势和监控建议共用同一 Evidence/Claim 契约。

复核：单企业五维、两企业组合、缺财务、缺质量/交付、司法冲突、舆情补采、synthetic、趋势和比较。

门槛：缺数据误判低风险为 0，多企业任务矩阵完整率 100%，数字/等级/趋势证据支持率 100%。

Task 14 与 Task 15 必须独立实施和提交：寻源只消费已验证风险约束，风险不重复实现供应商发现。

### 阶段 3：写操作与恢复安全（P0）

#### Task 16：统一 Harness 写操作、审批与恢复

实施：

- 将加入/移出风险监控迁入同一 Harness，取消活动路径按读写切换两个运行时。
- 写工具只能由 ToolExecutor 执行，图和 Supervisor 不得直接调用 service。
- ActionProposal 绑定 Session、Run、Task、Entity、参数哈希、建议人、过期时间和版本。
- ToolExecutor 内部验证签名 ApprovalToken，不信任非空字符串。
- /chat/resume 保持 API 兼容，但服务端强制验证用户、Proposal、版本、动作哈希和决策。
- 暂停记录采用 claim/lease -> execute -> ack；失败时 release/retry，不在执行前删除。
- 只有持久化 SideEffectReceipt 后，AgentAnswer 才能声明“已加入/移出监控”。

复核：伪造/过期令牌、错用户、错提案、参数篡改、重复确认、重启恢复和执行中失败重试。

门槛：未审批写入、伪造令牌放行和重复写入均为 0；重启恢复成功率 100%。

### 阶段 4：运行时稳定性与可观测（P1）

#### Task 17：动态计划、有限 Loop、并行与异步边界

实施：

- Planner 按能力契约构建 DAG，支持供应商 x 维度、时间趋势、多企业比较和工具依赖。
- 只保留寻源扩充、证据补全、可重试提供方失败三类 Loop，均由结构化缺口触发。
- 强制 max_llm_calls、max_tool_calls、max_parallel_tasks、max_loop_iterations 和端到端 deadline。
- 无依赖任务受控并行，同 Entity 或同一写资源按键串行。
- 同步 PyMongo/OpenAI/provider 操作放入 asyncio.to_thread 或受控线程池，并按 deadline 等待。

复核：429、超时、空结果、冲突、工具卡死、预算耗尽和同会话并发。

门槛：预算越界、无限 Loop、工具消息串线和事件循环阻塞调用均为 0。

#### Task 18：真实流式 SSE、Trace 与前端服务端终态

实施：

- LangGraph 节点实时产生 workflow_status、tool_call、tool_result、evidence、approval_required、agent_answer 和 done/error。
- 事件关联 Run/Task/ToolCall 持久化后再发送；断线可按 run_id/cursor 重放。
- Trace 保存节点、工具版本、输入哈希、耗时、状态、Evidence/Claim/Proposal/Receipt 引用和错误码，不保存隐藏推理。
- 前端不再预先显示“已批准”，也不在 onDone 将 partial/needs_review/failed 改成 completed。
- 工作台展示目标、任务矩阵、工具状态、证据覆盖、Loop 退出原因、提案和真实回执。

复核：慢工具执行时的中间事件、刷新后 cursor 恢复、前后端终态一致性。

门槛：开发环境 P95 首事件不超过 2 秒，事件可关联率 100%，前端错误成功显示为 0。

### 阶段 5：真实验收门禁与收口（P0/P2）

#### Task 19：重建生产链路 E2E 与 CI 门禁

实施：

- Scenario 穿过真实 FastAPI/SSE、Harness、生产 ToolRegistry/ToolExecutor、PostgreSQL/MongoDB/Redis 和前端事件解析。
- 只替换 LLM 和外部付费 Provider；不替换 Runtime、工具契约或数据库提交。
- 从持久化 Task/ToolCall/Evidence/Claim/Receipt/Event 计算指标，禁止默认通过的自报指标。
- 覆盖品类寻源、多轮指代、显式新企业、多企业五维风险、缺失/过期/冲突/synthetic、外部扩源、审批/拒绝/重放、重启、DB 失败、超时/429、并发和断线重连。
- CI 拆分快速契约测试与三数据库 Agent E2E；数据库不可用时明确失败/阻塞，不假通过。
- 对 graphs/harness、graphs/agent_core、tools/executor 先设置至少 80% 定向覆盖率。

复核：每个 P0 场景重复 10 次，比较实体、计划、工具、Evidence、Claim 和 AgentAnswer 结构，不比较表面措辞。

门槛：固定场景、实体焦点、工具契约、Claim 支持率均 100%；错误成功、未审批写入、重复写入和未完成 tool call 均为 0。

#### Task 20：浏览器验收、问题回填和文档收口

实施：

- 用本地开发服务执行寻源、多轮风险、多企业比较、证据不足、监控提案、拒绝/确认、刷新恢复和错误降级。
- 核对页面、SSE、PostgreSQL 控制面、Mongo 展示投影和 AgentAnswer 一致。
- 回填 ISS-20260903-001 至 ISS-20260903-010 的验证结果和提交号。
- 更新 README、AGENTS、验收手册、架构图和 API 说明；仓库只保留一份活动 Agent 计划。

验收：开发环境浏览器场景全部通过，代码、测试、问题状态和文档无冲突。

## 5. 阶段依赖与停止条件

    阶段 0
      -> Task 11 -> Task 12 -> Task 13
      -> Task 14 / Task 15（分开实施，均依赖 Task 13）
      -> Task 16
      -> Task 17 -> Task 18
      -> Task 19 -> Task 20

| 阶段 | 离开条件 | 不通过时 |
|---|---|---|
| 1 | PG 事实源、工具契约和事实校验全部达标 | 不开始寻源/风险迁移 |
| 2 | 寻源与风险各自通过定向 E2E | 只阻塞对应能力，不混合修复 |
| 3 | 写操作统一进入 Harness 且审批无绕过 | 所有写工具保持 fail closed |
| 4 | 预算、Loop、SSE、Trace 和前端终态达标 | 不用提高超时掩盖卡死 |
| 5 | E2E、CI、浏览器和文档全部收口 | 不宣称 Harness 稳定完成 |

## 6. 测试分层

| 层级 | 验证对象 | Mock 边界 |
|---|---|---|
| 契约单测 | Pydantic、Resolver、Validator、预算函数 | 允许精确输入样本 |
| 图级测试 | 真实 Harness 节点、Loop、中断/恢复 | 可替换 LLM/Provider，不替换 ToolExecutor |
| 数据库集成 | PG 事务/并发，Mongo 投影，Redis 租约 | 不允许内存存储替代 |
| Agent E2E | HTTP/SSE 到 AgentAnswer/Receipt | 仅替换 LLM 和外部 Provider |
| 浏览器验收 | 页面、用户操作、恢复和错误展示 | 可用演示数据，但必须显示 synthetic |

最小验证顺序：定向单测 -> 对应 Agent E2E -> 非集成后端回归 -> 三数据库集成 -> 前端 Lint/TypeScript/Vitest/Build -> git diff --check。

## 7. 实施纪律

- 每个 Task 最多 1 次实现 + 1 次复核；同一问题最多修复 2 轮，仍失败则标记阻塞。
- PostgreSQL、MongoDB 或 Redis 任一不可用时，立即停止数据库集成和 E2E；可继续契约任务，但不得将阶段标记完成。
- 所有新问题先写入 docs/issue-log.md，修复后回填验证与提交号。
- Task 14 和 Task 15 必须独立提交和复核，禁止合并成无边界大任务。
- 每个 Task 完成时同步更新本计划勾选、问题状态和验收数字后再提交。
- 不修改已有公开 API 返回结构；新字段使用向后兼容可选字段或新版本端点。
- 不在 service 层引入 LangGraph。

## 8. 工期估算

| 阶段 | 单人估算 | 主要交付 |
|---|---:|---|
| 0 基线 | 0.5-1 天 | 失败探针和基线 |
| 1 可信基础 | 5-7 天 | PG 事实源、工具契约、证据门禁 |
| 2 核心能力 | 5-7 天 | 完整寻源和五维风险 |
| 3 审批安全 | 3-4 天 | 唯一写链路、安全恢复和回执 |
| 4 稳定与可观测 | 4-6 天 | 动态计划、有限 Loop、SSE/Trace |
| 5 验收收口 | 3-5 天 | E2E、CI、浏览器和文档 |

总计约 20-30 个开发日。每个 Task 都形成独立可审查提交，不等待全部任务结束后集中提交。

## 9. 总完成标准

- D01 至 D20 全部关闭，或按两轮修复上限明确标记阻塞。
- Chat 只有一个 Harness，读写不再切换执行内核。
- PostgreSQL 是唯一执行状态事实源，MongoDB 只保存业务、证据和展示数据。
- 生产工具严格契约、ToolExecutor 必经、Evidence 强制和 Claim 值校验均为 100%。
- 寻源和五维风险通过各自 E2E，无关推荐和缺数据伪结论为 0。
- 无有效审批不执行写操作，无真实回执不声明成功。
- 无无限 Loop、预算越界和工具消息串线，重启与页面刷新可恢复。
- P0 场景通过生产链路重复验证，前后端终态、Evidence、Claim 与数据库状态一致。
- 问题日志、README、AGENTS、验收手册和代码一致，仓库只有本文件声明为活动 Agent 计划。
