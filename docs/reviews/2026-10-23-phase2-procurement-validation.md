# 第二阶段采购流程最终验收记录

日期：2026-10-23  
状态：代码、自动化门禁与隔离 PostgreSQL 验收完成；真实三角色浏览器矩阵尚待授权隔离身份，故第二阶段整体验收保持未完成。

## 批次与提交

### 2A：采购事项与独立关闭审核

提交：`603f6671`、`7f77a5aa`、`17d3a489`、`95e59429`、`a716ca25`、`c7813ec7`、`c907aa01`。

创建、认领/指派、回执、期限、转交、搁置/恢复及独立关闭审核已贯通。`accepted_limitation` 保留 unknown；resolved 需要当前完整覆盖和对应缺口的新有效证据。独立 PostgreSQL 仓储/任务集成 6 passed。真实 Feishu 责任身份、调岗撤权及 390px 页面矩阵仍待隔离测试账号。

### 2B：寻源部分继续和受权交接

提交：`73d26d75`、`7e639f2d`、`8523edd5`、`b433c953`。

部分候选处置、历史恢复、受权交接与 Harness 返回回执已通过 Agent E2E、独立 PostgreSQL 恢复集和本地 API fixture 浏览器验收。普通聊天入口没有新增执行 mode 分支。2B 报告说明了 fixture 不替代生产数据和真实供应商服务验收。

### 2C：首页工作队列与未知覆盖

提交：`7cc8c782`、`b0f0ac35`、`6c24c6f1`，以及本次连续旅程验收提交。

本人/经理队列、覆盖缺口、权限范围与稳定分页已通过自动化和隔离 PG 验收。真实多角色浏览器矩阵尚待测试身份。

## 最终门禁

| 门禁 | 命令 | 结果 |
| --- | --- | --- |
| 后端完整离线套件 | `PYTHONPATH=../docs/reviews:. .venv/bin/python -m pytest -p operational_metric_offline_guard -m 'not integration and not agent_e2e_live' -q` | 1506 passed，110 deselected |
| 后端 Agent E2E | `PYTHONPATH=../docs/reviews:. .venv/bin/python -m pytest -m agent_e2e -q` | 126 passed，5 skipped；live 项和 integration 项在对应独立命令中处理 |
| 2A PostgreSQL 集成 | `SUPPLISENSE_TEST_PG_DB=run_lifecycle_test PYTHONPATH=. .venv/bin/python -m pytest tests/test_procurement_case_repo.py tests/test_monitor_review_tasks.py -m integration -q` | 6 passed |
| 2B Agent Run 仓储集成 | `SUPPLISENSE_TEST_PG_DB=run_lifecycle_test PYTHONPATH=. .venv/bin/python -m pytest tests/test_agent_run_repo.py -m integration -q` | 16 passed |
| 2C PostgreSQL 集成 | `SUPPLISENSE_TEST_PG_DB=run_lifecycle_test PYTHONPATH=. .venv/bin/python -m pytest tests/test_phase2_procurement_journey.py -m integration -q` | 1 passed |
| 前端完整套件 | `npm test` | 28 files，225 passed |
| 前端静态与构建 | `npm run typecheck`、`npm run lint`、`npm run build` | 全部通过 |

后端测试有 3 条既有 Starlette/Pydantic 弃用警告；Node 测试有 localStorage 实验性警告。没有将这些警告当作通过/失败之外的新缺陷。

## 浏览器未完成项

当前页面只到本地登录页。没有可用的已授权采购员/经理/管理员及两个科室身份。尝试通过 Playwright CLI 启动 Chrome 时，桌面环境对进程/用户缓存访问返回 `EPERM`；仅使用了工作区本地 Vite 页面查看登录门槛，没有登录、外发消息或触碰生产业务数据。

待拥有隔离测试身份后执行：

- 2A：三角色/两科室、当前责任范围变更和撤权、关闭申请人自批拒绝、搁置到期及刷新、390px。
- 2C：本人/科室队列、部门切换、刷新、无期限/过期事项、部分服务故障、390px、键盘可达和真实详情导航，并观察没有自动飞书/邮件。

此前 2A/2C 的组件/API 回归和 Agent E2E 已覆盖对应状态逻辑，但不能替代登录态真实页面验收。

## 安全与业务范围

- 2A、2C PostgreSQL 集成只使用显式隔离库 `run_lifecycle_test`；测试行按 fixture ID 管理/清理；未把共享开发库 `sra` 或 `tianyancha` 当作可清空测试库。
- 本轮没有通过共享开发库创建账号、事项或消息；没有触发 Feishu、邮件或供应商外部通知。
- 当前未覆盖数据仍保持 unknown/待核验；关闭接受限制事项不会代表供应商安全或资料齐全。
- 没有真实用户前后时长、差错率或采购结果样本，本报告不宣称业务效率提升。

## 问题状态

- `ISS-20261023-017`：隔离 PostgreSQL 并发/去重验收通过，关闭。
- `ISS-20261022-024`：工作台实现、授权队列、未知/覆盖提示及自动化/PG 回归通过；真实多角色浏览器验收待身份。
- `ISS-20261023-014`、`ISS-20261023-015`：实现回归已通过；真实覆盖源/身份浏览器验收未完成。
- `ISS-20261023-020`：Agent Run PostgreSQL 测试隔离门禁已在隔离库下验证 16 passed；历史上共享库是否接受过幂等 DDL 无法由本轮测试证明，保持其审计边界记录。
- `ISS-20261023-023`：已由 `6c24c6f1` 修复；工作台逐事项授权依赖故障现使事项区变为 unavailable。授权/事项/工作台专项 49 passed，完整后端离线门禁 1506 passed，独立复审通过。

## 业务基线

本轮记录为工程与自动化验收基线。要测量产品收益，还需在已授权真实采购任务中记录处置耗时、补充资料次数、重复复核/返工率、超期率及覆盖缺口闭环时长；当前没有足够样本形成收益结论。

