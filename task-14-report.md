# Task 14 Report

## P1 修复交付

- Offline Eval 改为 executable input + injectable runner/trace recorder；发布评估只接受真实 `GraphTraceAdapter`，无 adapter 明确返回 unavailable/failed，禁止 fixture 提供 `observed` 真值。
- 指标按 case 聚合 precision/recall（空预测 precision=0），校验 citation 覆盖 evidence ref、evidence state、审批角色/决定/提案/幂等键/写入、重放副作用、澄清、关键缺证据推荐和 start/end latency；上述安全指标全部纳入 passed gate。
- 创建、澄清恢复、身份恢复和审批 API 接入 `agent_run_v2_route`；disabled/未进入灰度明确拒绝 V2，Shadow 审批只读并禁止领域写入，Internal/Canary/Default 继续由 role/stable hash 决定。
- 新增 `backend/app/core/rollout_gate.py`：阶段顺序、最小样本、完整观测窗口、全部质量/安全门槛、人工审批记录和在途 Run/proposal/outbox 检查；rollback 明确冻结新 V2 动作、保留 Run/checkpoint/audit、处置 pending proposal/leased outbox 并要求人工复核后恢复。
- 高基数指标说明限定为 V2 新增 metrics scope，未扩大历史 metrics 改动。
- Eval `passed` 现在同时受实际 trace latency、macro precision/recall、citation/evidence completeness、unsafe action、critical missing evidence 和 clarification expected-vs-observed gates 约束；trace 必须有非负 start/end，duration 为 `end-start`。
- Promotion latency 按实际 P95 `<= threshold` 判断；promotion/rollback guard 提供 config 状态转换，rollback 设置 `rollback_frozen`，拒绝新 V2/Shadow 请求并明确暂停在途 Run、冻结 proposal、停止新 Outbox lease 和完成/过期既有 lease 的处置。
- API route 矩阵覆盖 disabled/shadow/internal/canary/default；保持现有 V2 409 fail-closed 语义，不新增 legacy fallback 响应；Shadow 在 graph/service/action/outbox 边界均阻断写入。
- 第三轮：`create`、澄清恢复和身份恢复在 Shadow 直接返回 read-only 409，不创建/恢复可执行 V2 graph；rollback latch 接入 API、graph runner 和 Outbox worker，停止新 V2 action/lease，promotion 成功后解除当前进程冻结。
- Eval 增加 `GraphTraceAdapter`/`ProductionGraphTraceAdapter` 与 `trace_source` 报告字段；默认无 adapter 为 unavailable/failed，关键 approval/recovery 事件必须来自真实 recorder。

## 交付

- 新增 `backend/app/evals/sourcing_risk.py` 离线评估 runner 与固定 12 场景 JSON 集。
- 覆盖 requirement parsing、local-first discovery、identity/evidence safety、decision/action approval boundary、recovery fail-closed。
- 输出 task quality、candidate precision/recall、citation/evidence completeness、unsafe action rate、clarification rate 与 latency p50/p95/max。
- 新增 V2 低基数 Prometheus metrics，拒绝 company/run/user/provider 任意 ID 作为 label。
- 新增安全默认 feature flags、role-gated internal、deterministic canary routing。
- README 增加 Shadow/Internal/Canary/Default 发布顺序、人工审批、观测门槛与回滚步骤。

## 验证

```text
cd backend && pytest -q tests/test_rollout_gate.py tests/test_sourcing_risk_evals.py tests/test_agent_run_api.py
85 passed, 1 deselected, 8 warnings（本轮 focused suite；已排除需要本地 PostgreSQL 的真实集成用例）

cd backend && python -m compileall -q app
passed

git diff --check
passed

本轮 focused suite 未包含需要本地 PostgreSQL 的真实 Outbox/事务集成用例；该环境的 localhost:5432 访问受限，已单独保留为环境阻塞，不将其误报为通过。

补充验证：包含 `tests/test_outbox_service.py` 的扩展集合实际为 95 passed、17 failed、8 warnings；17 项失败均在 PostgreSQL 连接/真实事务初始化处因当前沙箱禁止访问 `localhost:5432`，并非断言失败。未将这些环境受限用例计入通过数。
```

前端未受影响，未修改 service 层或既有 API 返回结构。既存的 `task-9-review.md` 至 `task-13-review.md` 为用户工作区文件，未纳入提交。
# Task 14 P1 closure update

- Eval no longer selects a production deterministic fallback. Without an injected `GraphTraceAdapter`, the report is `trace_source=unavailable`, `passed=false`, and returns an actionable adapter-injection error.
- The graph adapter seam records trace-derived latency and evaluates per-case macro candidate precision/recall, evidence/citation completeness, unsafe action, clarification, recovery, and latency gates. Empty predicted candidates retain precision `0.0` semantics.
- Rollout state is persisted in PostgreSQL through `agent_rollout_control` and the existing agent-run repository boundary. API, LangGraph runner, and Outbox worker read the durable state on every entry; control-plane read/write failures fail closed.
- Tests cover injected trace execution, unavailable Eval default, promotion/rollback, independent state readers, and unavailable control plane. Review markdown files were not modified.
- Production wiring now reads the durable rollout state at API read/write boundaries, graph start/resume, action proposal/approval/execution, and V2 Outbox claim; unavailable or frozen control state fails closed.
- `GraphTraceRecorder` is bound around the real sourcing-risk graph runner and node events, with a production `ProductionGraphTraceAdapter` bridge; Eval without that adapter remains explicitly unavailable/failed.
- Shadow is enforced as no-write at graph, service/action, and Outbox boundaries. The control API is `/api/v1/admin/agent-run-rollout/promote` and `/rollback`; disabled/non-target routes retain 409 fail-closed semantics.
