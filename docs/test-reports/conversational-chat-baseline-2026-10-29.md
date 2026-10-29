# 聊天评测基线（2026-10-29）

## 范围与判定方式

本基线覆盖纯对话、业务/混合输入、输入缺失、来源失败与未找到、寻源约束、多轮上下文恢复及审批。离线 API/SSE 回归固定意图模型输出和业务工具结果，验证现有 `/api/v1/chat/stream`、Harness、ToolExecutor、证据及 SSE 契约；实时评测只调用当前配置的真实模型，业务候选工具返回合成的只读夹具。真实模型结果不代表真实供应商数据质量。

评测数据集为 [`sourcing_chat_acceptance_cases.jsonl`](../../backend/tests/evals/sourcing_chat_acceptance_cases.jsonl)，固定 6 个寻源案例。评测器按路由/澄清、工具名称及次数、约束字段、候选结果类型和最终答案要点判定，不以 HTTP 200 单独算通过。

## 场景矩阵

| 场景 | 回归入口 | 断言重点 | 本轮证据 |
|---|---|---|---|
| 问候、感谢、能力介绍 | `test_chat_pure_conversation_returns_natural_completed_answer_without_business_tools` | 完成态、无澄清、无业务工具 | 离线全链路套件通过，3 种输入 |
| 业务与混合输入 | `test_chat_mixed_greeting_and_financial_request_still_runs_business_tool` | 问候不遮蔽业务请求，财务工具只调用一次 | 离线全链路套件通过 |
| 一般咨询、翻译及话题切换 | `test_chat_general_supplier_question_returns_without_business_tool_or_supplier_clarification` 等 | 不误入供应商澄清或业务工具；通用回答服从模型调用预算 | 离线全链路套件通过 |
| 缺少供应商主体 | `test_chat_missing_supplier_target_clarifies_without_calling_business_tools` | 发出主体澄清，不调用业务工具 | 离线全链路套件通过 |
| 来源失败/不可用/未找到 | `test_chat_financial_provider_failure_is_distinct_from_no_result` | `failed`、`unavailable`、`not_found` 保持独立；不产生财务 Claim，答案保留对应限制 | 离线全链路套件通过；`not_found` 表示本轮未找到可验证指标，不表示已确认来源完整且为空 |
| 有约束及无候选寻源 | `test_chat_sourcing_request_passes_normalized_constraints_to_tool_executor`、`test_chat_sourcing_request_reports_no_candidates_without_supplier_clarification` | 类别、地区、交付地区、预算、规格、资质、数量和候选数进入正确字段；仅在明确候选查询结果时说本轮无匹配候选 | 离线全链路套件通过；候选由合成夹具提供 |
| 显式寻源但模型缺结构化槽位 | `test_explicit_sourcing_without_model_slots_binds_requirement_and_plans_search` | 在模型只输出 sourcing 能力/任务而缺少 `sourcing_requirement` 时，从明确文本恢复类别、数量、规格、偏好地区并生成只读寻源计划 | 离线红绿回归通过；真实模型 SCA-005 定向复验通过 |
| 追问及上下文恢复 | `test_clarification_persists_confirmed_slots_to_postgres_state`、`test_chat_follow_up_restores_supplier_context_from_postgres` | 已确认槽位进入 PostgreSQL 状态；下一轮恢复供应商上下文 | API 合约测试通过；隔离 PostgreSQL 两轮集成测试通过 |
| 审批确认/拒绝 | `test_persistent_supervisor_pause_restart_and_resume_keeps_run_and_results[approve/reject]` | 保持暂停、恢复和决策状态；外部写执行器在验收中为替身 | 独立 PostgreSQL `run_lifecycle_test` 两种决策均通过；没有真实业务写入 |

## 结果

### 离线 API/SSE 与回归

- `tests/test_agent_intent_extractor.py tests/test_agent_harness_task14.py tests/test_agent_input_understanding_rules.py`：**208 passed, 1 warning**。覆盖确定性寻源 fallback、意图/槽位解析和 Harness 规划；没有真实模型、数据库、Redis 或供应商服务调用。
- `tests/test_agent_chat_full_chain.py tests/test_agent_chat_context_contract_e2e.py`：**22 passed, 3 warnings**。通过 TestClient 保留活动 API/SSE 路径；模型结构化结果、业务数据和会话边界由固定夹具控制。
- 隔离 PostgreSQL 两轮上下文恢复：**1 passed**。使用独立 `run_lifecycle_test` 数据库及随机用户/会话 ID；仅删除本测试创建的记录。
- 隔离 PostgreSQL 审批确认/拒绝：**2 passed**。数据库为 `run_lifecycle_test`，拒绝路径不产生执行结果；审批测试的外部写执行器由替身提供。

以上测试的通过结果可以作为当前代码的离线回归基线；它们不等同真实浏览器登录后的交互验收。

### 真实模型 API/SSE 寻源评测

| 阶段 | 模型/用例 | 结果 | 可得结论 |
|---|---|---:|---|
| 切换到新配置后的完整初始轮 | `deepseek-v4.1-flash-1017`，SCA-001 至 SCA-006 | **4/6**；SCA-004、SCA-005 失败 | SCA-004 没有发出预期品类澄清；SCA-005 返回空计划且没有工具调用。失败保留在 `/tmp/chat-eval-baseline-new-model-live.json`，并已记入 ISS-20261029-025。 |
| 确定性 fallback 修复后 | SCA-005，1 例 | **0/1** | trace 实际已识别 `capability=sourcing` 和 `task_type=sourcing`，但没有 `sourcing_requirement`，仍未规划工具；这推翻了“有效 no-task 早退”的初始假设。记录保存在 `/tmp/chat-eval-baseline-sca005-postfix-20261029.json`。 |
| 显式需求绑定修复后 | SCA-005，1 例 | **1/1** | 真实 `/api/v1/chat/stream` HTTP 200，17 个 SSE 事件，恰好一次 `discover_supplier_candidates`；类别、候选数、规格、偏好地区均匹配。记录保存在 `/tmp/chat-eval-baseline-sca005-requirement-fix-20261029.json`。 |
| SCA-004 有界定向复测 | SCA-004，1 例 | **1/1** | 一次复测通过，未证明稳定复现或稳定修复，因此保留初始失败并标作非确定性观察；不据此改代码或提高 token 上限。记录保存在 `/tmp/chat-eval-baseline-sca004-current-700.json`。 |

真实模型测试运行前断言了模型 ID 和隔离范围：PostgreSQL/MongoDB `run_lifecycle_test`，Redis DB 15。SCA-005 的会话加载/保存和候选检索均为隔离替身；本轮候选替身返回“当前没有匹配候选”。这验证模型输入理解到活动 API/SSE 和只读工具计划的边界，不证明真实候选源返回空，也没有访问生产供应商数据。完整 6 例没有在修复后重跑。

历史 MiniMax 结果与本轮模型和配置不同，不并入新模型基线。

### 浏览器与服务

- 验收期间 SuppliSense 前端 `127.0.0.1:5174`、后端 `127.0.0.1:8000` 均在运行；首页、`/health/live` 和 `/health/ready` 返回 HTTP 200。未重启或停止服务。
- Playwright 打开真实前端后落在 `/login`，页面显示登录表单且没有已登录会话。没有安全可用的认证态，因此本轮没有登录后发送消息或检查聊天卡片；本次只记录入口和健康检查，不计为浏览器聊天验收。没有访问 `5173`。

## 可复跑入口

离线固定输入回归：

```bash
cd backend
python -m pytest -q \
  tests/test_agent_intent_extractor.py \
  tests/test_agent_harness_task14.py \
  tests/test_agent_input_understanding_rules.py \
  tests/test_agent_chat_full_chain.py \
  tests/test_agent_chat_context_contract_e2e.py
```

真实模型评测通过同一 `tests/test_agent_sourcing_chat_live.py` 选择用例运行。评测进程必须安全加载当前批准的模型配置，并在启动前显式覆盖与断言 PostgreSQL/MongoDB=`run_lifecycle_test`、Redis DB=15；保留的历史 opt-in 开关名为 `RUN_MINIMAX_SOURCING_ACCEPTANCE`，虽然该用例按运行时 `settings.LLM_MODEL` 工作，本轮实际模型是 `deepseek-v4.1-flash-1017`。指定 `SOURCING_CHAT_LIVE_CASE_IDS` 可做有界单例复测，`SOURCING_CHAT_LIVE_REPORT_PATH` 可保存 JSON 轨迹。禁止打印 API key 或将共享开发库设为测试目标。

## LangChain 组件决策

**当前不新增 LangChain/LangGraph 组件或依赖。** 代码已使用 LangGraph `StateGraph`、`interrupt`/`Command` 与 PostgreSQL checkpoint，以及 `langchain-core` 工具抽象；活跃聊天仍由唯一 Harness 控制，Session/Turn/Run/Task 和审批事实仍以项目 PostgreSQL 为准。当前发现的问题分别落在显式寻源槽位恢复和证据状态表达，评测没有显示缺少新执行内核或 memory abstraction。

官方 LangGraph 文档把 checkpointer 定位为线程级图状态、会话连续性和可恢复人工介入的能力；其 interrupt 要求持久 checkpointer、thread ID，并以 `Command(resume=...)` 继续运行，和项目已有机制相吻合（[Persistence](https://docs.langchain.com/oss/python/langgraph/persistence)、[Interrupts](https://docs.langchain.com/oss/python/langgraph/interrupts)）。引入新组件不会替代项目已有的一次上下文快照、证据校验、审批执行策略或状态事实源。

LangSmith 的离线/在线评测、数据集与实验对长期比较有价值，但它需要独立服务/API key 和数据集/追踪配置；当前本地固定夹具、红绿回归和有界真实 API/SSE 评测可以覆盖眼下的基线。现阶段不把提示词、对话或业务轨迹外送到新服务；若后续确需团队级实验仪表盘或线上评估，再单独评估其隐私、数据最小化和运营成本（[LangSmith Evaluation](https://docs.langchain.com/langsmith/evaluation)）。

因此保留现有 LangGraph Harness、工具注册和 PostgreSQL 上下文/审批契约，以当前基线持续捕获失败样例；只有出现本地评测无法满足的明确需求时才重新评估新增组件。

## 关联问题记录

- [`ISS-20261029-022`](../issue-log.md)：区分财务来源查询失败、不可用和本轮未找到可验证指标。
- [`ISS-20261029-023`](../issue-log.md)：修复并验证隔离 PostgreSQL 的两轮上下文测试夹具。
- [`ISS-20261029-024`](../issue-log.md)：记录意外共享 Mongo 写入，原记录保留不清理，并收紧后续评测隔离断言。
- [`ISS-20261029-025`](../issue-log.md)：记录新模型评测失败、确定性 fallback 与结构化寻源需求绑定修复、保留的失败和定向真实复验。
