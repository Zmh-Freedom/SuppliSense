# Task 1 完成报告

## 改动文件

- `backend/app/domains/agent_run/__init__.py`：领域包导出。
- `backend/app/domains/agent_run/models.py`：运行状态、候选来源/状态、动作提案状态及允许转移表。
- `backend/app/domains/agent_run/schemas.py`：创建、澄清、审批请求，Run 响应和事件响应的深度不可变 Pydantic 契约。
- `backend/tests/test_agent_run_models.py`：枚举、状态机、字段校验、版本约束、事件字段和深度不可变性测试。

未实现 repo、API、service、图或数据库。

## 测试命令及结果

- `cd backend && pytest tests/test_agent_run_models.py -v`：`9 passed`；存在项目既有的 FastAPI/httpx 弃用警告。
- `cd backend && python -m compileall -q app/domains/agent_run`：通过。
- `git diff --check`：通过。

## TDD red/green 证据

- RED：先创建测试后运行 `pytest tests/test_agent_run_models.py -v`，收集阶段按预期失败，错误为 `ModuleNotFoundError: No module named 'app.domains.agent_run'`。
- GREEN（原始实现）：补充最小领域包和契约后重新运行同一 focused test，结果为 `7 passed`。
- 本次修复 RED：先新增两个嵌套 mutation 测试，运行 focused test 得到 `2 failed, 7 passed`；失败原因为浅层 `frozen=True` 未阻止嵌套 dict/list 原地修改。
- 本次修复 GREEN：增加递归冻结容器后运行 focused test，结果为 `9 passed`。

## 设计取舍或遗留风险

- 使用 `ConfigDict(frozen=True)` 保护模型字段，并用递归冻结的 dict/list 子类保护 `answers`、`data`、`candidates`；这些容器仍保持 dict/list 的读取和序列化形状，兼容后续使用。
- `ALLOWED_STATUS_TRANSITIONS` 为每个终态显式提供空集合；当前状态机只定义 Task 1 所需的契约边界，不包含任何持久化或执行逻辑。
- `AgentRunResponse` 的候选结果采用 `list[dict[str, Any]]`，以保持本任务不提前引入 repo/service 层细节；后续任务可在明确候选契约后收紧类型。运行时输入会递归冻结，但仍以 list/dict 形状暴露。
- 事件 `event_id` 使用正整数，`version` 使用从 1 开始的正整数；实际单调递增和乐观锁由后续 repo/service 负责。
