# Phase 2A 采购事项闭环验收记录

日期：2026-10-23
状态：代码、自动化回归和独立 PostgreSQL 验收完成；真实三角色浏览器验收仍待授权隔离测试身份。

## 本批结果

2A 已贯通事项创建、认领/指派、回执、延期与恢复、技术复核任务关联、独立关闭审核及前端列表/详情操作。事项关闭继续与技术任务终态、风险状态和正式证据分离。

复核发现并修复了两处授权/关闭边界：

- `coverage_gap` 创建事件保存当时缺口维度与证据基线。resolved 申请和审批都要求当前覆盖完整、引用原缺口维度的新有效证据、来源采集时间晚于创建基线且不晚于引用该证据的处置回执。无基线的历史事项不能以 resolved 关闭。
- 未关联历史任务的 requester 例外限于采购角色；viewer 无论是否为历史发起人都不能读、确认或执行。当前范围经理可从任务列表发现并处理其有权访问的未关联任务，撤权后即时拒绝。

## 验证结果

| 范围 | 命令 | 结果 |
| --- | --- | --- |
| 后端离线套件 | `PYTHONPATH=../docs/reviews:. .venv/bin/python -m pytest -p operational_metric_offline_guard -m 'not integration and not agent_e2e_live' -q` | 1506 passed，110 deselected；包含完整离线 Agent E2E，且包含第二阶段收尾授权故障回归 |
| Agent E2E 全集 | `PYTHONPATH=../docs/reviews:. .venv/bin/python -m pytest -m agent_e2e -q` | 126 passed，5 skipped；live 用例和单独运行的 integration 用例安全跳过 |
| 事项/任务 PostgreSQL 集成 | `SUPPLISENSE_TEST_PG_DB=run_lifecycle_test PYTHONPATH=. .venv/bin/python -m pytest tests/test_procurement_case_repo.py tests/test_monitor_review_tasks.py -m integration -q` | 6 passed，25 deselected；独立 PG，覆盖并发/回滚/DDL 重复执行/任务去重 |
| 前端 | `npm test` | 225 passed，28 files |
| 前端静态与生产构建 | `npm run typecheck`、`npm run lint`、`npm run build` | 全部通过 |
| Python 编译与差异检查 | `python -m compileall ...`、`git diff --check` | 通过 |

后端套件有 3 条既有依赖弃用警告；Vitest 报告 Node localStorage 实验性警告。当前环境未安装 `ruff`，未运行该工具。

## 尚未覆盖

- PostgreSQL 集成现已在显式隔离库 `run_lifecycle_test` 完成；测试 fixture 使用测试 UUID 并管理清理自身事项记录，没有把共享开发库作为可清空测试库。
- 浏览器端真实三角色/两科室、Feishu 当前身份、调岗撤权、关闭自批拒绝、搁置到期、刷新及 390px 验收未完成。本地页面停在登录页，缺少已授权隔离测试身份；Playwright 启动 Chrome 遇到桌面环境 `EPERM`。本记录不声称这些场景已通过。
- `ISS-20261023-014` 的真实覆盖源验收和 `ISS-20261023-015` 的真实经理身份/撤权链路仍待隔离环境；`ISS-20261023-017` 的独立 PostgreSQL 并发验收已通过。

## 问题状态

- ISS-20261023-014：实现已修复，待真实覆盖数据验收。
- ISS-20261023-015：实现已修复，待授权隔离账号的浏览器验收。
- ISS-20261023-016、018、019：离线与组件回归通过，已关闭。
- ISS-20261023-017：独立 PostgreSQL 并发验收通过，已关闭。

## 关联提交

- `603f6671` capabilities/scope；`7f77a5aa` 持久化与幂等；`17d3a489` API/生命周期；
  `95e59429` 页面流程；`a716ca25` 复核修复、回归与 issue 记录。
- `c7813ec7`、`c907aa01` 更新服务进度和 issue 记录。
