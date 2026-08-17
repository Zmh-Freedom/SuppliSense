# Task 14 Report

## 本轮定向修复验证

### Focused non-PG

命令：

```text
cd backend && pytest -q \
  tests/test_rollout_gate.py tests/test_sourcing_risk_evals.py \
  tests/test_release_verification.py tests/test_agent_run_checkpointer.py \
  tests/test_sourcing_risk_actions.py \
  tests/test_agent_run_repo.py::test_rollback_outbox_update_scopes_action_events_to_v2_runs \
  tests/test_agent_run_repo.py::test_rollback_outbox_update_preserves_legacy_action_events \
  -k 'not real_postgres'
```

结果：**64 passed, 1 deselected, 3 warnings**。

覆盖本轮修复：

- rollback Outbox SQL 含 `payload->>'run_id'` 与 `agent_runs.run_type = 'sourcing_risk_v2'` 条件，并保留 legacy action event 在更新范围外的回归测试。
- release verification 使用真实 `GraphTraceRecorder`，通过 production adapter 生成 trace artifact，再由 recorded artifact 进入 Eval gate 的端到端测试。

### PG/Outbox

命令：

```text
cd backend && pytest -q \
  tests/test_rollout_gate.py tests/test_sourcing_risk_evals.py \
  tests/test_release_verification.py tests/test_agent_run_repo.py \
  tests/test_agent_run_checkpointer.py tests/test_sourcing_risk_actions.py \
  tests/test_outbox_service.py -k 'not real_postgres'
```

结果：**80 passed, 19 failed, 4 deselected, 3 warnings**。

19 个失败均发生在真实 PostgreSQL/Outbox 路径，环境错误为连接 `localhost:5432` 被沙箱阻止：`Operation not permitted`。本轮未将这些环境阻塞计为实现断言失败，也未宣称真实 PG/Outbox 状态机已验证通过。

### 编译与 diff

```text
cd backend && python -m compileall -q app
exit 0

git diff --check
exit 0
```

真实 PG/Outbox 集成验证仍需在可访问 PostgreSQL 的环境执行。
