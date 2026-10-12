# 聊天后端全链路验收（2026-10-12）

## 验收范围

主路径：

```text
POST /api/v1/chat/stream
  → 会话上下文加载
  → 输入意图抽取与归一化
  → 确定性澄清/权限预检
  → Harness 规划
  → ToolExecutor 工具调用
  → 证据校验与回答
  → SSE agent_answer / done
```

本次新增的固定输入测试使用 FastAPI `TestClient`，保留真实 API 路由、上下文解析、Harness 图、计划生成、ToolExecutor、证据校验及 SSE 序列。LLM 结构化响应和业务数据工具由固定夹具提供，测试不依赖外部数据库、模型 API，不执行业务写操作。

## 固定案例结果

执行：`cd backend && python -m pytest tests/test_agent_chat_full_chain.py -q`

| 请求 | 预期行为 | 验收结果 |
|---|---|---|
| 点名供应商并分析财务风险 | 调用 `query_financials`，返回有证据支持的完成态 | 通过；工具被调用一次，Claim 标记为 `supported` |
| 有完整约束的工业相机寻源 | 调用 `discover_supplier_candidates`，参数保留地区、预算和数量 | 通过；`region=华东`，未误写成交付地区 |
| 供应商风险通用指标咨询 | 不要求供应商主体，不调用业务工具 | 通过路由验收；无澄清和工具调用 |
| 翻译含供应商风险的句子 | 不调用供应商分析工具 | 通过；无澄清和工具调用 |
| 否定寻源/风险请求并提出算术题 | 不调用业务工具 | 通过；无澄清和工具调用 |
| 明确切换到冷笑话 | 不继承旧采购任务，不调用业务工具 | 通过；无澄清和工具调用 |
| 请求分析“这家供应商”但无主体上下文 | 发出主体澄清，不调用业务工具 | 通过 |

结果：**7 passed**。新增 API 预检修复提交为 `c74e5348`。

## 真实 MiniMax API 冒烟

通过同一个 `/api/v1/chat/stream` 路由运行 3 条真实模型输入；测试只替换供应商数据工具，不写业务数据：

- 明确财务分析：HTTP 200；SSE 顺序包含 `tool_call`、`tool_result`、`agent_answer`、`done`；调用 `query_financials` 一次；最终状态 `completed`。
- 完整约束寻源：HTTP 200；调用 `discover_supplier_candidates` 一次；传入工业相机、200 万像素/GigE、华东、50 万元、IATF 16949、5 家等条件；最终状态 `completed`。
- 通用风险指标咨询：HTTP 200；没有澄清事件，也没有业务工具调用；最终状态 `needs_review`，回答仍是“暂未识别出可执行的分析任务”。

因此本次验证确认了业务工具路由边界，但通用知识咨询目前没有实质回答。该问题已记录为 `ISS-20261012-023`，仍需单独修复和验收。

## 回归与结论

- `cd backend && python -m pytest -q`：**1234 passed，4 skipped**。
- `cd backend && python -m pytest -m agent_e2e -q`：**100 passed，4 skipped，1134 deselected**。
- `python -m compileall -q app`：通过。
- `git diff --check`：通过。
- 全量后端回归包含现有 Harness 生产运行时、持久化工具调用与 API/SSE 集成用例；新增长链路测试则专门覆盖“输入解析到执行工具”之间的接缝。

本次验收关闭了 API 关键词式澄清覆盖通用咨询解析结果的问题（`ISS-20261012-022`）。由于通用咨询还没有形成有帮助的回答，整体全链路验收为**核心分析/寻源路径通过，通用咨询回答质量待修复**。
