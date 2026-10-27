# Phase 2C 采购工作台验收记录

日期：2026-10-23  
状态：实现、自动化门禁和隔离 PostgreSQL 验收完成；真实多角色浏览器验收待授权身份。

## 已交付

- 后端受权队列按当前 case capabilities 过滤，计数与分页使用相同可见集合；授权依赖无法确认时，事项区标记 unavailable，不再呈现误导性部分/空队列。提交：`7cc8c782`，授权故障修复与回归见本次收尾提交。
- 首页显示本人待办/经理科室队列及 unknown、stale、failed 覆盖；保留历史风险分布提示，并将固定案例收至折叠示例。提交：`b0f0ac35`。
- 增加跨批连续旅程：采购员创建并处理事项、经理独立审核关闭，关闭后事项从队列消失而 unknown 覆盖仍显示；另有隔离 PG 计数与无期限稳定游标分页测试。

## 验证

| 范围 | 命令 | 结果 |
| --- | --- | --- |
| 工作台连续旅程 | `PYTHONPATH=. .venv/bin/python -m pytest tests/test_phase2_procurement_journey.py -m agent_e2e -q` | 1 passed |
| 工作台隔离 PostgreSQL | `SUPPLISENSE_TEST_PG_DB=run_lifecycle_test PYTHONPATH=. .venv/bin/python -m pytest tests/test_phase2_procurement_journey.py -m integration -q` | 1 passed；25 条隔离 fixture 分三页返回、计数一致、无重复 |
| 全部后端 Agent E2E | `PYTHONPATH=../docs/reviews:. .venv/bin/python -m pytest -m agent_e2e -q` | 126 passed，5 skipped |
| 工作台/授权/事项专项 | `PYTHONPATH=. .venv/bin/python -m pytest tests/test_procurement_workbench.py tests/test_procurement_case_access.py tests/test_procurement_case_service.py -q` | 49 passed |
| 后端全离线门禁 | `PYTHONPATH=../docs/reviews:. .venv/bin/python -m pytest -p operational_metric_offline_guard -m 'not integration and not agent_e2e_live' -q` | 1506 passed，110 deselected |
| 前端全量 | `npm test` | 28 files / 225 tests passed |
| 前端门禁 | `npm run typecheck`、`npm run lint`、`npm run build` | 全部通过 |

工作台专项组件/API 回归覆盖：本人和科室范围切换、切换科室使用独立 query、账号切换不复用旧结果、unknown 分母、覆盖失败与队列失败不伪装为 0、事项详情链接保留范围、已关闭事项从队列移除。新增真实责任快照读取异常回归，确认授权依赖故障使事项区 unavailable、counts 为 null、列表与游标清空。无期限和过期排序、优先级分组及稳定分页由隔离 PG 测试覆盖。

## 浏览器状态与边界

本地 Vite 页面打开后进入 `/login`。当前浏览器没有该项目的登录态，也没有已授权的采购员、经理、管理员及两科室隔离测试身份。Playwright CLI 的 Chrome 启动被桌面环境拒绝（`EPERM`）；没有修改用户浏览器或共享开发数据库。真实浏览器的身份切换、刷新、390px 实际布局、键盘访问、详情页操作和通知外发观测尚未执行。

因此 `ISS-20261022-024` 的产品实现和自动化/数据库行为已验证，但计划要求的真实浏览器矩阵仍待补，不把组件测试当作真实登录验收。

## 数据与业务边界

- 真实 PG 集成只连接显式的隔离数据库 `run_lifecycle_test`，测试清理仅限本次 fixture UUID；未向共享库 `sra`/`tianyancha` 写入队列验收数据。
- 没有配置正式名单或新的评分政策；未覆盖供应商继续按 unknown 展示。关闭接受限制事项不会清除未知覆盖。
- 没有通过真实采购案例测量工时、差错率或业务收益，报告不声称已有提效。

