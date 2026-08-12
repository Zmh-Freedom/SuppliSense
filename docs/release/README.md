# SuppliSense 发布验收清单

本目录描述 Sourcing Risk Agent V2 的上线门槛。发布前必须执行：

```bash
cd /Users/zhouminhao/Projects/work/AI_team/SuppliSense/.worktrees/sourcing-risk-agent-v2
cd backend && python scripts/release_verify.py --skip-commands
```

生产候选版本在具备依赖环境后执行完整命令：

```bash
cd backend && python scripts/release_verify.py
cd ../frontend && npm run build
cd .. && docker compose --env-file .env.docker config --quiet
```

CLI 输出 JSON。`PASS` 表示门禁已验证，`FAIL` 表示代码/配置门禁失败，`BLOCKED` 表示外部环境没有执行或不可达。只要存在 `FAIL` 或 `BLOCKED`，`ready_for_release` 就是 `false`，退出码为 2。特别是 `migration_schema` 的 `BLOCKED` 不能被解释为 PostgreSQL、pgvector 或 MongoDB 已通过集成验证。

## 上线前门槛

- migration/schema：在可访问的 PostgreSQL（含 pgvector）和 MongoDB 环境执行真实 schema/index/migration 测试，并保存输出。
- API auth：匿名请求必须被保护端点拒绝；管理员操作必须验证角色。
- 人工审批与 Outbox：未审批只能持久化 proposal，不得写主数据或 Outbox；只有 `approved` proposal 才允许一次性事务 Outbox，重复 replay 必须幂等拒绝。
- recovery：Mongo evidence 缺失、非法 lifecycle、跨存储状态冲突均为 `unknown`，详情、决策和 SSE 必须 fail closed。
- rollout：`AGENT_RUN_V2_ENABLED=false` 默认关闭；Shadow 只读，不允许领域写入；控制面不可用或 rollback frozen 时拒绝 V2 执行。
- SSE replay：断线重连按 durable run/checkpoint 和 replay identity 恢复，不能重复 action effect。
- frontend build：`npm run build` 成功。
- Compose：`docker compose --env-file .env.docker config --quiet` 成功；`.env.docker` 必须替换所有 `CHANGE_ME` 占位符。

## Shadow rollout

1. 初始设置 `AGENT_RUN_V2_ENABLED=true`、`AGENT_RUN_V2_ROLLOUT=shadow`，并确认 durable control state 为 `active/shadow`。
2. 只观察 trace、SSE、recovery 和 metrics；Shadow 不得执行领域写入、外部导入或 approved action。
3. 达到 Task 14 的 sample/metric/approval 门槛后，管理员通过 `/api/v1/admin/agent-run-rollout/promote` 推进到 `internal`，再到 `canary`/`default`。
4. 每次推进都要保存 observation window、指标快照和人工 approval record；没有 approval record 不得 promotion。

## 证据与阻塞

发布记录至少包含 checklist JSON、前端 build 日志、Compose config 输出、migration/schema 输出、rollout state、metrics 窗口和审批记录。若 PG/Mongo/Docker 不可用，保留 `BLOCKED` 原文，转交有依赖访问权限的环境重跑，不通过跳过或手工改写结果放行。

回滚步骤见 [rollback-runbook.md](rollback-runbook.md)。
