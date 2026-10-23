# 2B 寻源候选独立处置与任务续接 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 已确认候选可独立继续，用户能找回任务，并携稳定主体和需求上下文继续核验。

**Architecture:** 新增候选处置与交接读写接口，保留旧run和request结构；
领域纯函数统一资格筛选，专用寻源图和Harness分别消费，不新增聊天执行内核。

**Tech Stack:** FastAPI、Pydantic v2、PG、LangGraph、pytest；React、TanStack Query、Vitest。

**Spec:** [设计§5/7/8](../specs/2026-10-23-procurement-workflow-closure-design.md)。

## Global Constraints

继承[总计划约束](2026-10-23-phase2-procurement-workflow.md#global-constraints)。
所有新路由 `/api/v1/sourcing-workspace`；旧AgentRunResponse/IdentityResolutionRequest/
ChatRequest和SSE格式不扩展。旧identity-resolution入口也必须复用严格主体校验。
灰度/取消/审批门禁保持；未确认、暂存、排除候选不得进入调查、正式评分或写提案。

## Review Focus

1. 恶意company_id、重复review key、同主体多候选：Task1。
2. 决策已提交但进程在resume前退出、两次resume：Task2。
3. 历史分页同一更新时间、混合两来源及灰度关闭：Task3。
4. 手工篡改handoff/session、父任务撤权、证据失效：Task4。
5. 页面刷新和重复点击不能自动重复聊天/写操作：Task4/5。

## 文件职责

- Create `backend/app/domains/sourcing_risk/candidate_review.py`：纯候选决策与分组。
- Create `backend/app/domains/sourcing_risk/candidate_review_service.py`、`candidate_review_repo.py`：
  受权事务、持久化批次、版本、幂等和待恢复记录。
- Create `backend/app/domains/sourcing_risk/workspace_service.py`、`workspace_schemas.py`、`api_workspace.py`：
  两来源列表和新API；历史查询SQL分别在agent_run/repo.py、sourcing对应现有repo扩展。
- Create `backend/app/domains/sourcing_risk/handoff_service.py`、`handoff_repo.py`：服务端会话交接。
- Modify `backend/app/db/init_pg.py`、`backend/app/main.py`：新表和router。
- Modify `backend/app/domains/agent_run/service.py`、`api.py`：旧确认入口严格校验及共用恢复边界。
- Modify `backend/app/graphs/sourcing_risk_v2/{nodes,runner,state}.py`：候选分组和恢复路由；
  状态定义使用现有 `state.py`。
- Modify `backend/app/domains/agent_run/execution_jobs.py`、`backend/app/workers/agent_worker.py`：
  为专用寻源候选恢复增加显式分发及租约保护，禁止隐式进入Harness执行分支。
- Modify `backend/app/domains/agent_run/state_store.py`：session+handoff原子创建和turn消费绑定。
- Modify `backend/app/graphs/agent_core/adapter.py`、`backend/app/api/chat.py`：唯一上下文加载边界交接重验。
- Create `frontend/src/components/SourcingTaskHistory.tsx`、`SourcingHandoffPanel.tsx`；
  Modify `SourcingPage.tsx`、`SourcingRiskWorkbench.tsx`、`ChatView.tsx`、
  `hooks/useSourcingRiskRun.ts`、`types.ts`、`query-keys.ts`。
- 新增测试文件按下面每项给出；保留原sourcing/agent_run/chat全部回归。

## Task 1：候选独立处置契约

**Interfaces:** `apply_candidate_decisions(candidates: list[dict], decisions: list[dict]) -> dict`，
返回eligible/deferred/excluded三组，原输入不修改；每decision含candidate_key、
action(confirm/defer/exclude)、company_id(confirm必需)、reason(其余必需)。
`submit_candidate_decisions(run_id: str, payload: dict, user_id: str, user_role: str, request_key: str) -> dict`。
新GET `/runs/{run_id}/candidate-decisions` 和POST同路径；响应固定
{run_id,run_version,dispositions,resume_status,new_run_id}，new_run_id可空。
旧run仍按原schema序列化，前端从新接口取得处置，不能悄悄添加旧响应字段。

- [x] **1. 写失败测试。** `tests/test_sourcing_candidate_review.py`：
  exact A+无选项B，B defer时只有A eligible；全部exclude零eligible；
  伪造company_id不在当前候选options拒绝；重复candidate_key拒绝；
  已exact未提交仍保留；未提交的pending转defer且固定reason；原输入不变。
- [x] **2. 验证RED。** backend `.venv/bin/python -m pytest tests/test_sourcing_candidate_review.py -q`。
- [x] **3. 实现纯函数/事务服务。** 新表sourcing_candidate_dispositions保存当前处置，
  sourcing_candidate_review_events追加保留各批决策/actor/reason；锁run、复核权限、CAS版本、
  写批次及恢复意图同事务；幂等(actor,key)+请求hash，冲突409。
  稳定key取已持久化review/candidate键；无稳定键时拒绝提交，不用名称补造。
- [x] **4. 验证GREEN。** 上述测试、新 `tests/test_sourcing_candidate_review_api.py` 和旧
  `tests/test_agent_run_service.py tests/test_agent_run_api.py`；旧确认入口也不能信任任意company_id。
- [x] **5. 提交。** `feat: persist independent sourcing candidate decisions`，补021记录。

## Task 2：恢复、过滤与终态候选派生

**Interfaces:** Task1分组供节点消费；新增
`resume_candidate_review_batch(batch_id: str) -> dict`（服务端恢复意图的幂等调度边界）；
`derive_candidate_followup(run_id: str, candidate_keys: list[str], user_id: str, user_role: str, request_key: str) -> dict`。
新POST `/runs/{run_id}/followups`返回{parent_run_id,run_id,resume_url}；
父任务终态不可更改，派生需求和政策引用从受权父任务读取。

- [x] **1. 写失败测试。** `tests/test_sourcing_candidate_resume.py`：
  partial审核后仅eligible传给真实investigate/decision；defer/exclude不外呼；
  全部暂存=>NEEDS_REVIEW，部分处理=>PARTIAL并保留暂停组；
  不新增公共status枚举。原pending返回不能穿过runner无条件边进入调查。
  提交后resume前崩溃可按PG批次恢复；重复调度不能重复推进；取消先于恢复则停止。
- [x] **2. 验证RED。** `.venv/bin/python -m pytest tests/test_sourcing_candidate_resume.py -q`。
- [x] **3. 修改节点与runner。** 将Task1分组保存在内部state并持久化决策源；
  identity_review后明确有eligible/无eligible路由；原样Command(resume=...)，
  使用现有执行租约/持久job机制处理待恢复记录，禁止只靠asyncio.create_task保证恢复。
  新恢复记录消费成功前不标完成；重复交付先检查批次已消费和run版本。
  暂存恢复在终态创建子run，排除候选需先显式新决策恢复为defer，不能直接派生排除项。
- [x] **4. 验证GREEN。** 新专项及 `test_sourcing_risk_graph.py`、
  `test_agent_run_checkpointer.py`、`test_agent_harness_identity_review.py`；
  独立PG/checkpointer集成验证重启/抢占/并发，仅mock不算持久恢复已验收。
- [x] **5. 提交。** `fix: resume sourcing with eligible candidates only`。

## Task 3：统一任务历史与实际候选操作

**Interfaces:** `list_sourcing_tasks(user_id: str, user_role: str, *, cursor: str | None, limit: int = 20) -> dict`；
返回{items,next_cursor,sources}，items字段见设计§5.2，sources标记各来源available/unavailable/disabled。
GET `/tasks`只聚合受权两来源，查询前完成owner/run_type/灰度约束；分页游标包含排序三元组，
服务器校验不合法游标400；部分来源失败时不生成虚假完整分页，返回该来源unavailable且next_cursor=null，
要求重试本页，已展示项按source_type/id去重。
legacy查询在 `backend/app/domains/sourcing/repo.py` 新增受权游标读函数，
无updated_at使用created_at排序；不能用当前仅created_at/页码排序的list_requests硬拼游标。

- [ ] **1. 写失败测试。** `tests/test_sourcing_workspace.py`：同更新时间无遗漏重复；
  run和legacy id相同仍分两任务；非本人run不可见；普通Harness聊天排除；
  经理无全局私有任务权；灰度未开不泄露run；来源失败显示局部不可用。
  前端 `SourcingTaskHistory.test.tsx`、扩展 `SourcingRiskWorkbench.test.tsx`：
  exact与无选项混合可提交，明确剩余暂存；历史深链接能恢复原来源和选定候选。
- [ ] **2. 验证RED。** 运行上述后端文件和前端两个组件测试文件。
- [ ] **3. 实现历史API及组件。** 替换仅legacy历史的入口；候选提供确认/本轮暂存/排除及理由。
  原legacy任务仍打开兼容结果，不强制转换成run；新run打开现有run URL。
  历史状态含暂停组提示，但不改旧AgentRunResponse；新query key独立集中定义。
- [ ] **4. 验证GREEN。** 上述测试、`test_sourcing_service.py`、前端typecheck/lint/build。
- [ ] **5. 提交。** `feat: restore sourcing history and partial candidate progress`。

## Task 4：服务端受权交接到Harness

**Interfaces:** `create_candidate_handoff(payload: dict, user_id: str, user_role: str, request_key: str) -> dict`；
`get_candidate_handoff(handoff_id: str, user_id: str) -> dict | None`；
`resolve_session_handoff(session_id: str, user_id: str, execution_context: dict) -> dict`。
POST `/handoffs`、GET `/handoffs/{id}`；持久表sourcing_handoffs绑定user/session/source run/version/
candidate/company/requirement/evidence refs/intent/status/consumed_turn_id。
新session通过现有SessionStateStore创建；加同事务接口保障session+binding+幂等结果原子写入。

- [ ] **1. 写失败测试。** `tests/test_sourcing_handoff.py`：换user/session/run/candidate被拒；
  过期证据只作历史；候选身份冲突触发现有澄清，不自动选主体；
  parent run版本改变409要求重新交接；刷新不创建第二会话；执行前撤权拒绝。
  `tests/test_sourcing_handoff_harness.py`使用真实Registry/Executor/Harness，
  断言execution_context仅加载一次、只读走Harness、monitor_proposal无审批不得写入。
- [ ] **2. 验证RED。** backend `.venv/bin/python -m pytest tests/test_sourcing_handoff.py tests/test_sourcing_handoff_harness.py -q`。
- [ ] **3. 实现handoff服务及加载边界。** 父任务证据仅ref，权限/新鲜度由服务端校验；
  merge进入同一execution_context快照，不在图内重新load session。
  在start_harness_turn事务中认领binding并记consumed_turn_id；重复提交绑定返回原turn/run回执，
  中断可恢复但不再创建新turn。保持ChatRequest及事件格式，复用现有run状态/事件恢复端点。
- [ ] **4. 前端实现并测试。** `SourcingHandoffPanel`加载受权详情，用户点击继续，
  消费后显示原run进度；清除q自动提交路径，旧q深链接仍兼容；
  新增 `SourcingHandoffPanel.test.tsx` 并扩展 `ChatView.test.tsx` 验证刷新无重复提交。
  跑后端专项、前端专项及typecheck。
- [ ] **5. 提交。** `feat: bind sourcing handoffs to authorized Harness sessions`。

## Task 5：跨页面旅程验收

- [ ] **1. Agent E2E。** 新 `tests/test_sourcing_continuity_e2e.py` 标记agent_e2e：
  混合候选→暂存未知→已确认调查→历史找回→受权交接→Harness返回可验证业务回执；
  固定虚构源，真实编排/工具/服务边界，禁止手写整套假答案替代实际链路。
- [ ] **2. 数据库恢复及全门禁。** 独立PG运行Task2重启/重复投递，
  后端离线门禁、前端test/typecheck/lint/build；记录灰度前置状态。
- [ ] **3. 浏览器验收。** 两来源历史、部分继续、终态派生、浏览器刷新/后退、
  手工篡改交接ID、撤权和390px；验证写意图仍停在人工审批。
- [ ] **4. 独立审查和提交。** 报告 `docs/reviews/2026-10-23-phase2b-sourcing-validation.md`，
  重要问题先记日志再修复，最终提交 `test: verify sourcing continuity across pages`，更新021/022。
