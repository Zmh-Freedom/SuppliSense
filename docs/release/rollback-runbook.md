# Sourcing Risk Agent V2 回滚 Runbook

## 触发条件

任一条件触发冻结：unsafe action、duplicate action、recovery fail-closed 失败、SSE replay 重复副作用、Outbox backlog/lease 异常、关键指标低于当前 stage 门槛，或 PG/Mongo/Redis readiness 不稳定。

## 操作步骤

1. 记录时间、stage、告警、当前 rollout state、in-flight runs、pending proposals 和 leased outbox 数量。
2. 由管理员调用 rollback API（`stage` 必须是当前 stage，`reason` 至少 2 个字符）：

   ```bash
   curl -X POST "$BASE_URL/api/v1/admin/agent-run-rollout/rollback" \
     -H "Authorization: Bearer $ADMIN_TOKEN" \
     -H 'Content-Type: application/json' \
     -d '{"stage":"canary","reason":"recovery fail-closed regression","in_flight":{"runs":0,"pending_proposals":0,"leased_outbox":0}}'
   ```

3. 确认 durable control state 为 `rollback_frozen`；新 V2 start/resume、Shadow 路由和领域 action 都必须被拒绝或回到 legacy。
4. 停止新的 outbox lease，冻结 pending proposals，冻结 in-flight V2 runs；保留 runs、checkpoints、audit 和 recovery 记录，不删除数据。
5. 对已 lease 事件按 repository 的 V2 scope 处理；不得取消 legacy action events。
6. 保留失败 run、SSE trace、recovery 状态、Outbox payload 和 metrics 作为事故证据。`unknown`/`pending_compensation` 不得手工改成 `compensated`。
7. 修复并通过完整 release checklist；恢复前必须有新的 observation window 和管理员 approval record，不能直接切回 `default`。

## 数据库与集成限制

回滚门禁本身可以通过离线单测验证，但真实 PostgreSQL/pgvector、MongoDB、Outbox 和 checkpoint 集成必须在可访问依赖的环境重跑。若 checklist 输出 `BLOCKED`，状态是环境阻塞，不是成功，也不是失败已被证明；在阻塞解除前不得宣称上线验收完成。
