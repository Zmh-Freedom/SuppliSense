# Task 13 P1 修复报告

## 本次修复

- recovery/retry 对 Mongo 记录缺失、查询异常、非 pending 状态及 owner 不匹配统一返回 `unknown`，不再误报 `already_compensated`；service 保留或创建 durable recovery 状态，detail/decision 继续 fail-closed。
- 每次 orchestration snapshot attempt 使用新的 `staging_owner` token，stage/commit/compensate/retry 继续按 owner 做状态转换，避免并发 attempt 相互覆盖或删除。
- 修复 Mongo commit failure 回归测试的 `reason` keyword-only mock 契约，并保留重复 stage/commit 的幂等语义。

## 验证

```text
cd backend && pytest -q tests/test_sourcing_risk_evidence_service.py tests/test_agent_run_service.py
45 passed

cd backend && python -m compileall -q app
git diff --check
```

未修改 `task-13-review.md`。
