# 第三阶段 3A：独立 Agent worker 与 durable job Implementation Plan

> **For agentic workers:** 按任务顺序执行，每个任务先写失败测试再实现；保留现有 API、SSE 事件名和返回结构。

**Goal:** 把只读 Harness 的图执行从 FastAPI 进程迁到独立 Agent worker。API 只创建/查询/取消 durable job 并订阅事件；worker 按 PostgreSQL 租约领取、续租、恢复和提交结果。

**Architecture:** 在现有 RunCoordinator 的 `agent_run_executions` 执行许可之上增加独立 `agent_execution_jobs` 队列。job 负责排队与租约，execution 继续负责 attempt/fencing token。worker 在同一短事务内领取 job 与执行许可，运行现有 Harness；进度和终态仍写入 `agent_run_events`，最终结果由 Coordinator 原子提交。API 不再启动 Harness 图任务，只订阅事件。

**Tech Stack:** FastAPI、psycopg2、LangGraph、PostgreSQL、pytest；不增加运行依赖。

**Spec:** `docs/superpowers/specs/2026-10-19-durable-harness-evolution-design.md` 第 6.1、6.2、6.4 节。

## Global Constraints

- 不新增按聊天 mode 切换执行内核的分支；只由 `AGENT_EXECUTION_MODE` 控制 inline / worker 部署开关。
- PostgreSQL 是 job、attempt、租约和终态的唯一事实源。
- 同一 job 同时只能有一个有效 owner；旧 attempt 的提交必须被 fencing token 拒绝。
- 图执行使用同一 Harness；service 层不引入 LangGraph。
- 现有 `/api/v1/chat/stream`、SSE 事件格式和既有查询契约保持兼容。
- 恢复先核对 checkpoint 与已提交快照；无法核对时暂停并给出原因，不伪造成功。
- 默认 heartbeat 5 秒、lease 30 秒、poll 1 秒；lease 至少为 heartbeat 的 3 倍。
- 不删除现有测试；发现新问题先登记 `docs/issue-log.md`。

## Scope

3A 只迁移只读 Harness。Supervisor 写审批仍走现有兼容入口；scheduler、delivery、迁移/回滚入口留给 3B–3D。

## File Structure

- 修改 `backend/app/db/init_pg.py`：新增 `agent_execution_jobs` 表、索引和 `agent_run_executions.recovery_count`。
- 新增 `backend/app/domains/agent_run/execution_jobs.py`：入队、领取、续租、完成/失败、查询。
- 修改 `backend/app/domains/agent_run/coordinator.py`：可组合的 claim/takeover，终态同步 job。
- 修改 `backend/app/domains/agent_run/state_store.py`：worker 模式创建 turn/run/job 同事务。
- 新增 `backend/app/workers/agent_worker.py`、`backend/app/workers/__init__.py`：worker 入口与执行循环。
- 复用 `backend/app/graphs/streaming.py` 已有生产者与恢复入口，无需修改该文件。
- 修改 `backend/app/api/chat.py`：worker 模式只订阅事件；新增 run 查询端点。
- 修改 `backend/app/main.py`：worker 模式不启动过期执行暂停循环。
- 修改 `backend/app/core/config.py`、`backend/.env.example`：模式与租约配置。
- 新增 `backend/tests/test_agent_worker_integration.py`、`backend/tests/test_agent_worker_shutdown.py`、`backend/tests/test_agent_worker_streaming.py`、`backend/tests/test_outbox_database_safety.py`。

## Task 1: durable job 表与租约领取

**Interfaces:** `enqueue_job_with_cursor(cur, run_id, max_recoveries=3)`；`claim_job(worker_id, lease_seconds, queue="agent") -> dict | None`；`heartbeat_job(job_id, worker_id, lease_seconds) -> bool`；`finish_job_with_cursor(cur, run_id, status, error=None)`。

- [x] 写失败测试：同库并发两个 worker 领取同一 job，只有一个成功；租约过期后可被第二个 worker 领取；心跳丢失后不能续租。
- [x] 运行测试观察 RED，使用 `run_lifecycle_test`。
- [x] 实现 DDL、job repo 和 Coordinator 可组合 claim/takeover。
- [x] 验证 job 与 execution 的 fencing token 同步，旧 attempt 的 `check/finish` 抛 `ExecutionLost`。
- [x] 运行新增测试及 `test_run_coordinator_integration.py`，期望 PASS。

## Task 2: 独立 Agent worker

**Interfaces:** `run_worker_once(worker_id=None) -> dict`；`main()` 持续轮询；`python -m app.workers.agent_worker`。

- [x] 写失败测试：worker 领取 job 后执行现有 Harness，事件/终态落 PG；worker 未启动时 API 创建的 job 保持 QUEUED。
- [x] 写失败测试：worker 在 progress 中途失联，第二个 worker 领取过期 job，新 attempt 完成，旧 permit 的 finish 被拒。
- [x] 写失败测试：恢复时已提交的 tool/assessment 结果从快照复用，不重新调用工具。
- [x] 运行测试观察 RED。
- [x] 实现 worker 循环、heartbeat、checkpoint 恢复、异常暂停/失败收口。
- [x] 验证 lease/attempt/fencing token 和 SIGKILL 后无 finally 也能接管。

## Task 3: API 创建、查询、取消、订阅

**Interfaces:** worker 模式下 `start_harness_turn` 同事务插入 job；`GET /api/v1/chat/runs/{run_id}` 返回 Run、execution、job 状态；`/chat/stream` 订阅持久化事件；`/chat/runs/{run_id}/events` 继续可重放。

- [x] 写失败测试：worker 模式下聊天请求只创建 job，不调用 inline `stream_harness_graph`；SSE 仍输出 run/workflow/done。
- [x] 写失败测试：API 创建 job 后立即退出，worker 完成，查询端点返回终态；重连事件无重复 done。
- [x] 写失败测试：取消 queued/running job 后 worker 不启动新工具，终态唯一。
- [x] 运行测试观察 RED。
- [x] 实现 worker 模式分支、事件轮询订阅、查询端点和取消联动。
- [x] 验证 inline 模式回归不变；worker 模式 API 不启动图任务。

## Task 4: 3A 验收与文档

- [x] 真实开发库启动 API + 两个 worker，验证：
  - 同一 job 两 worker 并发领取只有一个 owner。
  - API 创建 job 后重启，worker 仍完成，查询可读。
  - `kill -9` 当前 worker 后另一 worker 在租约到期后接管，旧 worker 不能覆盖终态。
- [x] 更新 `docs/issue-log.md` 中 007/008/019/004 的 3A 进度。
- [x] 更新设计文档状态与验收记录。
- [x] 运行后端定向、离线全量、Agent E2E；记录结果与提交号。

## Acceptance Matrix

| 验收 | 证据 |
| --- | --- |
| 两 worker 同时领取，仅一个执行者 | 并发领取测试只有一个 permit；job/execution 只有一个 attempt/token |
| API 重启后任务仍完成 | API 创建 job 后终止；worker 从 QUEUED 领取并提交终态；查询端点可读 |
| 杀死 worker 后接管，旧 worker 不能覆盖 | 过期 job 被新 worker 领取；新 token；旧 permit finish 抛 `ExecutionLost`；终态事件唯一 |

## Implementation Status (2026-10-21)

- 已完成 worker 模式创建、查询、取消、事件订阅、租约领取/续租/接管、checkpoint + 已提交账本恢复、失败终态收口。
- 已完成显式恢复的 worker 路径：`/chat/resume` 在 worker 模式下通过 `resume_for_worker` 重新入队，由 worker 执行，API 只订阅事件；inline 路径保持不变。
- 未实现：scheduler、delivery 的独立进程拆分，以及版本化迁移/回滚入口（3B–3D）。

## Acceptance Record (2026-10-21, 开发库)

环境：`scripts/db-tunnel.sh` 连接服务器 `sra` / `tianyancha`；API 与 worker 均以 `AGENT_EXECUTION_MODE=worker`、lease 10s、heartbeat 2s 启动。

1. **两个 worker 并发领取**
   - Run `330a8644-6bc8-4374-8ff0-ba4894f915a4`。
   - 同时启动 `concurrent-w1`、`concurrent-w2`：w1 `executed=1`，w2 `executed=0`。
   - `agent_execution_jobs.attempts=1`、`recoveries=0`；execution 仅一个 attempt `b281a5ea…`（owner `concurrent-w1`，fencing 1）；`done` 事件 1 条。

2. **API 重启后任务仍完成**
   - Run `e79e9caf-0ebd-49b4-b11e-af404b1c3153` 由 API 创建，job 保持 `QUEUED`。
   - 停止 API 后才启动 worker `accept-worker-a`；worker 从 PostgreSQL 领取并完成。
   - 重启 API 后 `GET /api/v1/chat/runs/{id}` 返回 `NEEDS_REVIEW`、execution `SUCCEEDED`、job `SUCCEEDED`（attempts=1）；事件重放 `done` 恰好 1 条。

3. **kill -9 后接管，旧 worker 不能覆盖**
   - Run `89971778-4d90-4f89-a784-05db2eb4e516`。
   - `kill-worker-a` 领取 attempt `bcafe9cb…`（fencing 1）后被 `SIGKILL`；job 保持 `LEASED`。
   - 租约到期后 `kill-worker-b` 接管为 attempt `021bc3fb…`（fencing 2），job `attempts=2`、`recoveries=1`，Run 正常终态，`done` 1 条。
   - 用旧 attempt/token 调用 `RunCoordinator.finish` 被 `ExecutionLost` 拒绝（Run 已结束）。

4. **定向回归与全量**
   - `tests/test_agent_worker_integration.py`：13 passed（含并发领取、租约接管、账本恢复、取消、显式恢复、启动失败、API 重启）。
   - `tests/test_run_coordinator_integration.py` + `tests/test_agent_harness_control_plane.py`：17 passed。
   - `tests/test_outbox_service.py`：26 passed。
   - `pytest -m agent_e2e`：104 passed、4 skipped。
   - 离线全量分段执行：前 70% 无失败；`test_outbox_service.py` 及其后 409 项全通过。期间唯一失败是 Outbox 队列被其他 Agent 测试残留事件污染（ISS-20261020-006），已用测试隔离修复，未改动生产逻辑。
   - 未在修复后再次单次跑完整 1403 项；上述分段结果不合并虚报为单次全绿。

## Rollout / Rollback

- 默认 `AGENT_EXECUTION_MODE=inline`，部署验收时显式设为 `worker`；同一 job 不会双执行。
- 回滚先停止 worker 领取，等待在途 attempt 排空/过期，再切回 inline；不直接删除 job/execution 记录。

## 2026-10-22 收尾修复

- Outbox 集成模块只允许 `PG_DB=outbox_service_test`，在任何 DDL/连接/清理前检查；CI 新建专库单独执行。共享开发库 `sra` 不运行清理。
- SIGTERM/SIGINT 后停止领取，最长 30 秒排空。真实子进程分别覆盖正常完成与不可取消的同步线程；后者由 CLI 截止计时器结束进程，未完成 attempt 保留租约到期恢复，不伪造 FAILED。
- 订阅看到终态/暂停后重新读尾部事件，覆盖事件查询与状态查询间的提交竞态。
- 自动接管验证冻结输入与账本，移除错误的 Supervisor PAUSED 绑定；显式恢复的暂停证明以当前 fencing permit 原子消费一次，后续 checkpoint 前进不阻塞再次接管。
- 失败终态提交被拒或提交结果不确定时保留可接管 job，删除只更新队列终态的旁路函数。
- 新增真实 LangGraph saver + PostgreSQL 用例：暂停、恢复、检查点前进、再崩溃接管；旧 permit 无法消费证明或覆盖结果。

### 运行与验证命令

```bash
# 从 backend 目录启动；沿用 .env 的服务器隧道连接。
AGENT_EXECUTION_MODE=worker .venv/bin/python -m app.workers.agent_worker --worker-id agent-1
# API 另行以 AGENT_EXECUTION_MODE=worker 启动。
PG_DB=run_lifecycle_test MONGO_DB=run_lifecycle_test .venv/bin/python -m pytest tests/test_agent_worker_integration.py tests/test_agent_worker_shutdown.py tests/test_agent_worker_streaming.py tests/test_outbox_database_safety.py -q
PG_DB=outbox_service_test MONGO_DB=outbox_service_test .venv/bin/python -m pytest tests/test_outbox_service.py -q
PG_DB=assessment_stage2_test MONGO_DB=assessment_stage2_test .venv/bin/python -m pytest -m 'not integration' -q
```

同一测试库的门禁串行执行，禁止另起同库 DDL/清理进程；不同隔离库可独立执行。本次未变更前端。


### 最终门禁（2026-10-22）

- worker 集成、信号退出、SSE 竞态、数据库清理保护联合专项：26 passed（其中真实 PostgreSQL worker 用例 16 项）。
- 新增自定义租约回归后，退出模块单独复跑：5 passed；job 与 execution 使用同一续租时长。
- Outbox：服务器 `outbox_service_test` 专库 26 passed；DDL/清理均受库名保护。
- 独立审查的 3 个 Important 已修复并完成定向验证。
- `compileall` 与 `git diff --check` 通过；未运行前端门禁（本次未修改前端）。
- 最终完整离线：1105 passed、1 skipped、71 deselected（集成用例通过独立专库门禁执行）；该次运行在最后一次租约修复后完成。

- 既有 RunCoordinator 真实并发/取消/回放/投影及控制面回归：17 passed（服务器 run_lifecycle_test；Mongo 独立测试范围），保留原有测试。
