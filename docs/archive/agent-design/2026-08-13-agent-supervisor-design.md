# 寻源与风险 Supervisor Agent 设计

> **归档说明（2026-08-18）：** 本文档已由 `docs/superpowers/specs/2026-08-18-agent-unified-architecture.md` 取代；仅保留用于追溯 Supervisor 协议与实现背景。

日期：2026-08-13
状态：已确认，待实施计划
范围：Agent 核心能力重构；不包含生产上线环境改造

## 1. 目标

建设一个统一的 Supervisor Agent，能够理解组合型采购与风险任务，自动拆解任务并协调寻源、风险、合规、舆情等专业子 Agent，形成有证据、有可信度、有行动建议的结果。

所有写操作必须经过人工确认。Agent 只能提出建议、创建待确认项和暂停等待，不能直接修改供应商主数据、监控列表、准入状态或发送通知。

## 2. 总体架构

```text
用户请求
  -> Supervisor
  -> Intent Router
  -> Task Planner
  -> 专业子 Agent 并行/依赖执行
  -> Evidence Merger
  -> Decision Agent
  -> Human Approval Gate
  -> 最终回答或待确认动作
```

第一阶段采用“Supervisor + Task Planner + 专业子图”的架构。保留现有 ReAct、Plan-Execute 和旧 Supervisor 作为兼容路径，仅将组合型任务路由到新 Supervisor。

### 2.1 组件职责

| 组件 | 职责 | 边界 |
|---|---|---|
| Supervisor | 管理整体流程、调度子 Agent、处理失败和汇总结果 | 不直接执行业务查询 |
| Intent Router | 判断寻源、风险、合规、舆情或组合任务 | 不生成最终结论 |
| Task Planner | 拆解任务、确定依赖、决定并行关系 | 不执行数据查询 |
| Sourcing Agent | 解析采购条件、检索候选、计算匹配度 | 不直接纳入供应商库 |
| Risk Agent | 调查并解释财务、司法、经营、ESG、连续性风险 | 不直接处置风险 |
| Compliance Agent | 检查资质、制裁和准入规则 | 不修改合规资料 |
| Sentiment Agent | 分析舆情、负面事件和趋势 | 不直接发送预警 |
| Evidence Merger | 证据去重、冲突标记、新鲜度和可信度整合 | 不修改原始证据 |
| Decision Agent | 生成排序、风险结论和行动建议 | 不执行写操作 |
| Human Approval Gate | 拦截写操作并等待人工决定 | 不自动放行 |

## 3. 统一状态协议

Supervisor 使用可持久化的 LangGraph 状态：

```python
class AgentTaskState(TypedDict, total=False):
    run_id: str
    user_query: str
    intent: dict
    plan: dict
    task_status: str

    sourcing_result: dict
    risk_result: dict
    compliance_result: dict
    sentiment_result: dict

    evidence: list[dict]
    findings: list[dict]
    recommendations: list[dict]
    pending_approvals: list[dict]
    final_answer: str
    error: dict | None
```

任务状态：

```text
CREATED -> PLANNING -> EXECUTING -> EVIDENCE_MERGING
         -> DECISION_READY -> COMPLETED
                              -> WAITING_HUMAN_APPROVAL
```

异常状态：`NEEDS_CLARIFICATION`、`PARTIAL_COMPLETED`、`FAILED`。

## 4. Task Planner

Planner 输出结构化任务计划：

```python
{
    "tasks": [
        {"task_id": "sourcing", "agent": "sourcing", "depends_on": [], "required": True},
        {"task_id": "risk", "agent": "risk", "depends_on": ["sourcing"], "required": True},
        {"task_id": "compliance", "agent": "compliance", "depends_on": ["sourcing"], "required": True},
        {"task_id": "sentiment", "agent": "sentiment", "depends_on": ["sourcing"], "required": False}
    ]
}
```

规则：

- 无依赖任务并行执行。
- 风险、合规、舆情默认可并行。
- 寻源结果为空时，跳过无意义的后续调查。
- 必需任务失败时进入 `PARTIAL_COMPLETED` 或 `FAILED`。
- 非必需任务失败时保留其他结果并明确标注缺失项。

## 5. 子 Agent 统一返回协议

所有子 Agent 返回同一结构：

```python
{
    "agent": "risk",
    "status": "completed",
    "summary": "发现 2 项中高风险因素",
    "findings": [
        {
            "type": "judicial_risk",
            "level": "high",
            "title": "存在未结重大诉讼",
            "description": "...",
            "impact": "...",
            "confidence": 0.91
        }
    ],
    "evidence": [
        {
            "evidence_id": "...",
            "source": "...",
            "source_type": "official",
            "collected_at": "...",
            "freshness": "fresh",
            "confidence": 0.95
        }
    ],
    "recommended_actions": [],
    "metrics": {"duration_ms": 1200, "evidence_count": 6},
    "error": None
}
```

结果必须区分事实、推断、建议和待确认操作。没有证据支撑的 LLM 推断不得进入最终风险结论。

## 6. 证据合并与决策

Evidence Merger 规则：

1. 相同企业、维度和来源的证据去重。
2. 官方来源优先于第三方来源。
3. 新证据优先于过期证据。
4. 冲突证据保留双方并标记 `conflict`，不自动覆盖。
5. 缺少关键证据时，结论标记为需人工复核。
6. 子 Agent 失败时保留其他 Agent 结果并标注未完成项。

Decision Agent 只输出：推荐排序、风险结论、证据说明、可信度、建议动作和待确认项，不执行动作。

## 7. 人工确认边界

以下操作必须进入统一 `Human Approval Gate`：

- 加入或移出监控列表；
- 纳入或移出供应商库；
- 企业身份合并；
- 采纳外部候选供应商；
- 供应商准入或淘汰；
- 发送风险通知；
- 修改风险处置状态。

待确认项结构：

```python
{
    "approval_id": "...",
    "action_type": "add_to_watchlist",
    "target": {"company_id": "...", "company_name": "..."},
    "reason": "...",
    "impact": "...",
    "status": "pending",
    "expires_at": "..."
}
```

确认前不得写入业务数据。拒绝后记录拒绝原因、保留分析结果且不重复执行相同动作。

## 8. 异常与重试

每个子 Agent 最多执行两轮。网络超时、临时第三方失败和 LLM 格式错误可重试一次；参数缺失、身份无法确认、证据冲突和权限不足直接失败或转人工复核。

第二次失败后，子 Agent 标记 `FAILED`，Supervisor 依据任务是否必需决定降级或停止。禁止将失败伪装为成功，也禁止用默认风险分数掩盖关键数据缺失。

最终任务状态含义：

- `COMPLETED`：证据充分，可直接回答；
- `PARTIAL_COMPLETED`：部分 Agent 失败，但可给出有限结论；
- `NEEDS_CLARIFICATION`：采购条件不足，需要追问；
- `WAITING_HUMAN_APPROVAL`：存在待确认写操作；
- `FAILED`：无法安全生成结论。

## 9. 对外回答格式

最终回答按以下顺序组织：

1. 结论摘要；
2. 关键发现；
3. 证据、来源、更新时间和可信度；
4. 推荐排序和淘汰原因；
5. 建议动作与待确认事项。

回答必须明确区分“事实、推断、建议、待确认操作”，并说明数据缺失、冲突或子 Agent 未完成的部分。

## 10. 第一阶段实施范围

### 包含

1. 新建统一 Supervisor 状态协议。
2. 新建 Task Planner 节点。
3. 统一寻源、风险、合规、舆情子 Agent 接口。
4. 实现并行执行和依赖调度。
5. 实现 Evidence Merger。
6. 实现 Decision Agent。
7. 接入统一 Human Approval Gate。
8. 将组合任务路由到新 Supervisor。
9. 保留现有图作为兼容路径。
10. 增加单元、图级、失败隔离和人工确认边界测试。

### 不包含

- 生产上线与 Compose 环境改造；
- 外部供应商真实数据接入；
- 自动写入供应商主数据；
- 自动执行任何审批动作。

## 11. 验收标准

- 组合型寻源与风险请求能够进入新 Supervisor。
- Planner 能正确拆解必需任务和依赖关系。
- 无依赖子 Agent 能并行执行，单个失败不丢失其他结果。
- 所有最终风险结论都能关联证据或明确标记缺失。
- 外部候选、身份不确定和制裁异常不会直接进入正式推荐。
- 所有写操作在执行前进入人工确认状态。
- 人工拒绝不会执行写操作，且可追溯拒绝原因。
- 现有 ReAct、Plan-Execute 和已有 Agent 测试不回归。
