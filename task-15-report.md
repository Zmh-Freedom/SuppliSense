# Task 15 报告：发布验证与上线门槛

## 交付

- 新增 `backend/app/release_check.py` 和 `backend/scripts/release_verify.py`，提供可运行 JSON release checklist。
- 覆盖 migration/schema、API auth、人工审批与 approved-only Outbox、recovery fail-closed、rollout flags/Shadow no-write、SSE replay、frontend build、Compose config 八项门禁。
- checklist 明确区分 `PASS`、`FAIL`、`BLOCKED`；PG/Mongo 未连接或不可达时只输出环境阻塞，不宣称集成通过。
- 新增最小验收测试 `backend/tests/test_release_checklist.py`。
- 新增 [发布验收 README](docs/release/README.md) 和 [rollback runbook](docs/release/rollback-runbook.md)。
- 未修改任何 `*-review.md`。

## 验证

```text
cd backend && pytest -q tests/test_release_checklist.py
3 passed

cd backend && python scripts/release_verify.py --skip-commands
# 输出 migration_schema/frontend_build/compose_config 为 BLOCKED；退出码 2（环境未执行，不代表业务测试失败）

cd backend && python -m compileall -q app
git diff --check
```

真实 PostgreSQL/pgvector、MongoDB、Outbox 和 checkpoint 集成测试仍需在依赖可访问环境执行；本任务不将当前环境阻塞写成通过。
