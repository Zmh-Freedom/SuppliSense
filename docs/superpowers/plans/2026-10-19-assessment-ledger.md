# 第二阶段：稳定评估记录 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 任何新 assessment ID 均可核对对象、操作者范围、冻结证据、政策、执行来源及唯一业务结果；恢复与投影重试不生成重复评估。

**Architecture:** 扩展既有 `assessment_history`，PG 保存权威评估和不可变政策/清单，沿用现有 raw-payload staging/compensation 和 Outbox。框架无关的 assessment 应用边界接入旧风险端点、Harness 工具与监控快照；新增 v2 查询提供完整证据与复核记录，原有成功响应模型保持。

**Tech Stack:** 现有 FastAPI、Pydantic、psycopg2、PyMongo、pytest；不新增依赖。

**Spec:** `docs/superpowers/specs/2026-10-19-durable-harness-evolution-design.md` 第 5 节。

## Global Constraints

- 用户已验收三阶段设计，并于 2026-10-19 明确授权第二阶段开发；延续第一阶段的 inline 执行，不再重复请求开工确认。
- 使用当前 `codex/2026-09-23` 开发分支，保留未跟踪的用户文档和隧道脚本。
- 使用服务器 SSH 隧道：PG 15432、Mongo 27018、Redis 16379；集成测试隔离于 `assessment_stage2_test`，禁止清空共享开发库。
- 服务层不引入 LangGraph，敏感操作仍校验当前用户；沿用共享供应商引用收集器。
- 沿用高分更安全方向，缺失政策变化采用新政策版本；缺少最低证据时不返回伪造整数成功分数。
- 事实、证据时间与投影时间分开，已有历史 UUID 不改写，不能证明的历史标记 legacy_unverified。
- 不进行第三阶段 worker/scheduler/delivery 角色拆分，不修与本阶段无关的业务功能。

## Review Focus

1. 同 Run 两个对象、同名不同 company_id、相同企业不同采购/操作者范围必须隔离（Task 1/4）。
2. 政策或来源数据在恢复期间改变，不得替换已冻结输入；幂等键参数变化报冲突（Task 1/2/3）。
3. 结果 PG 提交后 Mongo/HTTP 失败，重试返回原 ID/时间/版本；旧 attempt 不得提交（Task 1/3）。
4. 成功空查询必须有回执；提供方异常、未查询和过期不贡献安全分，单项覆盖率仅按请求维度计算（Task 2）。
5. 财务刷新不得复制旧总分创建新综合评估；迟到投影、跨政策趋势、旧名称歧义不得污染当前结果（Task 3/4）。

## 文件边界

- `domains/risk/assessment_contracts.py`：身份、执行上下文、不可变 JSON 哈希与证据状态。
- `domains/risk/assessment_policy.py`：可冻结政策、缺失/新鲜度/方向/阈值与实现版本。
- `db/assessment_schema.py`：追加式 PG schema，历史保留默认标记；由 init_pg 调用。
- `domains/risk/assessment_repo.py`：开始/冻结/结果/序列/查询/复核及 staging 对账事务。
- `domains/risk/assessment_evidence.py`：原始证据采集与状态、staging、内容哈希验证。
- `domains/risk/assessment_service.py`：跨入口评估应用边界与旧响应投影。
- `domains/risk/assessment_projection.py`：按 assessment ID 幂等生成 Mongo 展示与完成原始载荷提交。
- `domains/risk/assessment_api.py`、`assessment_migration.py`：独立 v2 查询/复核契约与只读历史迁移报告。
- 现有 `risk/service.py`、`soft_risk.py`、`repo_assessment.py`、`api_risk.py`、工具、监控/财务刷新、趋势和 main/outbox 做必要适配。

## Task 1：稳定身份、PG 合约与政策冻结（2A）

- [x] 先登记 002/006/016/017/018 续作和新增稳定身份缺口。
- [x] 测试：幂等冲突、同 Run 多对象/范围、政策不可变、历史 ID 保留、版本并发唯一、非所有者拒绝、旧 permit 拒绝；观察 RED。
- [x] 扩展 assessment_history，新增政策、清单、版本序列、复核、载荷 staging 对账；唯一 Run+logical_key 和 actor_scope+request_key。
- [x] begin 分配稳定 ID/版本并冻结政策；freeze 保存不可变输入摘要与清单；已冻结数据不覆盖。
- [x] 在服务器隔离测试库完成真实事务/并发验证；关联问题记录，提交。

## Task 2：冻结证据与真实可用性计算（2B）

- [ ] 测试：soft LLM 失败/缺失维度、查询失败不能当 empty、单项覆盖率、冻结后来源/政策变化、完全无证据时旧端点明确失败；观察 RED。
- [ ] 收集请求范围内的原始材料、读取回执与来源时间，内容寻址且带 assessment 所有权；复用 staging/compensation。
- [ ] v3 风险扣分算法消费冻结 request、recent_lawsuits 和 soft 结果，政策控制权重/归一化/阈值/缺失与等级，不在计算中再次查询外部数据。
- [ ] 缺失维度不计作安全满分；有效硬证据不足为 unavailable，其他结果明确 complete/partial。
- [ ] 新政策版本隔离旧评分；完成纯离线回归，提交。

## Task 3：跨入口结果与 Outbox（2C）

- [ ] 测试：PG 回滚无结果/Outbox；PG 成功 Mongo 失败可重放；同 ID 一条快照、版本/前驱唯一、已完成 Run 工具恢复复用业务回执；观察 RED。
- [ ] API/工具/监控写入口建立同一操作者和逻辑评估键，开始后再采集；Harness 透传稳定 task_id，并将实际 permit 传播到同步工具事务。
- [ ] freeze 后计算结果、任务业务关联、Outbox 在同一 PG 事务；Run 最终事件继续通过阶段 1 的 Coordinator 统一提交。
- [ ] Mongo 按 assessment ID upsert；PG 结果与展示同步状态分开，原始载荷生命周期可重放/补偿。
- [ ] 财务更新只保存独立财务证据，保留原综合评估 ID、分数和计算时间。
- [ ] 真实 PG/Mongo 故障窗口与当前 Harness/审批回归通过，提交。

## Task 4：查询、趋势、复核、迁移及门禁（2D）

- [ ] 测试：按 ID 当前授权、同名/范围隔离、跨政策分段、复核保留原分数、无歧义旧记录回退、旧响应结构；观察 RED。
- [ ] 新增 `/api/v2/assessments` 身份/证据/政策/来源查询和追加复核；旧端点保持返回结构。
- [ ] 监控历史只回退缺失稳定 ID 且无歧义记录；趋势默认同对象/范围/政策，v2 显式分段。
- [ ] 生成服务器开发库只读历史迁移报告；仅可证实字段回填，不伪造历史证据与哈希。
- [ ] CI 增加独立真实库门禁；离线 Agent E2E、后端定向、前端契约回归通过。
- [ ] 按 executing-plans 要求完成一次 fresh-context 全分支审查，修复重要发现，记录部署/验证与提交。

## 执行裁定与进度

- 沿用已验收总设计；本计划细化第二阶段的实现位置与验证，不改变既定产品范围。
- Task 1 已完成身份/政策/冻结与事务基础。服务器隔离库首轮 12 passed（9 个真实 PG 用例）；追加结果/Outbox 原子性回归随本批验证。Task 2–4 持续推进，不将基础提交声明为第二阶段完成。
- Task 1 最终基础门禁：`PG_DB=assessment_stage2_test MONGO_DB=assessment_stage2_test ... pytest tests/test_assessment_ledger.py tests/test_assessment_ledger_integration.py`，14 passed；包含结果/Outbox 原子提交、回滚及不可覆盖。测试库位于已约定服务器。
