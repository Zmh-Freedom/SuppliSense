# 当前计划与实际代码对账

日期：2026-09-01  
对账对象：`docs/superpowers/plans/2026-08-27-sourcing-risk-feishu-plan.md`  
结论：该计划仍是唯一有效计划；以下记录以当前分支代码、测试和 CI 配置为准。

## 状态定义

- **已完成**：代码、定向测试和验收记录能够相互证明，计划应标记为 `[x]`。
- **部分完成**：主路径已存在，但仍缺少计划中的边界、真实验收或收口项。
- **待完成**：代码或自动化验收尚不能证明计划目标已经实现。

## Task 对账

| Task | 计划目标 | 实际代码/证据 | 对账结论 | 下一步 |
|---|---|---|---|---|
| 1 | 三表字段契约和 ID 规则 | `docs/integration/feishu-bitable-data-contract.md`、三类模板和 `feishu_bitable.py` 已存在 | 部分完成 | 继续校准字段别名、契约和适配器测试 |
| 2 | 飞书三表只读、分页、幂等、快照和回退 | `feishu_bitable.py`、`test_feishu_bitable.py`、供应商快照仓储和同步脚本已存在 | 部分完成 | 补齐批次、过期记录和部分失败的可观测性 |
| 3 | 本地/飞书优先，天眼查/联网补充，候选证据可追溯 | `discovery_service.py`、寻源图、候选引用和相关 Eval 已存在 | 部分完成 | 完善无结果、提供方失败和身份不确定的统一回执 |
| 4 | 多维风险、证据状态、人工确认加入监控 | Supervisor、风险/ESG/舆情/合规 Worker、证据校验和审批路径已存在 | 部分完成 | 保留风险历史版本并补齐持续监控闭环 |
| 5 | Agent 工作台状态、目标、来源、证据、Loop 和审批 | `SourcingRiskWorkbench` 已有 V2 Trace；`ChatView` 只有启发式 `AgentWorkflowPanel` | 待完成 | 本轮统一聊天 SSE 状态事件和工作台状态卡 |
| 6 | 当前范围 E2E 回归集并自动运行 | `test_agent_e2e_workflows.py` 2 条跨层回归；12 条离线 Eval 已存在；CI 已有 E2E 步骤 | 部分完成 | 统一标记、增加状态生命周期和边界场景，CI 保持独立门槛 |
| 7 | 文档、问题和计划收口 | `AGENTS.md`、问题日志、验收手册和当前计划已存在 | 部分完成 | 纳入本次对账文档，关闭本问题并回写计划状态 |
| 8 | 第四张交易快照只读同步和商务风险 P0 | 交易快照同步、synthetic 演示边界和商务风险卡已实现并有定向测试 | 已完成 | 仅保留后续真实数据联调 |
| 9 | 画像商务风险展示、定向测试和浏览器验收 | 画像卡、后端/前端定向测试和构建已通过；登录态浏览器验收记录仍单独待执行 | 部分完成 | 补做登录态浏览器验收并记录结果 |

## 本轮优先级

1. Task 5：让聊天型 Agent 工作台展示可验证的生命周期状态，不展示模型隐式推理。
2. Task 6：把现有跨层流程和离线多轮 Eval 统一纳入 `agent_e2e`，补足证据不足、审批边界和状态事件回归。
3. 回写 Task 5/6 的勾选、问题日志和验收文档，避免再次出现“代码已完成但计划仍显示未完成”或相反情况。

## 验收命令

```bash
cd backend
python -m pytest -m agent_e2e -v
python -m pytest -m "not integration and not agent_e2e" -q

cd ../frontend
npm run lint
npm test -- --run
npm run build
```

数据库集成测试仍遵守项目规则：PostgreSQL、MongoDB 或 Redis 任一不可用时立即停止集成测试并记录阻塞原因，不把环境阻塞判为代码失败。
