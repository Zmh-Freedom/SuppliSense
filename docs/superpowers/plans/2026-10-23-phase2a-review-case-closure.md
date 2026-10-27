# 2A 复核事项协作与关闭 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 贯通采购员发起、经理分派、负责人处置、独立审核关闭的完整事项流程。

**Architecture:** PG 增量业务事项表关联现有 monitor_review_tasks，授权复用当前责任快照。
技术执行状态保持原义，事项状态由单独事务服务管理。

**Tech Stack:** FastAPI、Pydantic v2、psycopg2、pytest；React、TanStack Query、Vitest。

**Spec:** [设计§3/4/6/7](../specs/2026-10-23-procurement-workflow-closure-design.md)。

## Global Constraints

完整继承[总计划约束](2026-10-23-phase2-procurement-workflow.md#global-constraints)。
新增API均在 `/api/v1/procurement-cases`，旧复核任务成功字段集合不变。
业务关闭不自动改风险、不把用户回执升格为正式来源证据；不得自批。
新服务返回dict，Repository未找到返回None；API映射403/404/409/422统一错误信封。

## Review Focus

1. 多科室不猜默认部门、经理调岗后旧权限消失：Task1。
2. 指派不授予对象权限，外部对象不能借名称进入科室：Task1/3。
3. 同key异payload、并发指派和关闭：Task2/3，必须真实PG并发验证。
4. 执行结束、accepted_limitation与风险结论保持独立：Task3/4。
5. 历史任务completed与缺期限，不误显示业务关闭/逾期：Task4/5。

## 文件职责

- Create `backend/app/domains/alert/case_access.py`：共享能力与当前科室范围。
- Create `backend/app/domains/alert/case_repo.py`：事务SQL、幂等、事件和链接。
- Create `backend/app/domains/alert/case_service.py`：状态和关闭证据规则，框架无关。
- Create `backend/app/domains/alert/case_schemas.py`、`api_cases.py`：新契约/路由。
- Modify `backend/app/db/init_pg.py`：新建表和索引，保持幂等DDL。
- Modify `backend/app/domains/alert/review_tasks.py`、`api.py`：授权共用，保留旧响应。
- Modify `backend/app/main.py`：挂新router。
- Create `frontend/src/components/ProcurementCases.tsx`、`ProcurementCasePanel.tsx`；
  Modify `MonitoringView.tsx`、`routes.tsx`、`types.ts`、`query-keys.ts`。
- Test：新增 `backend/tests/test_procurement_case_{access,repo,service,api,e2e}.py`；
  前端新增 `ProcurementCases.test.tsx`，扩展 `MonitoringView.test.tsx`。

## Task 1：动作能力与经理范围

**Interfaces:**
`resolve_case_scope(target: dict, user_id: str, user_role: str, department_code: str | None) -> dict`；
`case_capabilities(case: dict, user_id: str, user_role: str) -> dict`。
返回scope包含 target_id/department_code/is_manager；capabilities包含
can_read/can_claim/can_assign/can_update/can_request_closure/can_decide_closure/can_reopen。
当前manager映射仅来自current/source_active快照及活跃用户；viewer全部false。

- [x] **1. 写测试。** `test_manager_scope_does_not_equal_supplier_visibility`：采购员可见供应商但非经理；
  两科室同供应商必须指定部门；撤权后read/update false；外部对象只所有者/admin；
  管理员申请关闭后其decide false；viewer拒绝操作，历史analyst按采购员兼容。
- [x] **2. 验证RED。** backend `.venv/bin/python -m pytest tests/test_procurement_case_access.py -q`。
- [x] **3. 实现case_access。** 复用供应商访问能力和当前用户映射；不扩展UserRole。
  读取共享授权结果，任何异常fail closed；权限不由客户端owner或department字符串授予。
- [x] **4. 验证GREEN。** 上述测试和 `tests/test_monitor_review_tasks.py`；保留旧请求者合法路径。
- [x] **5. 提交。** `feat: define scoped procurement case capabilities`，更新019问题进度。

## Task 2：事项、事件与并发安全存储

**Interfaces:** `case_repo.create_or_get_case(values: dict, actor_id: str, request_key: str) -> dict`；
`get_case(case_id: str, *, cur: object | None = None, for_update: bool = False) -> dict | None`；
`apply_case_transition(case_id: str, expected_version: int, changes: dict, event: dict, *, cur: object) -> dict | None`。
新增表为设计§4.1三张表和 `procurement_case_requests`；requests唯一(actor_id,key)，保存
canonical request hash、response和case_id；所有更新与幂等回执同事务。

- [x] **1. 写失败测试。** 重复创建同活动唯一键返回原事项；并发创建只有一事项；
  同key同请求返回原响应，同key不同payload拒绝；事件/链接失败整体回滚；
  closed后同触发另开必须关联前事项。活动部分唯一索引排除closed。
- [x] **2. 验证RED。** `.venv/bin/python -m pytest tests/test_procurement_case_repo.py -m 'not integration' -q`。
- [x] **3. 实现DDL和repo。** 状态/priority/version检查、FK和索引；关闭记录不覆盖事件。
  case_tasks唯一(case_id,task_id)，事件唯一(case_id,version)；事务内SELECT FOR UPDATE+CAS。
  读分页先约束当前scope再LIMIT；禁止先取全局200条后内存过滤。
- [x] **4. 验证GREEN。** 离线 repo 回归通过；隔离 PostgreSQL 集成用例因 `SUPPLISENSE_TEST_PG_DB` 未配置安全跳过。
- [x] **4a. 真实PG验收。** `SUPPLISENSE_TEST_PG_DB=run_lifecycle_test` 下运行仓储与复核任务集成集，6 passed；共享库保护未放宽。
- [x] **5. 提交。** `feat: persist procurement cases and immutable handling events`。

## Task 3：生命周期、新API与旧任务兼容

**Interfaces:** `create_case(payload: dict, user_id: str, user_role: str, request_key: str) -> dict`；
`get_case_detail(case_id: str, user_id: str, user_role: str) -> dict | None`；
`list_cases(filters: dict, user_id: str, user_role: str) -> dict`；
`handle_case_command(case_id: str, command: dict, user_id: str, user_role: str, request_key: str) -> dict`。
command类型：claim/assign/request_transfer/receipt/defer/resume/set_due/request_closure/
approve_closure/reject_closure/reopen；含expected_version及各动作必需字段。

新API：GET/POST集合、GET /{id}、POST /{id}/commands、POST /{id}/review-tasks。
新响应detail={case,events,linked_tasks,capabilities}；列表={items,next_cursor}。
分页默认20最大100，排序updated_at降序/id升序；未授权详情统一404防枚举。
POST review-tasks消费现有create_review_task，链接事务与幂等键须保障崩溃后可恢复关联。
在review_tasks.py抽出 `create_review_task_with_cursor(cur: object, values: dict) -> dict`，
原公开函数保留签名并调用该内部写入；新case入口在同一事务创建task、case_tasks链接及幂等回执。
任务去重命中其他事项的活动执行时只关联同对象且受权的任务，不创建第二份外呼。
command必需字段：assign为owner_user_id；request_transfer为reason；receipt为summary及refs；
defer为reason及next_review_at；set_due为due_at（可空）；request_closure为category/summary/receipt_ids；
approve/reject_closure及reopen为reason；claim/resume无附加字段。未列字段拒绝，文本1–2000字符。

- [x] **1. 写失败测试。** 所有合法/非法状态边；claim只有open且owner空；
  搁置无原因/非未来复查时间拒绝；关闭需回执，覆盖resolved需当前有效新证据，
  accepted_limitation需残余限制；审批者非申请人；duplicate同scope无循环；
  责任变更后操作拒绝；转交仅记录请求、经理assign才改owner；技术completed不关事项。
- [x] **2. 验证RED。** `.venv/bin/python -m pytest tests/test_procurement_case_service.py tests/test_procurement_case_api.py -q`。
- [x] **3. 实现状态服务及路由。** 状态转换严格用设计图；每条命令同事务事件和version。
  当前关联事项及旧任务都通过Task1能力校验；旧任务决策仍返回原结构，UI语义改确认执行。
  alert/service.py的嵌入任务通过同一授权校验，无权时review_task为None，不能只隐藏前端按钮。
  非法输入422，版本/状态409，不存在404，越权动作403；同步service用to_thread。
  再次执行读取当前来源，不把旧coverage payload当当前证明；运行前撤权必须拒绝。
- [x] **4. 验证GREEN。** 新测试、`test_monitor_review_tasks.py`、`test_monitor_coverage_validity.py`；
  固定旧返回字段集合，验证新API/列表计数无越权泄露。
- [x] **5. 提交。** `feat: support scoped case handling and independent closure review`。

## Task 4：真实页面贯通

**Interfaces:** types.ts新增ProcurementCase、CaseDetail、CaseCapabilities；
queryKeys.procurementCases(filters)、procurementCase(id)；新组件使用新契约。
`ProcurementCasePanel({caseId}: {caseId: string})`；列表支持mine/department视图。
监控对象通过稳定monitor_target_id查询事项，不通过企业名称找事项。

- [x] **1. 写组件失败测试。** 创建→接手→回执→申请关闭→切换经理审核；
  capabilities控制按钮；409保留输入并刷新；刷新和深链接恢复同事项；
  原技术completed显示执行完成；accepted_limitation关闭后仍显示资料缺口。
- [x] **2. 验证RED。** frontend `npm test -- src/__tests__/ProcurementCases.test.tsx src/__tests__/MonitoringView.test.tsx`。
- [x] **3. 实现页面。** 新/cases导航、详情与监控入口；状态/动作中文，期限与下次复查分开。
  mutation成功失效列表/详情/监控及未来工作台query key，失败不清空用户回执草稿。
- [x] **4. 验证GREEN。** 同组件命令及 `npm run typecheck`、`npm run lint`、`npm run build`。
- [x] **5. 提交。** `feat: deliver procurement case handling flow in monitoring`。

## Task 5：闭环验收与分批收口

**Interfaces:** 使用真实router/service/repo边界和实际组件，不另造业务状态机。

- [x] **1. 写Agent E2E。** `@pytest.mark.agent_e2e`：监控缺口→发起事项→
  实际复核执行needs_review→回执→独立审核accepted_limitation；最终事项closed但风险仍未知。
  固定输入，隔离外部服务；另测无权操作在读源前拒绝、重复命令无重复执行。
- [x] **2. 跑专项与集成。** 完整后端离线门禁、Agent E2E、前端全门禁均通过；`SUPPLISENSE_TEST_PG_DB=run_lifecycle_test` 下 2A 仓储/复核集成 6 passed，覆盖并发、回滚、重复初始化和任务去重。
- [x] **3. 浏览器验收。** 已使用隔离 PG/Mongo fixture 在 390px 浏览器验证 D1/D2 采购员和经理、管理员全局列表、本人/科室隔离、刷新与深链接；管理员提交关闭申请后没有审核控件，另一科室经理可独立批准。验证了关闭后重新打开、设置过去期限、搁置并设未来复查时间；刷新后事项仍按原期限计为逾期。D1/D2 关闭接受资料限制后仍显示覆盖未知。文档明确该 fixture 不覆盖真实 Feishu 身份映射、正式责任数据与调岗撤权；隔离 PostgreSQL 并发验证由独立 integration 测试覆盖。
- [x] **4. 独立审查和修复。** 复核提出的两个 Important 均补回归后修复；受影响门禁重跑通过。
- [x] **5. 提交验收。** `test: verify procurement case handling and closure journey`；
  写 `docs/reviews/2026-10-23-phase2a-case-validation.md`，019/020只按实际覆盖关闭。
