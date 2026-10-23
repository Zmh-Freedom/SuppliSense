# 第二阶段采购流程交付 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 采购事项有明确责任人、处理结果和关闭回执，寻源可以部分继续并恢复上下文，首页呈现真实待办。

**Architecture:** 新增 PG 业务事项层关联现有复核执行记录；复用责任快照授权。
寻源保留专用页面现有图，聊天继续 Harness；通过服务端受权交接连接两者。

**Tech Stack:** FastAPI、Pydantic v2、PostgreSQL、MongoDB、现有 LangGraph；React 19、TanStack Query 5、pytest、Vitest。

**Spec:** [第二阶段设计](../specs/2026-10-23-procurement-workflow-closure-design.md)。

**Status:** 分批实现、自动化门禁及隔离 PostgreSQL 验收已完成。真实三角色/两科室浏览器验收仍待具备授权的隔离测试身份，故整体验收保留未完成状态。

## Global Constraints

- “不修改旧 API 返回结构、既有 SSE 事件格式或历史评估，不回填历史业务关闭状态。”
- “关闭申请人不能自批”；管理员也不能绕过。
- “来源缺失不强迫采购员上传外部资料；事项关闭不等于供应商已安全或资料已齐全。”
- “普通聊天仍只进入 Harness”；领域服务无 LangGraph 依赖。
- “前端不增加 services/store/features 目录”；类型和 query key 分别集中维护。
- 开发与联调先确认服务器隧道及三库认证；仅使用 backend/.env 已验证配置。
- 写入集成测试仅服务器独立测试库、独立 Redis 范围；绝不能清空 sra/tianyancha。
- 离线单元和 Agent E2E 隔离 DB/provider/LLM；问题先记 docs/issue-log.md 再修复。
- 精确暂存本批文件，保留既有三项未跟踪文件；每个任务一组相关提交，不自动推送/部署。

## Review Focus

- 同一供应商跨科室、经理调岗、所有者撤权：可见范围和管理权分别验证，2A Task 1/3。
- 接受限制的关闭与有效风险结论：关闭不污染 Evidence/Claim 或风险颜色，2A Task 3/4。
- 候选部分提交及恢复重启：未知候选不能外呼评分，决策必须落库并可恢复，2B Task 1/2。
- 父任务可读但目标主体撤权/证据过期：交接重验而不沿用安全结论，2B Task 4。
- 分页和首页部分故障：计数与列表同权限，失败不为0，2B Task 3、2C Task 1/2。

## 分批交付及依赖

| 批次 | 关联问题 | 独立产物 | 计划 |
| --- | --- | --- | --- |
| 2A | 019、020 | 经理协作与业务事项关闭完整旅程 | [2A 实施计划](2026-10-23-phase2a-review-case-closure.md) |
| 2B | 021、022 | 部分候选继续、统一历史及候选受权交接 | [2B 实施计划](2026-10-23-phase2b-sourcing-continuity.md) |
| 2C | 024 | 本人/科室工作队列、首页未知与过期覆盖 | [2C 实施计划](2026-10-23-phase2c-workbench-queue.md) |

- [x] **1. 审阅设计及三份计划。** 确认设计第9节产品选择；修订时同步接口与验收。
- [ ] **2. 实施2A并验收。** 从事项创建到独立关闭审核，先贯通一条完整旅程。
- [x] **3. 实施2B并验收。** 验证专用寻源与普通聊天运行时边界，禁止新增聊天 mode 分支。
- [ ] **4. 实施2C并验收。** 复用2A事项和2B历史入口，不建立第二套待办状态。
- [x] **5. 第二阶段整体审查。** 新上下文审查权限和三条跨批旅程；已登记并修复工作台授权依赖故障，Important/Critical 修复后复审通过。
- [ ] **6. 最终门禁与归档。** 运行下列门禁并记录结果，按能力实际完成情况关闭019–022/024。

进度说明：2A/2C 的实现、完整后端离线门禁（1506 passed）、Agent E2E、前端门禁及隔离 PostgreSQL 并发/分页验收已通过。剩余浏览器矩阵需要采购员、经理、管理员和两个科室的已授权隔离身份；当前没有可用登录身份，不用共享开发库创建用户或业务数据替代。

## 跨批最终门禁

后端目录：
`PYTHONPATH=../docs/reviews:. .venv/bin/python -m pytest -p operational_metric_offline_guard -m 'not integration and not agent_e2e_live' -q`。
另明确执行 `agent_e2e` 标记集，确保新增完整旅程实际收集执行，而非仅靠文件存在。
前端目录依次 `npm test`、`npm run typecheck`、`npm run lint`、`npm run build`。
根目录 `git diff --check`；有代码变化时受影响专项及相应门禁必须用最终代码重跑。

集成测试在显式隔离库上运行2A并发/迁移及2B恢复测试；若独立库不可用，保留阻塞项，
不能用纯 mock 结果宣称数据库并发验收通过。浏览器验收采购员/经理/管理员三种身份，
两科室隔离、撤权、刷新、重复提交及390px窄屏，记录真实账号链路和fixture的各自范围。
不得为验收向共享开发库批量写入fixture或发送外部通知。

最终报告保存到 `docs/reviews/2026-10-23-phase2-procurement-validation.md`，
列提交、场景、通过/失败/未执行、数据隔离、浏览器身份验收阻塞、未开放来源及业务政策边界。
未接入正式名单、未确认运营评级或交易有效期，继续沿用第一阶段未知/待核验规则。

## 文档到任务的覆盖

设计§3→2A1/2；§4→2A2–5；§5.1→2B1/2；§5.2→2B3；§5.3→2B4/5；
§6→2C1–3；§7→各批迁移/兼容回归；§8→各批E2E及本计划最终门禁。
首个可交付切片是2A；第二、三批无需同时修改后再验收。
