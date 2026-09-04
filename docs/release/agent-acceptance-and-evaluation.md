# Agent 验收与评测手册

日期：2026-08-18  
状态：历史质量基线；当前 Harness 门槛以 `../superpowers/plans/2026-09-03-agent-harness-hardening-plan.md` 第 4 至 9 节为准
适用范围：采购决策 Agent 的功能验收、回归、离线评测和灰度判断。

## 1. 必过功能场景

| 场景 | 预期 |
|---|---|
| 单品类寻源 | 先检索本地库；候选不足时按预算扩源；外部候选标记来源和未验证状态 |
| 多轮指代 | “这些企业”“前两家”“它们”解析为结构化会话状态中的目标 |
| 组合分析 | 多家企业的风险、ESG、舆情、合规形成完整任务矩阵；无依赖维度可并行 |
| 证据不足 | 明确缺失维度、来源和置信度，不输出无依据风险结论 |
| 外部信息 | 官网、电话、邮箱显示来源与验证状态，抓取失败不伪造联系方式 |
| 写操作 | 仅创建待审批项；未批准时不得修改主数据、监控、准入或通知 |
| 中断恢复 | 审批、澄清、取消与恢复保持版本一致，不重复执行动作 |

## 2. 自动化测试门槛

- 单元测试覆盖目标解析、任务矩阵、Loop 退出、证据合并、策略评分、审批拦截和错误降级。
- 图级测试覆盖组合任务并行/依赖、部分失败、暂停恢复和 SSE 事件映射。
- `agent_e2e` 覆盖固定的跨层用户链路：请求级会话快照复用、“本地候选 → 审批暂停 → API 批准恢复 → 成功工具回执”和 Agent `workflow_status` 生命周期；同时执行 12 条无外部服务的多轮 Eval。
- `agent_e2e_live` 必须穿过真实 FastAPI/Harness/ToolRegistry/ToolExecutor、PostgreSQL、MongoDB、Redis 和 PostgreSQL checkpoint；只允许替换外部 LLM/Provider，不允许替换运行时或数据库。它在 CI 中作为独立门禁执行。
- 前端测试覆盖聊天引用卡、工作流、审批卡、部分结果与错误状态。
- 集成测试仅在 PostgreSQL 与 MongoDB 健康时运行；任一不可用立即停止集成测试并报告阻塞。
- 每次合并前必须通过相关后端 pytest、前端测试、前端构建和 `git diff --check`。

执行命令：

```bash
cd backend
python -m pytest -m "not agent_e2e" -v
python -m pytest -m "agent_e2e and not agent_e2e_live" -v
python -m pytest -m agent_e2e_live -v

# Harness 核心执行路径定向覆盖率
python -m pytest -m "not integration and not agent_e2e" \
  --cov=app.graphs.harness.graph \
  --cov=app.graphs.harness.state \
  --cov=app.graphs.agent_core.answer_contract \
  --cov=app.graphs.agent_core.evidence_ledger \
  --cov=app.tools.executor \
  --cov-fail-under=80
```

## 3. 离线 Eval 集

Eval 集必须包含固定输入、期望目标、最小证据要求和可接受的降级结果，至少覆盖：

- 本地命中、外部补充、无结果和错误品类别名；
- 单企业、多企业、显式名称、单复数指代和序数指代；
- 风险、ESG、舆情、合规及组合请求；
- 证据冲突、过期、缺失、供应商身份不确定；
- 审批拒绝、批准、重复提交和恢复。

评测指标包括目标解析准确率、任务完成率、证据覆盖率、无依据结论率、错误降级正确率、审批绕过率、端到端耗时及人工复核率。不得将企业名称、需求正文、用户标识或 run_id 写入 Prometheus label。

## 4. 发布/灰度门槛

- 不存在审批绕过、跨会话供应商泄漏、无限 Loop 或重复业务写入。
- 核心回归场景全部通过；失败场景必须产生可解释的结构化状态。
- 缺失或冲突证据不会被展示为低风险或已验证。
- 功能开关支持逐项回退：统一状态、任务矩阵、受控 Loop、校验器。
- 灰度阶段持续观察 Trace 中的 Loop 耗尽率、部分完成率、澄清率、人工复核率与外部源失败率；超过基线后回退相应开关。

## 5. 执行纪律

- 每个阶段最多一次实现和一次复核；同一问题最多修复两轮，随后标记阻塞。
- 子任务只在完成、失败或阻塞时汇报；30–60 分钟汇总一次。
- 业务数据删除、导入与其他写操作均遵守人工确认边界。

## 6. 当前基线记录

| 日期 | 检查项 | 结果 | 备注 |
|---|---|---|---|
| 2026-08-18 | Agent 相关后端单元/图测试 | 通过，46 项 | `test_agent_context`、`test_clarification`、`test_agent_supervisor_graph`、`test_sourcing_risk_discovery_service` |
| 2026-08-18 | 聊天与寻源工作台前端测试 | 通过，26 项 | `ChatView`、`SourcingRiskWorkbench` |
| 2026-08-18 | 前端生产构建 | 通过 | `tsc -b && vite build` |
| 2026-08-18 | 集成/端到端验证前置健康检查 | 阻塞 | `http://127.0.0.1:8000/health/ready` 无法连接；未运行集成测试，待服务与 PostgreSQL/MongoDB 可用后恢复 |
| 2026-08-20 | PostgreSQL、MongoDB、Redis 与 checkpoint 就绪检查 | 通过 | `/health/ready` 四项均为 `ok`；恢复后完成真实工作台本地候选与审批卡验证。 |
| 2026-09-01 | Agent 自动 E2E 回归 | 通过，9 项 | 跨层工作流、审批暂停/恢复、状态生命周期和 12 条固定多轮 Eval 均纳入 `agent_e2e`；GitHub CI 单独执行 `-m agent_e2e`。 |
| 2026-09-01 | 三表同步、寻源降级与风险历史版本定向回归 | 通过 | 后端相关回归 33 + 31 + 8 项通过；前端 9 个测试文件、50 项通过，Lint、TypeScript 检查和生产构建通过。真实浏览器验收因后端重启后登录态失效待人工登录。 |
| 2026-09-01 | 数据库集成回归 | 通过，228 项 | `/health/ready` 显示 PostgreSQL、MongoDB、Redis 和 checkpoint 均正常；集成测试完整执行通过。 |
| 2026-09-01 | 后端完整非集成与 Agent E2E 回归 | 通过，501 + 9 项 | 非集成回归 501 项，`agent_e2e` 9 项；Python 编译检查和 `git diff --check` 通过。 |
| 2026-09-04 | Task19 Harness 生产链路与稳定性门禁 | 通过 | 离线 Agent E2E 24 项、P0 固定场景 150/150、真实 `agent_e2e_live` 3 项、三数据库集成 229 项；核心 Harness 覆盖率 81.89%；前端 Lint、TypeScript、Vitest 54 项和生产构建通过；真实 SSE 断线重连按 `Last-Event-ID` 回放通过。 |
