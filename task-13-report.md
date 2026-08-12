# Task 13 P1 修复报告

## Retry API cross-store P1 修复（2026-08-12）

- retry service 不再以历史 PG `compensated` 覆盖本次 Mongo `pending_compensation` 或 `unknown`；这些不安全结果会原样返回并调用持久 recovery 更新。
- 只有历史 PG 已 `compensated` 且本次 Mongo outcome 也明确为 `compensated` 时才走幂等短路；重复 retry 不会把 durable PG 状态降级。
- 新增 service/API 回归测试，覆盖 PG `compensated` 与 Mongo 不安全 outcome 的组合及 API 原样返回契约。

验证：

```text
cd backend && pytest -q tests/test_agent_run_service.py -k 'retry_after_compensation or repeated_retry_after_compensation or does_not_promote_unsafe_mongo_outcome_over_pg_compensated'
# 4 passed

cd backend && pytest -q tests/test_agent_run_api.py -k 'raw_payload_compensation_retry'
# 3 passed

cd backend && python -m compileall -q app
git diff --check
# 均 exit 0
```

未修改 `task-13-review.md`。

## 最后状态机修复（2026-08-12）

- detail/API 对 evidence 缺失 `raw_payload_ref`、Mongo 文档缺失及未知 lifecycle 生成稳定 `unknown` recovery 状态；detail、决策和 SSE 使用的候选评分统一 fail closed。
- compensation retry 状态单调化：已 `compensated` 的 recovery 记录不会被重复 retry 的 `unknown` 覆盖；service 返回保持 `compensated`，repository 更新也由 SQL 防止回退。
- 新增最小回归测试覆盖上述缺失/未知状态、fail-closed 与重复 retry 幂等行为。

验证：

```text
cd backend && pytest -q \
  tests/test_sourcing_risk_evidence_service.py::test_lifecycle_status_for_missing_mongo_document_is_unknown \
  tests/test_sourcing_risk_evidence_service.py::test_lifecycle_status_for_unknown_mongo_lifecycle_is_unknown \
  tests/test_agent_run_service.py::test_get_sourcing_risk_run_fails_closed_when_evidence_raw_payload_ref_is_missing \
  tests/test_agent_run_service.py::test_get_sourcing_risk_run_fails_closed_for_unknown_recovery_lifecycle \
  tests/test_agent_run_service.py::test_retry_after_compensation_keeps_compensated_state
# 5 passed

cd backend && python -m compileall -q app
git diff --check
# 均 exit 0
```

未修改 `task-13-review.md`。

## 最后非环境回归修复（2026-08-12）

- 保留 PostgreSQL recovery index 的已知 `pending_compensation`、`committed`、`compensated` 状态；只有 Mongo 缺失/非法 lifecycle 或真正未知的 PG 状态才规范化为 `unknown`，并继续由 detail/decision fail-closed。
- 详情仍为缺失 `raw_payload_ref` 生成 `missing:*` synthetic 状态以触发恢复门禁，但 retry 只收集真实 Mongo ref，不会把 synthetic ref 传给 Mongo 删除命令；授权 Run 和 durable compensation refs 约束保持不变。
- 补充 PG recovery lifecycle 保留、synthetic ref retry 过滤，以及新增 Mongo lifecycle 查询后的详情测试替身契约回归。

验证：

```text
cd backend && pytest -q tests/test_sourcing_risk_evidence_service.py tests/test_agent_run_service.py tests/test_agent_run_api.py tests/test_sourcing_risk_graph.py
# 85 passed

cd backend && python -m compileall -q app
git diff --check
```

`tests/test_agent_run_repo.py` 中需要真实 PostgreSQL 的集成测试仍受当前环境 `localhost:5432 Operation not permitted` 阻塞；未将该环境失败计入本次非 PG 回归结果。未修改 `task-13-review.md`。

## 本次修复

- 修复跨存储 compensation 合并：PG `compensated` 只有在 Mongo 同一 `raw_payload_ref` 明确为 `committed`/`compensated` 时才可合并为完成；Mongo `pending`、`pending_compensation`、`unknown` 或缺失时保留不安全状态并触发 fail-closed。
- detail 返回的候选/决策、API DTO 与 SSE durable detail 继续共享该门禁；补充反向组合矩阵、PG compensated + Mongo pending 的 detail/decision gate 回归，以及重复 retry 不降级回归。

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
