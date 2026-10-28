# SuppliSense 3D 隔离预生产就绪记录

日期：2026-10-28<br>
结论：**隔离开发/预生产演练就绪；未切生产角色或生产开关**<br>
目标版本：PR #2 合并基线 `dac71829a6a8d1385d4c60868f69793a81501b83`。

## 1. PR #2 收尾

PR #2 已完成差异审查和必需 CI/审查门禁，并合并至 `master`，本分支以合并提交为基线。工作区原有未跟踪的运营风险设计稿和 `scripts/db-tunnel.sh` 保留在原 checkout，未混入 PR 或本分支。

## 2. 3D 实现范围

- `backend/app/db/worker_migrations.py` 提供 `migrate`、`status`、`check`：PostgreSQL 与 MongoDB 使用独立 migration 记录、版本和 checksum；重复执行幂等；拒绝未知更高版本；PG/Mongo 任一缺失或不匹配时 worker readiness 失败。迁移目标只允许 `_test`/`_preprod` 数据库，明确拒绝 `sra`、`tianyancha` 和 `postgres`。
- PostgreSQL 迁移纳入 Agent LangGraph checkpointer schema；迁移时显式执行 setup。Agent Worker 改为连接已建 schema，不在 claim 后临时创建 DDL。MongoDB 索引创建使用严格失败策略。
- Agent Worker、Scheduler、Scheduled Worker 与 Delivery 启动前读取同一版本状态。新增 `backend/app/workers/probe.py` 作为容器内部依赖探针（迁移状态 + Redis PING），没有新增公网 HTTP API；`/health/ready` 原有响应结构保留。
- Compose 增加一次性迁移 profile 和独立 `dedicated-agent`、`dedicated-scheduler`、`dedicated-delivery` 角色配置。所有 profile 默认不启动；各角色设置单独连接池上限和内部探针。
- 逐类交接只允许经验证的 `financial_check`：API 保留未迁移的免费 cron，迁移任务由 durable scheduler 执行；legacy APScheduler 与 durable worker 都尊重付费执行开关，`full_refresh` 不迁移且开关关闭时不注册。错误配置在 role startup 失败关闭。
- Delivery profile 强制 `OUTBOX_DELIVERY_SINK=test`，测试 sink 只写隔离 MongoDB 的去标识回执，不调用真实飞书提供方。API 兼容发送角色没有切换。

## 3. 连接预算

按 API 4 个 Gunicorn 进程、每种独立 worker 一个实例、迁移进程不与 API 同时放大部署估算：

| PostgreSQL 角色 | 预算算法 | 最大连接 |
| --- | ---: | ---: |
| API 业务池 | 4 × 15 | 60 |
| API checkpointer | 4 × 1 | 4 |
| API readiness 探针 | 4 × 1 | 4 |
| Agent Worker | 5 个池 + 1 个 checkpointer | 6 |
| Scheduler | 1 × 2 | 2 |
| Scheduled Worker | 1 × 5 | 5 |
| Delivery | 1 × 5 | 5 |
| 一次性迁移 | 2 个池 + 1 个 checkpointer | 3 |
| **合计** |  | **89** |

远端 PostgreSQL 只读检查显示 `max_connections=100`；按 3 个保留连接，估算可用 97，模型余量 8。扩展 API/worker 副本数或连接池前必须重新核算。

MongoDB 按当前配置上限估算：API 同步/异步池 `4 × (25 + 25)`、API 临时 health probe `4 × 1`、Agent Worker 20、Scheduler 5、Scheduled Worker 20、Delivery 20、一次性迁移 2，合计上限 271。只读 `serverStatus` 快照为 `current=3`、`available=406`。快照是演练时点数据；副本扩容后需重新估算。

## 4. 隔离环境演练

演练只使用隔离 PostgreSQL/MongoDB `worker_3d_20261028_test` 和专用 Redis 实时频道 `codex:3d:worker-preprod:20261028`。共享开发数据库 `sra`、`tianyancha` 没有用于测试写入；Redis 只执行连通性检查或向该独立频道验证，不调用真实通知提供方。

| 演练 | 结果 |
| --- | --- |
| Compose 静态展开 | `docker compose --env-file .env.docker.example config --quiet` 通过；未启动容器或触碰共享卷 |
| 版本迁移 | 隔离 PG/Mongo 初始缺版本 → migrate → status ready → 再次 migrate/status ready；重复迁移保持版本和 checksum 一致 |
| 失败关闭 | 分别移除隔离 Mongo migration 标记、PG migration 标记；Agent Worker 与 Scheduled Worker 均在 claim 前以非零退出；重新 migrate 后两个标记恢复 |
| worker role `--once` | 完整离线集后再检查隔离库无待执行 Agent job、Scheduler plan 全 disabled、Scheduled Worker job 全 SUCCEEDED、Delivery 队列为空；migration status、probe、Agent Worker、Scheduler、Scheduled Worker、Delivery 均退出 0，执行数为 0 |
| lease 接管 | 对一个隔离 `financial_check` job 在 handler 边界 SIGKILL lease holder；接管者完成任务，最终状态 `SUCCEEDED`、attempt/fence 各递增一次、仅一条 terminal audit |
| backlog | 三条隔离任务由本地 handler drain；UUID-safe 复核显示三条均成功，相关 drill schedule 均 disabled |
| 通知 | delivery test sink 写入隔离回执；`provider_calls=0`，回执未包含 Open ID |
| 回滚 | `SCHEDULER_MIGRATED_KINDS` 清空时，API 重新包含 `financial_check`；配置 `financial_check` 时 API 跳过它。付费开关关闭时 `full_refresh` 在旧/新 scheduler 均保持关闭，其他未迁移免费 cron 保留 |

早期 drill 脚本曾有输出格式错误和 UUID/text 复核查询类型错误，已恢复 migration 标记，并用独立查询核对 backlog 最终状态。详见问题日志 ISS-20261028-008。

## 5. 自动化验证

- worker migration、角色门禁、通知 test sink、Agent checkpointer、调度、Delivery 和 `/health/ready` 定向组合：**86 passed**。
- 原 PostgreSQL 连接失败用例在隔离数据库下定向重跑：**52 passed**。
- Redis 环境频道覆盖独立进程回归：**1 passed**；新频道同时出现在 `settings.REALTIME_CHANNEL` 和 `realtime.CHANNEL`。
- 完整后端离线套件在隔离 PG/Mongo 及专用 Redis 频道下：**1432 passed、1 skipped、232 deselected**。该集合不包含 `integration` 与 `agent_e2e` 标记；未运行真实外部 provider。
- 最终隔离 CLI：migration status 与内部 probe 均 `ready=true`；Agent Worker `executed=0`，Scheduler `scanned=0/created=0`，Scheduled Worker `executed=0`，Delivery `claimed=0/published=0/failed=0`，均 exit 0。

## 6. 保留边界

- 本结果只证明隔离环境迁移、role gate、故障接管、积压、回滚和测试发送端具备演练条件，不代表生产环境变更批准或生产就绪。
- 未启动生产容器、未切生产 API 角色、未打开付费执行、未迁移 `full_refresh`，未向供应商或采购员发送真实通知。
- 真实 SCM/PWMS/TMS 数据缺失与试点对象未选另见[真实数据核验与试点评审稿](2026-10-28-operational-risk-data-validation-and-pilot-plan.md)。
