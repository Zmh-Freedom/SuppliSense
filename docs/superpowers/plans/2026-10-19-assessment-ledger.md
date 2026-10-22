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

- [x] 测试：soft LLM 失败/缺失维度、查询失败不能当 empty、单项覆盖率、冻结后来源/政策变化、完全无证据时旧端点明确失败；观察 RED。
- [x] 收集请求范围内的原始材料、读取回执与来源时间，内容寻址且带 assessment 所有权；复用 staging/compensation。
- [x] v3 风险扣分算法消费冻结 request、recent_lawsuits 和 soft 结果，政策控制权重/归一化/阈值/缺失与等级，不在计算中再次查询外部数据。
- [x] 缺失维度不计作安全满分；有效硬证据不足为 unavailable，其他结果明确 complete/partial。
- [x] 新政策版本隔离旧评分；完成纯离线回归，提交。

## Task 3：跨入口结果与 Outbox（2C）

- [x] 测试：PG 回滚无结果/Outbox；PG 成功 Mongo 失败可重放；同 ID 一条快照、版本/前驱唯一、已完成 Run 工具恢复复用业务回执；观察 RED。
- [x] API/工具/监控写入口建立同一操作者和逻辑评估键，开始后再采集；Harness 透传稳定 task_id，并将实际 permit 传播到同步工具事务。
- [x] freeze 后计算结果、任务业务关联、Outbox 在同一 PG 事务；Run 最终事件继续通过阶段 1 的 Coordinator 统一提交。
- [x] Mongo 按 assessment ID upsert；PG 结果与展示同步状态分开，原始载荷生命周期可重放/补偿。
- [x] 财务更新只保存独立财务证据，保留原综合评估 ID、分数和计算时间。
- [x] 真实 PG/Mongo 故障窗口与当前 Harness/审批回归通过，提交。

## Task 4：查询、趋势、复核、迁移及门禁（2D）

- [x] 测试：按 ID 当前授权、同名/范围隔离、跨政策分段、复核保留原分数、无歧义旧记录回退、旧响应结构；观察 RED。
- [x] 新增 `/api/v2/assessments` 身份/证据/政策/来源查询和追加复核；旧端点保持返回结构。
- [x] 监控历史只回退缺失稳定 ID 且无歧义记录；趋势默认同对象/范围/政策，v2 显式分段。
- [x] 生成服务器开发库只读历史迁移报告；仅可证实字段回填，不伪造历史证据与哈希。
- [x] CI 增加独立真实库门禁；离线 Agent E2E、后端定向、前端契约回归通过。
- [x] 按 executing-plans 要求完成一次 fresh-context 全分支审查，修复重要发现，记录部署/验证与提交。

## 执行裁定与进度

当前状态：第二阶段四项任务全部完成，自动化门禁与独立审查修复已通过，待用户验收后进入第三阶段。以下保留分批验证轨迹。

- 沿用已验收总设计；本计划细化第二阶段的实现位置与验证，不改变既定产品范围。
- Task 1 已完成身份/政策/冻结与事务基础。服务器隔离库首轮 12 passed（9 个真实 PG 用例）；追加结果/Outbox 原子性回归随本批验证。Task 2–4 持续推进，不将基础提交声明为第二阶段完成。
- Task 1 最终基础门禁：`PG_DB=assessment_stage2_test MONGO_DB=assessment_stage2_test ... pytest tests/test_assessment_ledger.py tests/test_assessment_ledger_integration.py`，14 passed；包含结果/Outbox 原子提交、回滚及不可覆盖。测试库位于已约定服务器。

- Task 2/3 已实现：冻结评分、真实数据状态、统一入口、任务业务回执和 Outbox、载荷补偿及财务独立证据。当前专用真实库 gate 39 passed（20 个 PG/Mongo 用例），追加缺失维度事实输出回归 20 passed（含既有算法）；最终全量门禁及一次 fresh-context 审查在执行中。
- Ruling: 同名主体无法通过名称缓存证明证据归属时拒绝采集，不伪造 company_id 关联；代价是需补齐稳定来源身份。
- Ruling: 未冻结且失联的采集保留原 ID 并转 needs_review，补偿登记载荷；新材料创建关联新评估。代价是一次显式重建，避免覆盖不可核对的中间状态。

## 第二阶段交付与操作说明

- 身份与政策基础提交 `c068bb41`；冻结计算、统一入口、查询/复核、补偿/投影及最终审查修复提交 `7986559d`。
- 第一阶段方案保持：API 进程内执行现有 Harness；独立 worker/scheduler/delivery 与进程退出接管属于第三阶段。
- 现有 `/api/v1/risk/assess` 成功模型及 SSE 事件不变；可传 `Idempotency-Key` 复用请求，缓存仅查当前 actor 的稳定对象/范围/政策。
- `POST /api/v2/assessments`：必填 company_name、company_id、request_key；可指定 dimensions、monitor_target_id、purchase_scope；重试沿用原 request_key，新材料显式换 key，并用 linked_assessment_id 关联原评估。
- `GET /api/v2/assessments/{id}`：身份、质量、原始结果、政策、清单、来源及复核；内部采集 token 和完整计算输入不对外返回。
- `GET /api/v2/assessments/company/{company_id}`：按范围/政策显式分段，每条记录重验当前对象权限。
- `POST /api/v2/assessments/{id}/reviews`：reason、decision（confirmed / needs_review / rejected）、request_key；追加复核及审计，不修改原始分数。
- 旧历史/状态/变更接口通过当前请求 actor 读取 PG 兼容快照；Mongo 展示投影失败可重放现有 Outbox 的 `risk.assessment.committed`。
- 财务自动检查保存 financial_evidence_snapshots 与独立告警回执；综合评估计算时间不随财务刷新推进。

### 开发库与历史

- 已通过约定服务器隧道向 sra 应用 37 条增量 DDL；未使用本地 Docker。自动化用 assessment_stage2_test、run_lifecycle_test 隔离验证。
- 只读报告：`docs/reports/2026-10-19-assessment-ledger-migration.json`。PG 原有 167 条为 legacy_unverified；Mongo 172 条无 assessment_id。保留 UUID、分数与原时间；没有可证实的完整证据/政策，不伪造回填。
- 重复生成只读报告：在 backend 运行 `.venv/bin/python -m app.domains.risk.assessment_migration`。

### 最终验收记录

| 门禁 | 结果 |
| --- | --- |
| 后端全量离线（not integration and not agent_e2e） | 994 passed，1 skipped |
| 评估专用真实 PG/Mongo + 合约 | 44 passed（其中真实集成 21 项） |
| 第一阶段取消/恢复/审批/终态回归 | 51 passed（服务器 run_lifecycle_test） |
| 离线 Agent E2E | 100 passed |
| 前端 | 143 passed，lint/build 通过 |
| 独立审查 | 4 Important 均有 RED→GREEN 回归；无 Critical，未登记 Deferred minors |

同库初始化和真实事务门禁必须串行。首次并行触发 DDL deadlock 的 setup error 已记录，串行全量重跑全部通过。

- 最终阶段一真实门禁 51 passed（685.92s，使用服务器隔离库）；恢复、取消、审批与唯一终态保持通过。实现与验收文档均保留在当前开发分支。

### 第二阶段业务验收（2026-10-20，服务器开发库）

环境：`127.0.0.1:15432/sra`、`127.0.0.1:27018/tianyancha`、`127.0.0.1:16379`（SSH 隧道），API 进程 `127.0.0.1:8011`；为隔离投影时序，验收进程使用 `OUTBOX_POLL_SECONDS=3600`，投影由一次性 worker 显式驱动。

| 链路 | 操作 | 验收结果 |
| --- | --- | --- |
| 发起评估、重复请求 | `POST /api/v2/assessments`，重庆卓通汽车工业有限公司，`dimensions=["operational"]`，同一 `request_key` 连续两次 | 两次均 200，评估 ID 均为 `f3f23236-e724-46d8-85fb-a2c350c7c807`；`risk_score=91`、`assessed_at=2026-10-20T08:25:19.110163+00:00` 完全一致，未产生第二条记录 |
| 查看历史、证据、政策，追加复核 | `GET /api/v2/assessments/{id}`、`GET /api/v2/assessments/company/{company_id}`、`POST /api/v2/assessments/{id}/reviews`（confirmed），同键重复复核 | 详情返回政策快照与 `judicial` 证据清单；历史分段 1 条；复核 ID `30844627-cd56-4f64-9b19-4cb6f812e229`，重复请求返回同一复核 ID。复核前后 `risk_score=82`、`assessed_at=2026-10-20T08:22:05.349903+00:00`、原始 `result` 均未变化，复核记录仅 1 条 |
| Mongo 投影失败后补偿 | 评估 `8b2afd7b-f2fa-4231-817f-2886d3b68075` 提交后保持 Outbox 待投递；以不可达 Mongo 端口执行 worker 制造投影失败，再经管理员接口回放 `risk.assessment.committed` 并重投 | 失败时事件 `26f7767c-27f9-45cc-8263-2ed3fafdf7d7` 为 `attempt_count=1`、`last_error=consumer_handler_failed`，Mongo 快照 0 条；此时详情仍 200 且 `risk_score=82`，历史仍为 2 段各 1 条。回放后事件发布时间 `2026-10-20T08:24:30.892147+00:00`，快照恰好 1 条；重复执行投影后仍为 1 条，历史仍为 2 段各 1 条 |

补充观察：

- 首轮以“上海宇内新材料有限公司 + operational”探测时，因该主体缺少可证明时效的运营证据，评估以 `committed/unavailable`、`risk_score=null` 落库；这是数据覆盖结果，不涉及身份或幂等缺陷。正式验收改用具备证据的重庆卓通与浙江亚太机电。
- 四条验收评估在 `assessment_snapshots` 中均为 1 条，Outbox 均已发布；复核仅追加，不改写 `assessment_history` 的冻结结果。
