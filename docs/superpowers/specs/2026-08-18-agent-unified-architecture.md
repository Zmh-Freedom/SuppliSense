# 采购决策 Agent 统一架构设计

日期：2026-08-18  
状态：历史架构基线；当前 Agent 架构以 `../plans/2026-09-01-agent-harness-runtime-plan.md` 为准
范围：智能寻源、供应商风险分析、多轮对话、审批与可追溯运行；不包含生产部署改造。

## 1. 目标与架构原则

系统建设一个面向采购决策的统一 Agent：理解采购需求和上下文、发现供应商、完成风险/ESG/舆情/合规分析、基于证据给出建议，并将任何业务写操作交给人工审批。

- `ConversationState` 是会话事实、当前目标和待办的唯一事实源；自然语言历史只提供语义背景。
- LLM 只负责理解、规划、归纳和解释；实体绑定、证据状态、硬门槛、评分、权限和写入由确定性代码控制。
- 寻源、补证、校验和错误恢复可以循环，但每种循环都有次数、时间和覆盖率预算。
- 外部发现结果均为 `unverified` 候选，未经审批不得写入供应商主数据。
- 运行记录、轨迹、证据索引和图检查点可自动持久化；供应商主数据、监控、准入、通知等业务变更必须审批。

## 2. 统一架构

```text
用户输入
  -> Conversation State Loader（每个请求仅一次，作为 execution_context 透传）
  -> Target Resolver（名称、指代、序数、排除条件）
  -> Task Parser / Task Matrix Planner
  -> Supervisor（调度、依赖、预算、恢复）
       -> Sourcing Worker + Sourcing Loop
       -> Risk / ESG / Sentiment / Compliance Workers
       -> Evidence Completion Loop
       -> Result Validator Loop
  -> Evidence Merger + Decision Builder
  -> Human Approval Gate（仅业务写操作）
  -> Answer + Trace + Conversation State Update
```

依赖方向保持为 `API -> graphs -> services -> repositories/providers`。`services/` 不依赖 LangGraph；图仅编排、路由、暂停和恢复。

## 3. 组件职责

| 组件 | 负责 | 不负责 |
|---|---|---|
| Conversation State | 活跃供应商、选中目标、需求、当前任务、澄清与审批状态 | 风险结论 |
| Target Resolver | 显式名称、单复数指代、序数、排除条件解析 | 虚构或猜测企业 |
| Task Matrix Planner | 生成“企业 × 分析维度”子任务和依赖 | 执行工具、直接下结论 |
| Supervisor | 调度、并行、预算、暂停、恢复、完成判断 | 直接查询业务库 |
| V2 领域服务 | 需求、策略、发现、身份、证据、决策、动作提案 | LangGraph 编排 |
| 专业 Worker | 在固定输入、预算、范围内完成一个专业子任务 | 扩大任务范围或写业务数据 |
| Loop Controller | 继续、降级、停止、转人工的判断 | 覆盖或伪造工具结果 |
| Evidence Merger | 去重、冲突、新鲜度、可信度和来源合并 | 修改原始证据 |
| Decision Builder | 门槛、排序、结论、限制和行动建议 | 执行业务动作 |
| Approval Gate | 创建待审批项、等待、批准后放行 | 自动批准 |

## 4. 运行协议

### 4.1 ConversationState

会话状态至少包含：`session_id`、`active_suppliers`、`selected_supplier_names`、`current_requirement`、`current_task`、`recent_tasks`、`pending_clarification`、`pending_approvals` 与版本时间。

供应商引用必须带来源、身份状态和联系方式状态；旧会话可一次性从历史回答兼容提取，迁移后必须写回结构化状态。

### 4.2 Task Matrix 与 Worker Result

任务矩阵以 `supplier_name/company_id × dimension` 建模，子任务显式标记依赖、必需性、状态和预算。Worker 的返回统一包含：状态、摘要、发现、证据引用、置信度、限制、建议动作及结构化错误。

空结果必须说明“无数据/调用失败/不适用/未执行”中的准确状态；不得用模板化结论伪装完成。

### 4.3 受控 Loop

| Loop | 触发 | 上限 | 正常退出 |
|---|---|---:|---|
| 寻源 | 本地候选不足 | 3 层：本地→天眼查→联网/官网补全 | 候选足够、来源耗尽或预算耗尽 |
| 补证 | 关键维度证据不足 | 2 轮 | 覆盖达标、无更多可信来源或转人工 |
| 校验 | 结果存在缺项、冲突或不一致 | 1 次定向修复 | 通过或标记 `needs_review` |
| 错误降级 | 可重试的瞬时失败 | 1 次重试；同问题最多 2 轮 | 成功、降级为部分结果或失败 |

确定性评分、审批执行和无限“自我反思”不进入 Loop。

## 5. 业务与安全边界

- 单个 V2 寻源 Run 默认处理一个采购品类和一组规格；多品类进入澄清，不自动拆单。
- 本地供应商库优先；天眼查、联网搜索和官网补全仅扩充候选与公开信息。
- 候选正式身份使用稳定 `company_id`；名称仅用于展示和兼容检索。
- 缺失、过期、冲突、无风险必须为不同证据状态；关键证据缺失或制裁数据不可用时，结论转人工复核。
- 策略使用“默认策略 + 品类策略模板”，每个 Agent Run 冻结版本快照。
- 写供应商主数据、监控清单、准入状态或发送通知前，必须创建 `ActionProposal` 并经人工审批；批准后的可靠投递复用 Outbox。

## 6. 兼容与迁移

`/api/v1/chat/stream`、ReAct、Plan-Execute 和已有图继续兼容。聊天 API 生成的 `execution_context` 在本次请求内透传到全部执行图；供应商工具结果通过共享引用收集器合并，禁止由各图重新解析自然语言答案。`agent_run`、事件回放、检查点和审批恢复是统一运行时基础设施。恢复时 LangGraph `Command` 原样传入图，确保批准动作继续原暂停分支而非重新规划。

推荐的稳定入口是：普通单一查询走兼容图；组合型寻源与风险任务进入 Supervisor；所有路径都读取、更新同一 ConversationState。

## 7. 文档治理

本文件保留为 2026-08-18 阶段的历史架构基线。当前 Agent 架构、任务顺序、迁移和质量门槛统一以 `docs/superpowers/plans/2026-09-01-agent-harness-runtime-plan.md` 为准。

历史 V2 和 Supervisor 设计/计划保存在 `docs/archive/agent-design/`，只用于实现追溯，不再作为新开发依据。
