# 2C 本人及科室工作队列 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 首页展示真实待处理事项和证据缺口，并可进入准确的事项或监控对象。

**Architecture:** 新增聚合只读API消费2A事项及第一阶段覆盖读模型；
UI复用2A事项详情、2B寻源历史，不维护第二份待办状态。

**Tech Stack:** FastAPI、PG、MongoDB、pytest；React、TanStack Query、Vitest。

**Spec:** [设计§6/8](../specs/2026-10-23-procurement-workflow-closure-design.md)。

## Global Constraints

继承[总计划约束](2026-10-23-phase2-procurement-workflow.md#global-constraints)。
2A验收完成后实施；不修改原dashboard返回结构，不新增外发提醒或虚构默认SLA。
服务不可用不是0，未知不是低风险；队列计数和列表必须使用同一权限与过滤规则。

## Review Focus

1. 同事项属于多个组时只占最高优先组：Task1。
2. 期限边界、时区、搁置不暂停due、无期限：Task1。
3. 管理员/经理的全局概览与“待我审核”不同，禁止本人自批混入队列：Task1。
4. 一对象多缺口不重复计对象，源故障与零结果不同：Task1/2。
5. 从首页进入详情后撤权/任务已变，客户端不能沿旧按钮继续操作：Task2/3。

## 文件职责

- Create `backend/app/domains/alert/workbench_service.py`：队列/覆盖聚合和排序。
- Create `backend/app/domains/alert/workbench_schemas.py`、`api_workbench.py`：只读契约/路由。
- Modify `backend/app/domains/alert/case_repo.py`：受权聚合与游标查询；
  复用 `case_access.py` 和 `coverage_service.py`，禁止复制有效期规则。
- Modify `backend/app/main.py`：挂路由。
- Create `frontend/src/components/WorkQueue.tsx`；Modify `Dashboard.tsx`、
  `types.ts`、`query-keys.ts`、`ProcurementCases.tsx`。
- Create `backend/tests/test_procurement_workbench.py`、`test_procurement_workbench_api.py`；
  Create `frontend/src/__tests__/WorkQueue.test.tsx`，Modify `Dashboard.test.tsx`。

## Task 1：受权队列和覆盖聚合

**Interfaces:** `get_procurement_workbench(user_id: str, user_role: str, *, view: str = 'mine', department_code: str | None = None, cursor: str | None = None, limit: int = 20, now: datetime | None = None) -> dict`。
GET `/api/v1/procurement-workbench?view=mine|department&department_code=...&cursor=...&limit=20`。
response={scope,capabilities,counts,items,next_cursor,coverage,sections}。
counts含pending_closure/overdue/due_review/high/normal，均为队列互斥组总数；
coverage含target_total/unknown_count/stale_count/failed_count；各状态可交叠，禁止相加称总对象。
sections标明cases/coverage各自available/unavailable，失败部分count为null。
items={case_id,target_id,title,group,status,owner_user_id,due_at,next_review_at,detail_url}。
scope和capabilities由后端计算；view=department需当前经理能力，admin显式选定部门。

- [x] **1. 写失败测试。** test_queue_group_precedence：本人可独立审批项优先，
  再overdue/due_review/high/normal；同事项一条；closed排除；
  due_at<=now才逾期，空期限不算；deferred仍可能逾期；closed重开重新计。
  同target多个缺维度仅计一次unknown；旧高风险保持历史标签；
  请求者自批项不进入待我审核；跨科室聚合不可见；cases失效时counts为null。
- [x] **2. 验证RED。** backend `.venv/bin/python -m pytest tests/test_procurement_workbench.py tests/test_procurement_workbench_api.py -q`。
- [x] **3. 实现服务/API。** 复用当前权限和共享覆盖计算，一请求同一now；
  同谓词计算总数和分页项，limit前做权限过滤；优先组+due/null-last+updated/id稳定排序。
  游标保存完整排序键；不受可见前20项限制计总数；单域失败保留另一域并附sections说明。
- [x] **4. 验证GREEN。** 同命令、case_access/api和monitor_coverage_validity专项；
  数据量超过一页、同时间记录和撤权后的总数与详情一致。
- [ ] **5. 提交。** `feat: expose scoped procurement work queues`。

## Task 2：首页真实待办与未知风险

**Interfaces:** queryKeys.procurementWorkbench(view, departmentCode)；WorkQueue消费上节契约。
处置mutation失效事项/队列/监控缓存；WS只复用已有兼容事件，断线后重连触发已有resync。
首页刷新并列请求原风险概览和新队列，分别处理失败；不产生自动外呼采集。

- [ ] **1. 写组件失败测试。** mine/department切换不沿用上一范围数据；
  点击具体事项到/cases/:id，覆盖缺口到/assess/:targetId；
  API失败显示加载失败而非0；unknown图例及分母一致；
  旧高风险+覆盖过期显示历史边界；固定企业示例不进入待办。
- [ ] **2. 验证RED。** frontend `npm test -- src/__tests__/WorkQueue.test.tsx src/__tests__/Dashboard.test.tsx`。
- [ ] **3. 实现Dashboard/WorkQueue。** 首屏显示本人动作队列、经理科室视图和覆盖缺口；
  详情回跳保留筛选；独立错误/空/加载状态；列表计数避免把交叠coverage相加。
  历史快照分布标题注明口径，未知显式可见；原示例移至帮助折叠区域。
- [ ] **4. 验证GREEN。** 前端全test/typecheck/lint/build；
  事项关闭后队列和数量同步刷新，切换账号清除前一用户数据。
- [ ] **5. 提交。** `feat: prioritize real cases and unknown coverage on dashboard`。

## Task 3：三批用户旅程收口

- [ ] **1. 新增 `backend/tests/test_phase2_procurement_journey.py`。** agent_e2e覆盖
  首页待办→同事项处理→经理独立关闭→刷新消失；另测接受限制关闭后unknown覆盖仍存在。
  复用2A/2B真实服务，固定时钟/虚构源/隔离状态边界，断言可验证业务回执。
- [ ] **2. 验证后端和数据库。** 跑新Agent E2E、总计划离线全门禁；
  独立PG验证聚合计数/分页及关闭事务后的队列变化。
- [ ] **3. 浏览器验收。** 本人和经理队列、不同科室、刷新、无期限、过期、部分服务故障、
  390px布局、键盘可达及真实详情导航；确认没有自动飞书/邮件外发。
- [ ] **4. 最终审查与提交。** 写 `docs/reviews/2026-10-23-phase2c-workbench-validation.md`，
  最终提交 `test: verify actionable procurement workbench journeys`；
  024仅在未知/比例、真实待办、权限和导航全部通过后关闭。
  继续总计划的第二阶段整体审查，业务指标记录基线，不宣称已有收益提升。
