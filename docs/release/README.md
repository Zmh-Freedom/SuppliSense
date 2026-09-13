# SuppliSense 发布验收清单

本目录描述 Sourcing Risk Agent V2 的上线门槛。发布前必须执行：

```bash
cd /Users/zhouminhao/Projects/work/AI_team/SuppliSense/.worktrees/sourcing-risk-agent-v2
cd backend && python scripts/release_verify.py --skip-commands
```

生产候选版本在具备依赖环境后执行完整命令：

```bash
cd backend && python scripts/release_verify.py
cd ../frontend && npm run lint && npm run build && npm test -- --run
cd .. && docker compose --env-file .env.docker config --quiet
```

首次在本地执行 Compose 校验时，先复制 `.env.docker.example` 为 `.env.docker`，并将所有 `CHANGE_ME` 替换为本机生成的随机值或实际环境凭据。`.env.docker` 已被 `.gitignore` 忽略，禁止提交到版本库；只提交配置模板，不提交密钥。

CLI 输出 JSON。`PASS` 表示门禁已验证，`FAIL` 表示代码/配置门禁失败，`BLOCKED` 表示外部环境没有执行或不可达。只要存在 `FAIL` 或 `BLOCKED`，`ready_for_release` 就是 `false`，退出码为 2。特别是 `migration_schema` 的 `BLOCKED` 不能被解释为 PostgreSQL 或 MongoDB 已通过集成验证。

## 上线前门槛

- migration/schema：在可访问的 PostgreSQL 和 MongoDB 环境执行真实 schema/index/migration 测试，并保存输出。
- API auth：执行 `test_auth.py::test_protected_endpoints_require_auth` 与 `test_agent_run_api.py::test_agent_run_endpoints_require_authentication`。
- 人工审批与 Outbox：执行未审批 proposal 与 approved-only transactional Outbox 的对应 pytest；失败不得被源码契约检查掩盖。
- recovery：执行 evidence service 与 agent run service 的 unknown/fail-closed pytest；PG/Mongo 不可用时立即停止后续后端 pytest 集成门禁。
- rollout：执行 Shadow action boundary 与 rollout eval pytest；`AGENT_RUN_V2_ENABLED=false` 默认关闭，Shadow 只读，不允许领域写入。
- SSE replay：执行 durable event replay 与 terminal replay pytest，不能重复 action effect。
- frontend：分别执行 `npm run lint`、`npm run build`、`npm test -- --run`，每项独立报告。
- Compose：`docker compose --env-file .env.docker config --quiet` 成功；`.env.docker` 必须替换所有 `CHANGE_ME` 占位符。

`migration_schema` 的 V2 测试集合覆盖 agent run models/repository/checkpointer、policy、candidate discovery、evidence 与 approval action；它不是单一 company schema 测试。每个后端安全门禁都执行真实 pytest 命令，pytest 非零退出为 `FAIL`，命令或依赖不可用为 `BLOCKED`。

## Shadow rollout

1. 初始设置 `AGENT_RUN_V2_ENABLED=true`、`AGENT_RUN_V2_ROLLOUT=shadow`，并确认 durable control state 为 `active/shadow`。
2. 只观察 trace、SSE、recovery 和 metrics；Shadow 不得执行领域写入、外部导入或 approved action。
3. 达到 Task 14 的 sample/metric/approval 门槛后，管理员通过 `/api/v1/admin/agent-run-rollout/promote` 推进到 `internal`，再到 `canary`/`default`。
4. 每次推进都要保存 observation window、指标快照和人工 approval record；没有 approval record 不得 promotion。

## 证据与阻塞

发布记录至少包含 checklist JSON、前端 build 日志、Compose config 输出、migration/schema 输出、rollout state、metrics 窗口和审批记录。若 PG/Mongo/Docker 不可用，保留 `BLOCKED` 原文，转交有依赖访问权限的环境重跑，不通过跳过或手工改写结果放行。

回滚步骤见 [rollback-runbook.md](rollback-runbook.md)。
