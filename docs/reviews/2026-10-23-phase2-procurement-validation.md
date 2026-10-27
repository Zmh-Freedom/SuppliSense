# 第二阶段采购流程最终验收记录

日期：2026-10-23  
状态：代码、自动化门禁、隔离 PostgreSQL 与合成三角色/两科室浏览器矩阵均完成。真实 Feishu 身份映射及正式责任数据验证未纳入本轮；这属于生产集成验收边界，不影响隔离环境下的阶段实现验收。

## 批次与提交

### 2A：采购事项与独立关闭审核

提交：`603f6671`、`7f77a5aa`、`17d3a489`、`95e59429`、`a716ca25`、`c7813ec7`、`c907aa01`。

创建、认领/指派、回执、期限、转交、搁置/恢复及独立关闭审核已贯通。`accepted_limitation` 保留 unknown；resolved 需要当前完整覆盖和对应缺口的新有效证据。独立 PostgreSQL 仓储/任务集成 6 passed。合成隔离账号浏览器已覆盖 D1/D2 采购员、经理及管理员、关闭申请人不能自批、接受限制审核、重新打开、期限/搁置语义与 390px 页面；真实 Feishu 身份映射和正式责任数据未验证。

### 2B：寻源部分继续和受权交接

提交：`73d26d75`、`7e639f2d`、`8523edd5`、`b433c953`。

部分候选处置、历史恢复、受权交接与 Harness 返回回执已通过 Agent E2E、独立 PostgreSQL 恢复集和本地 API fixture 浏览器验收。普通聊天入口没有新增执行 mode 分支。2B 报告说明了 fixture 不替代生产数据和真实供应商服务验收。

### 2C：首页工作队列与未知覆盖

提交：`7cc8c782`、`b0f0ac35`、`6c24c6f1`；本轮连接池修复与验收归档提交：`56039c26`。

本人/经理队列、覆盖缺口、权限范围与稳定分页已通过自动化和隔离 PG 验收。合成隔离账号已通过浏览器验证本人/科室队列、跨科室隔离、刷新、逾期搁置事项仍按原期限排序、关闭后队列消失且未知覆盖保留；真实 Feishu 责任快照未验证。

## 最终门禁

| 门禁 | 命令 | 结果 |
| --- | --- | --- |
| 后端完整离线套件 | `PYTHONPATH=../docs/reviews:. .venv/bin/python -m pytest -p operational_metric_offline_guard -m 'not integration and not agent_e2e_live' -q` | 1507 passed，110 deselected |
| 后端 Agent E2E | `PYTHONPATH=../docs/reviews:. .venv/bin/python -m pytest -m agent_e2e -q` | 126 passed，5 skipped；live 项和 integration 项在对应独立命令中处理 |
| 2A PostgreSQL 集成 | `SUPPLISENSE_TEST_PG_DB=run_lifecycle_test PYTHONPATH=. .venv/bin/python -m pytest tests/test_procurement_case_repo.py tests/test_monitor_review_tasks.py -m integration -q` | 6 passed |
| 2B Agent Run 仓储集成 | `SUPPLISENSE_TEST_PG_DB=run_lifecycle_test PYTHONPATH=. .venv/bin/python -m pytest tests/test_agent_run_repo.py -m integration -q` | 16 passed |
| 2C PostgreSQL 集成 | `SUPPLISENSE_TEST_PG_DB=run_lifecycle_test PYTHONPATH=. .venv/bin/python -m pytest tests/test_phase2_procurement_journey.py -m integration -q` | 1 passed |
| 前端完整套件 | `npm test` | 28 files，225 passed |
| 前端静态与构建 | `npm run typecheck`、`npm run lint`、`npm run build` | 全部通过 |
| PostgreSQL 连接池回归 | `tests/test_postgres_pool.py tests/test_runtime_connections.py -q` | 13 passed |

后端测试有 3 条既有 Starlette/Pydantic 弃用警告；Node 测试有 localStorage 实验性警告。没有将这些警告当作通过/失败之外的新缺陷。

## 浏览器验收

本轮在 390px 视口用本地真实 UI、隔离 PostgreSQL `run_lifecycle_test`、MongoDB `tianyancha_test` 和五个合成账号完成验收。浏览器矩阵验证了 D1/D2 采购员及经理、管理员全局列表、本人/科室范围与刷新；事项可由授权链接直达。D1 采购员与 D2 管理员分别记录内部验收回执并申请接受资料限制；管理员作为申请人时没有自批按钮，两项均由独立科室经理批准。关闭后页面仍明确显示资料缺口未解决、覆盖未知；经理重新打开事项后设置过去期限并指派给 D2 负责人，负责人将事项搁置并安排未来复查，刷新后仍按未暂停的原期限显示逾期。关闭状态下对应的本人待办消失，未知覆盖仍为 1/1。工作台键盘 Tab 首段可达跳过链接和菜单，390px 下页面宽度与视口相同；没有观察到飞书、邮件或供应商外部通知。

浏览器没有注入队列单域故障；该失败态由前端/API 自动化回归覆盖。合成用户和责任快照不代表真实 Feishu 身份映射、正式组织责任数据、生产环境调岗撤权；这些留待真实集成验收。浏览器中一次错误用户名输入产生预期 401，纠正后登录成功；未暴露或复用任何真实账号。

## 安全与业务范围

- 2A、2C PostgreSQL 集成只使用显式隔离库 `run_lifecycle_test`；测试行按 fixture ID 管理/清理；未把共享开发库 `sra` 或 `tianyancha` 当作可清空测试库。
- 浏览器 fixture 只写入隔离 PG `run_lifecycle_test` 与 Mongo `tianyancha_test`；验收后按精确 run tag/ID 清理，并确认相关案例、事件、用户、责任快照和 watchlist 记录均为 0。没有把共享开发库 `sra`/`tianyancha` 用作 fixture，没有触发 Feishu、邮件或供应商外部通知。
- 当前未覆盖数据仍保持 unknown/待核验；关闭接受限制事项不会代表供应商安全或资料齐全。
- 没有真实用户前后时长、差错率或采购结果样本，本报告不宣称业务效率提升。

## 问题状态

- `ISS-20261023-017`：隔离 PostgreSQL 并发/去重验收通过，关闭。
- `ISS-20261022-024`：工作台实现、授权队列、未知/覆盖提示及合成多角色/两科室浏览器矩阵、自动化/PG 回归通过，关闭。真实 Feishu 责任身份仍属生产集成验证边界。
- `ISS-20261023-014`、`ISS-20261023-015`：实现回归已通过；真实覆盖源/身份浏览器验收未完成。
- `ISS-20261023-020`：Agent Run PostgreSQL 测试隔离门禁已在隔离库下验证 16 passed；历史上共享库是否接受过幂等 DDL 无法由本轮测试证明，保持其审计边界记录。
- `ISS-20261023-023`：已由 `6c24c6f1` 修复；工作台逐事项授权依赖故障现使事项区变为 unavailable。授权/事项/工作台专项 49 passed，完整后端离线门禁 1507 passed，独立复审通过。
- `ISS-20261023-025`：连接池获取增加有界等待，连接池回归 13 passed；本轮后续浏览器登录、路由、刷新及事项关闭旅程中未再观察到 pool exhaustion。

## 业务基线

本轮记录为工程与自动化验收基线。要测量产品收益，还需在已授权真实采购任务中记录处置耗时、补充资料次数、重复复核/返工率、超期率及覆盖缺口闭环时长；当前没有足够样本形成收益结论。

