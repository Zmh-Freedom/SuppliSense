# Phase 2B 寻源连续性验收

日期：2026-10-23
计划：`docs/superpowers/plans/2026-10-23-phase2b-sourcing-continuity.md` Task 5
灰度前置状态：只读快照为 `active` / `default`；本次未修改灰度配置。

## 本阶段完成内容

- 统一历史跨 Agent Run 和兼容寻源请求；历史条目可回到原来源和对应详情。
- `PARTIAL` / `NEEDS_REVIEW` 终态页面可读取候选处置，由用户明确选择暂存候选派生后续 Run。排除候选需要先填写理由并显式改为暂存。父 Run 保持终态。
- 后端 follow-up 的 `resume_url` 使用工作台识别的 `run` 查询参数。
- 寻源交接执行时，证据新鲜度窗口根据 provider 证据维度映射到策略评分维度；归一化和交接重验共享该映射。
- 连续旅程 Agent E2E 保留生产候选访问授权判断，并验证创建交接和恢复会话两个授权阶段。

## 自动化验证

在 `backend/` 执行：

- `PYTHONPATH=. .venv/bin/python -m pytest -v -m "not integration and not agent_e2e"`：1372 passed，1 skipped，229 deselected。
- `PYTHONPATH=. .venv/bin/python -m pytest -v -m "agent_e2e and not integration"`：121 passed，1481 deselected。
- 设置 `SUPPLISENSE_TEST_PG_DB=run_lifecycle_test` 后运行批次租约恢复/取消安全和 PostgreSQL checkpointer 重启用例：2 passed。测试仅使用独立测试库，没有向共享开发库写入。

在 `frontend/` 执行：

- `npm run test -- --run`：27 个测试文件、217 项通过。
- `npm run lint`、`npm run typecheck`、`npm run build`：全部通过。

## 浏览器验收

使用本机 Vite 页面和固定的本地 API fixture，在 390×844 视口完成：

1. 两类历史来源均可显示并打开；旧寻源详情保留兼容任务元数据。
2. 从 `PARTIAL` 任务选中暂存候选创建 follow-up，进入子 Run；后退、刷新可分别恢复父任务和统一历史。
3. 候选交接恢复聊天；篡改 handoff ID 和撤权后的 handoff 均显示不可读取状态。
4. 监控意图在 `approval_required` 后显示人工审批卡片。fixture 记录到一次交接创建和一次聊天流请求，没有发生监控写入。
5. 已检查历史、续接和交接页面的 `clientWidth` 与 `scrollWidth` 均为 390，无横向溢出。

浏览器使用隔离的本地 API fixture，不代表生产数据库或外部供应商服务上的浏览器验收；持久恢复由上面的独立 PostgreSQL 用例覆盖。

## 独立复审

独立只读复审确认 E2E 保留生产 `can_access_handoff_candidate`，并断言创建与恢复阶段都执行授权检查；终态候选续接 UI 与 `run` 路由契约有测试覆盖。复审未发现可行动的重要缺陷或安全问题，未修改代码。

Task 5 实现提交：`test: verify sourcing continuity across pages`。关联提交 SHA 将在提交后补入本报告和 `docs/issue-log.md`。
