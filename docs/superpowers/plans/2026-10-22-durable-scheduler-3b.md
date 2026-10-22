# 第三阶段 3B：持久调度与执行门禁 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans，按下列任务逐项执行。用户已授权 3A 提交后直接开始 3B，沿用当前分支原地实施，不重复请求批准。

**Goal:** 将计划触发与业务执行分离，用可核对周期键抵御多 scheduler、漏跑、时区和进程故障；付费执行统一当前权限、预算预留和审计。

**Architecture:** PostgreSQL 保存版本化计划和独立 scheduled_execution_jobs。独立 scheduler 只计算最近到期周期并入队，唯一键为 schedule_id + revision + target_id + logical_period。后续独立消费者按任务种类调用现有框架无关 service；完成执行门禁后才切换 API 内旧 scheduler。

**Tech Stack:** 现有 APScheduler CronTrigger、zoneinfo、psycopg2、pytest；不新增依赖。

**Commits:** `c0f02777`（持久计划首包）、`46cd6b2b`（执行门禁与角色）。

**Spec:** docs/superpowers/specs/2026-10-19-durable-harness-evolution-design.md §6.1、6.3、6.4；3A 基线 `6f51036b`。

## Global Constraints

- 默认错过周期只补最近一次；历史批量补发需要单独授权和预算。
- 唯一触发键 schedule_id + schedule_revision + target_id + logical_period；计划时区计算，保存 UTC 边界；DST 重复小时不重复逻辑周期，不执行不存在的本地时间。
- Scheduler 只提交任务，不直接抓取、评分、通知；正确性依赖数据库唯一约束。
- 付费任务携带操作者/明确 service principal、范围、预算预留及必需审计，执行前重新核验当前权限；门禁未完成前不开放付费执行。
- 新功能沿用 Harness/领域分层；service 不引入 LangGraph；不改已有 REST/SSE 契约，不删除测试。
- 日常沿用服务器隧道；本包写测试仅 scheduler_stage3_test，fixture 仅清理自己的 UUID 计划和任务。
- 3B 首包只完成 Task 1–2，不打开新业务消费者；Task 3–4 完成后才切换默认调度角色。3C delivery/3D 迁移发布另行实施。

## Review Focus

1. DST 秋季重复小时：两次 UTC 触发只生成一个本地逻辑周期任务（Task 1）。
2. DST 春季跳时：不存在的本地时间跳过，不能偷偷改为下一小时执行（Task 1）。
3. 长时间停机与高频 cron：只补最近一次，周期查找不能从安装以来逐分钟遍历（Task 1）。
4. 定义变更/旧 scheduler 迟到：同 revision 内容不可变，旧 revision 不能覆盖新定义（Task 1）。
5. 停用/撤权、预算不足、未知外呼结果：外呼前拒绝、审计；未知结果不自动重复调用（Task 3）。

### Task 1: 周期定义与唯一任务账本

**Files:** 新增 app/domains/scheduling/{__init__,periods,repo}.py；修改 app/db/init_pg.py；新增 tests/test_scheduler_periods.py、tests/test_durable_scheduler_integration.py。

**Interfaces:** `ScheduleDefinition`（schedule_id/revision/target_id/cron/timezone/actor_kind/actor_id/kind/scope/effective_at）；`latest_due_period(definition, now) -> LogicalPeriod | None`；`register_schedule(definition) -> dict`；`enqueue_due_schedule(schedule_id, target_id, now=None) -> dict | None`。

- [x] 写失败测试：上海每日 UTC 边界；停机多日只取最近一次；无到期返回 None；DST 重复/跳时；长期高频不逐周期回放；非法时区/无时区时间/revision 拒绝。
- [x] 观察 RED：`.venv/bin/python -m pytest tests/test_scheduler_periods.py -q`。
- [x] 实现纯周期计算：复用 CronTrigger，向过去指数扩大搜索窗找到最近一次；用本地无偏移分钟作为逻辑键，重复小时规范为第一次 UTC 起点，结束为下一个不同逻辑周期。跳过不能 UTC 往返还原的本地时间。
- [x] 写真实 PG 失败测试：并发只产生一个任务并返回同一 ID；定义改变需递增 revision；不同目标/版本各自任务；回滚不留半条任务；付费 kind 固定 BLOCKED_POLICY。
- [x] 观察 RED；增加 durable_schedules、scheduled_execution_jobs DDL 与四字段唯一键。定义同 revision 不可变，旧 revision 不覆盖新定义；任务保存完整定义快照。
- [x] 运行纯单元与专库门禁 GREEN，登记验证结果。
- [x] 与 Task 2 一起提交首包。

### Task 2: 独立只入队 scheduler

**Files:** 新增 app/workers/scheduler.py；修改 CI 与本计划。

**Interfaces:** `run_scheduler_once(batch_size=20) -> dict`；`python -m app.workers.scheduler [--once] [--poll-seconds 5]`。

- [x] 写失败测试：只调用周期入队函数，不调用任何业务抓取/评分/通知；坏计划不阻塞其他计划；SIGTERM/SIGINT 停止下一轮领取。
- [x] 实现从 PG 时间计算、轮询持久计划的入口。用 last_checked_at 轮转批次避免固定前 N 条饿死后续计划；每计划独立事务；不在启动时执行 DDL。
- [x] 专库验证重启读取原计划、重复执行返回既有周期任务；默认不注册任何业务计划，不启动付费调用。
- [x] CI 建立 scheduler_stage3_test 专库独立门禁；复跑旧 scheduler 单元回归与最终离线门禁。
- [x] 独立审查首包，修复并记录后提交。

### Task 3: 执行权限、预算预留、审计与业务消费者

**Files:** app/domains/scheduling/executor.py、app/workers/scheduled_worker.py；适配 core/paid_quota.py 与既有任务 service；新增执行边界测试。

**Interfaces:** 下一包实现前依据 Task 1 已落地字段细化，必须包含 permit/fencing、当前 actor/目标授权核验、按 job 幂等预算预留、业务幂等结果/审计事务。service principal 必须明确配置权限和预算，不能以字符串回退绕过。

- [x] 写失败测试：停用/撤权/额度不足均零外呼；重试复用预算预留；每个目标隔离；外部未知结果转待对账。
- [x] 实现免费/付费任务白名单及有限并发消费者，保留领域 service 框架无关；通知迁移与 3C 边界对齐。
- [x] 双 worker、SIGKILL 接管、额度/审计原子性验证；付费 BLOCKED_POLICY 只在统一门禁通过后解除。
- [x] 独立审查、验证、提交。

### Task 4: 角色切换与验收

**Files:** services/scheduler.py、main.py、部署配置与回滚手册；新增进程验收。

- [x] 增加统一调度角色开关，API 不再执行已迁移的业务 cron；Outbox 在 3C 切出之前保持明确单一 owner。
- [x] 双 scheduler 同周期唯一任务、停机只补最近周期、权限撤销不外呼、时区边界、旧入口失权和 SIGKILL 接管门禁。
- [x] 更新总设计状态和 issue-log；3B 完整验收后再进入 3C。

## 首包实施记录（2026-10-22）

- 周期语义：本地无偏移分钟为逻辑键；秋季重复小时取第一次 UTC 起点、以下一不同逻辑周期为结束；春季不存在的本地时间跳过。向后指数扩窗，分钟级 cron 停机多年也只需少量查询。
- 持久计划：durable_schedules 以 (schedule_id, revision, target_id) 为主键，revision 内容不可变；scheduled_execution_jobs 以 (schedule_id, revision, target_id, logical_period) 唯一。
- 付费/外部副作用任务先落 BLOCKED_POLICY 记录：full_refresh、sentiment_check、proactive_agent、daily_digest、alert_notify、feishu_supplier_sync。当前只有 financial_check 记为 QUEUED，但尚无消费者，不会执行。
- 时间源：入队时间读取 PostgreSQL clock_timestamp；测试可显式传入 now。
- 坏计划隔离：失败扫描单独推进 last_checked_at，避免永久坏计划占住轮转队首。
- 门禁：周期 15、worker 7、真实 PG 8 = 30 passed（scheduler_stage3_test）。CI 增加独立专库。
- 批处理异常只记日志并在下一轮重试，不让一次数据库瞬时故障永久杀死调度角色；未从失败批次推断任何任务状态。

### 尚未完成（不宣称 3B 完成）

- 权限/预算/审计执行门禁与业务消费者（Task 3）。
- API 内旧 scheduler 的切换/撤权（Task 4）；当前旧 APScheduler 仍在其既有入口运行，本首包未改变生产触发。

## 执行门禁与消费者实施记录（2026-10-22）

- **租约**：scheduled_execution_jobs 支持 LEASED、attempts/max_attempts、fencing_token、lease_owner/lease_until、心跳与到期接管。只有当前 owner + fencing 能提交终态；旧 owner 的完成/心跳被拒。
- **权限**：用户型 actor 执行前重新读取 is_active/role，并通过 get_watchlist_targets 重新核验目标可见性；撤权、停用、目标移除均零外呼，终态 CANCELLED 且写审计。服务型 actor 必须出现在 SCHEDULER_SERVICE_PRINCIPALS，空配置即拒绝。
- **预算**：付费/外部副作用任务需要 SCHEDULER_PAID_EXECUTION_ENABLED=true；reserve_paid_refresh_once 以 job id 幂等预留，重试不重复扣费，预留 key 保留 7 天以跨日重试。额度不足/不可用均不进入 handler。
- **未知外呼结果**：provider 调用前写 provider_started_at；租约过期后若已越过该边界，任务直接 NEEDS_REVIEW(external_result_unknown)，不自动重放。成功提交被 fencing 拒绝时同样报告待核对。
- **审计**：终态与 audit_logs 同事务；用户删除后不再写失效 user_id，actor 保留在 details。
- **运行角色**：SCHEDULER_ROLE_ENABLED 控制 API 内业务 cron；独立 scheduler 入口在开关仍为 true 时拒绝启动，避免双触发。OUTBOX_WORKER_ENABLED 保持原投递回滚语义，Outbox 仍由 advisory leader 单一 owner 轮询，3C 再迁出。
- **运维入口**：`python -m app.domains.scheduling.cli register|list` 注册不可变 revision、检查计划；Compose 提供 `dedicated-scheduler` profile 下的 scheduler 与 scheduled-worker。

### 门禁结果

- 调度专项（周期、worker、角色开关、执行门禁、真实 PG 计划/任务、CLI）：59 passed。
- 既有 scheduler 生命周期回归：5 passed。
- 全量离线：1148 passed、1 skipped、87 deselected（最后代码变更后复跑）。
- 真实进程验收（scheduler_stage3_test）：
  - 两个 `scheduler --once` 并发：一个 created=1，一个 existing=1，数据库仅 1 条同周期任务。
  - 两个 claim 进程并发：一个取得 job，另一个 NONE。
  - claim 后 `kill -9`、租约过期：新进程接管 attempts 1→2、fencing 1→2。
  - 不存在/已删除用户的任务：handler 未调用，状态 CANCELLED(actor_inactive)，审计含 job/schedule/target/actor。
  - 付费任务在开关关闭时保持 BLOCKED_POLICY，worker 不领取。

### 尚未完成（不宣称 3B 完成）

- 现有生产 watchlist 与旧 cron 的计划注册/迁移尚未执行；没有自动 bootstrap，避免在未确认 actor 归属时批量改写生产计划。
- 付费外呼默认关闭，尚未做真实供应商付费 pilot。
- 计划管理目前是 CLI，无 REST 管理端点。
- 生产 API 角色开关未切换；Compose profile 默认不启动。
- 3C delivery、3D 迁移/发布门禁仍未开始。
