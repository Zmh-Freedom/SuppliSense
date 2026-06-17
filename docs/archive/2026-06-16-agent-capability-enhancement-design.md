# Agent 能力增强设计文档

**日期：** 2026-06-16
**状态：** 已完成
**范围：** 三阶段渐进式增强 — 架构重构 → 新工具接入 → 智能提升

---

## 1. 背景与目标

当前 agent 有 15 个工具和 3 种执行模式（ReAct / Plan-Execute / Multi-Agent），但存在以下核心问题：

- **工具调用脆弱：** 使用 prompt 引导 LLM 输出 JSON，再用正则解析，频繁失败
- **流式输出虚假：** LLM 调用同步阻塞，"流式"是模拟的 10 字符分块
- **能力边界受限：** 报告生成、趋势分析、多企业对比、财务查询等后端服务已存在但未接入
- **智能不足：** 无法根据中间结果动态调整计划，无法主动预警，无法记住用户偏好

**目标：** 三阶段递进增强，使 agent 从"能用"提升到"好用、可靠、智能"。

---

## 2. 阶段一：架构重构

### 2.1 改用原生 Function Calling

**现状：** `agent.py` 中 `_try_parse()` 用正则从 LLM 输出中提取 JSON，解析失败时要求 LLM 重试格式。

**改为：** 使用 OpenAI SDK 的 `tools` 参数传入工具定义，LLM 通过 API 原生的 function calling 返回结构化调用。

**改动文件：** `backend/app/services/agent.py`

**关键改动：**

1. 将 `TOOLS` 字典中的工具描述转换为 OpenAI function calling 格式：
```python
def _build_tools_schema() -> list[dict]:
    """将注册工具转换为 OpenAI function calling schema。"""
    schemas = []
    for name, (fn, desc, params) in TOOLS.items():
        schemas.append({
            "type": "function",
            "function": {
                "name": name,
                "description": desc,
                "parameters": params,  # JSON Schema 格式
            }
        })
    return schemas
```

2. 修改工具注册装饰器，增加参数 schema：
```python
@_register(
    name="search_company",
    description="根据关键词搜索企业全称",
    parameters={
        "type": "object",
        "properties": {
            "keyword": {"type": "string", "description": "企业名称关键词"}
        },
        "required": ["keyword"]
    }
)
def search_company(keyword: str) -> str:
    ...
```

3. ReAct 循环改为处理 `tool_calls` 响应：
```python
response = client.chat.completions.create(
    model=settings.LLM_MODEL,
    messages=messages,
    tools=_build_tools_schema(),
    tool_choice="auto",
)

msg = response.choices[0].message
if msg.tool_calls:
    for tc in msg.tool_calls:
        result = await _execute_tool(tc.function.name, json.loads(tc.function.arguments))
        messages.append({"role": "tool", "tool_call_id": tc.id, "content": result})
elif msg.content:
    # 最终回答
    yield {"event": "answer", "data": msg.content}
```

4. 移除 `_try_parse()` 和所有 prompt-based JSON 格式引导。

### 2.2 真实 LLM 流式输出

**现状：** LLM 调用是同步的，`chat_stream()` 中的"流式"是把最终结果切成 10 字符一块模拟的。

**改为：** 使用 `stream=True` 参数，token 级实时输出。

**关键改动：**

```python
stream = client.chat.completions.create(
    model=settings.LLM_MODEL,
    messages=messages,
    tools=_build_tools_schema(),
    stream=True,
)

async for chunk in stream:
    delta = chunk.choices[0].delta
    if delta.tool_calls:
        # 累积 tool_call，不立即输出
        _accumulate_tool_call(delta.tool_calls)
    elif delta.content:
        # 逐 token 推送
        yield {"event": "answer_chunk", "data": {"text": delta.content}}
```

对于 function calling 响应，累积完整的 tool_call 后立即执行，对 answer 部分逐 token 推送。

### 2.3 ReAct 模式工具并行执行

**现状：** ReAct 循环中工具同步调用 `fn(**args)`。

**改为：** 当 LLM 在一次响应中返回多个 tool_calls 时，使用 `asyncio.gather()` 并行执行。

```python
if msg.tool_calls:
    tasks = [
        _execute_tool_async(tc.function.name, json.loads(tc.function.arguments))
        for tc in msg.tool_calls
    ]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    for tc, result in zip(msg.tool_calls, results):
        content = str(result) if not isinstance(result, Exception) else f"错误：{result}"
        messages.append({"role": "tool", "tool_call_id": tc.id, "content": content})
```

### 2.4 结构化错误恢复

**现状：** 工具调用失败时错误信息直接传回 LLM，无重试。

**改为：**
- 工具执行失败自动重试 1 次（指数退避，1s → 2s）
- 重试仍失败则返回结构化错误给 LLM，附带建议
- 连续 3 次工具调用失败则终止并返回错误报告

```python
async def _execute_tool_async(name: str, args: dict, retry: int = 0) -> str:
    try:
        fn = TOOLS[name].callable
        return await asyncio.to_thread(fn, **args)
    except Exception as e:
        if retry < 1:
            await asyncio.sleep(2 ** retry)
            return await _execute_tool_async(name, args, retry + 1)
        return json.dumps({
            "error": str(e),
            "suggestion": _get_error_suggestion(name, e),
        }, ensure_ascii=False)
```

### 2.5 工具结果大小管理

**现状：** 工具结果直接塞入对话，大结果可能超出上下文窗口。

**改为：**
- 每个工具定义 `max_tokens` 阈值（默认 2000）
- 超过阈值的结果自动截断 + 摘要
- `knowledge_search` 默认只返回 top-3 结果

```python
@dataclass
class ToolConfig:
    callable: Callable
    description: str
    parameters: dict
    max_result_tokens: int = 2000

async def _truncate_result(result: str, max_tokens: int) -> str:
    if _estimate_tokens(result) <= max_tokens:
        return result
    truncated = result[:max_tokens * 2]  # 粗略按字符截断
    summary = await _llm_summarize(truncated)
    return f"[结果过长，已摘要]\n{summary}"
```

---

## 3. 阶段二：新工具接入

### 3.1 报告生成

**已有服务：** `backend/app/services/report_service.py`

**注册为 agent 工具：**

```python
@_register(
    name="generate_report",
    description="生成企业风险评估报告（Excel 或 HTML 格式）",
    parameters={
        "type": "object",
        "properties": {
            "company_name": {"type": "string", "description": "企业全称"},
            "report_type": {"type": "string", "enum": ["excel", "html"], "default": "excel"},
            "sections": {"type": "array", "items": {"type": "string"}, "description": "报告包含的模块（可选）"}
        },
        "required": ["company_name"]
    }
)
def generate_report(company_name: str, report_type: str = "excel", sections: list[str] | None = None) -> str:
    ...
```

### 3.2 趋势分析

**已有服务：** `backend/app/api/trend.py` 对应的 service

**注册为 agent 工具：**

```python
@_register(
    name="analyze_trend",
    description="分析企业风险评分的历史趋势变化",
    parameters={
        "type": "object",
        "properties": {
            "company_name": {"type": "string", "description": "企业全称"},
            "period_months": {"type": "integer", "description": "分析周期（月）", "default": 6}
        },
        "required": ["company_name"]
    }
)
def analyze_trend(company_name: str, period_months: int = 6) -> str:
    ...
```

### 3.3 多企业对比

**已有服务：** `backend/app/api/compare.py` 对应的 service

**注册为 agent 工具：**

```python
@_register(
    name="compare_companies",
    description="对比多家企业的风险状况（支持多维度对比）",
    parameters={
        "type": "object",
        "properties": {
            "company_names": {"type": "array", "items": {"type": "string"}, "description": "企业名称列表"},
            "dimensions": {"type": "array", "items": {"type": "string"}, "description": "对比维度（可选）"}
        },
        "required": ["company_names"]
    }
)
def compare_companies(company_names: list[str], dimensions: list[str] | None = None) -> str:
    ...
```

### 3.4 财务数据查询

**已有服务：** `backend/app/services/financial_service.py`（目前仅在 `assess_risk` 内部使用）

**独立注册为 agent 工具：**

```python
@_register(
    name="query_financials",
    description="查询企业财务指标（资产负债率、净利润、营收等）",
    parameters={
        "type": "object",
        "properties": {
            "company_name": {"type": "string", "description": "企业全称"},
            "metrics": {"type": "array", "items": {"type": "string"}, "description": "指定指标（可选，默认返回全部）"}
        },
        "required": ["company_name"]
    }
)
def query_financials(company_name: str, metrics: list[str] | None = None) -> str:
    ...
```

### 3.5 定时报告

**新建服务：** `backend/app/services/scheduled_report.py`

**利用已有的 APScheduler（`scheduler.py`）：**

```python
@_register(
    name="manage_scheduled_report",
    description="管理定时报告任务（创建/查看/删除）",
    parameters={
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": ["create", "list", "delete"]},
            "company_names": {"type": "array", "items": {"type": "string"}, "description": "监控企业列表"},
            "cron": {"type": "string", "description": "定时表达式（如 weekly）"},
            "report_type": {"type": "string", "enum": ["excel", "html"], "default": "excel"}
        },
        "required": ["action"]
    }
)
def manage_scheduled_report(action: str, **kwargs) -> str:
    ...
```

报告生成后通过 WebSocket 推送通知到前端。

---

## 4. 阶段三：智能增强

### 4.1 动态重规划（Plan-Execute 模式）

**改动文件：** `backend/app/services/executor.py`

**现状：** 执行器按初始计划执行，无法根据中间结果调整。

**改为：**
- 在 `_execute_step()` 完成后检查结果是否需要追加步骤
- 需要重规划时，将已有结果 + 原始目标发回 planner
- 最多允许 2 次重规划

```python
async def execute_plan_stream(plan: dict, original_query: str):
    replan_count = 0
    step_idx = 0

    while step_idx < len(plan["steps"]):
        result = await _execute_step(plan["steps"][step_idx])

        # 检查是否需要重规划
        if _needs_replan(result) and replan_count < 2:
            new_steps = await replan(original_query, plan["steps"][:step_idx+1], result)
            plan["steps"] = plan["steps"][:step_idx+1] + new_steps
            replan_count += 1

        step_idx += 1
```

**重规划触发条件：**
- 工具返回列表结果（如 `get_watchlist` 返回 N 家企业），需要为每项执行后续步骤
- 工具返回意外结构（如空结果），需要调整后续步骤

### 4.2 主动预警触发

**新建服务：** `backend/app/services/alert_notifier.py`

**实现方式：**
- APScheduler 每小时执行一次预警检查
- 对监控列表中每家企业调用 `alert_service.detect_changes()`
- 风险评分变化 ≥10 分时，通过 WebSocket 推送通知
- 通知存储到 MongoDB `notifications` 集合

```python
async def check_and_notify():
    watchlist = alert_service.get_watchlist()
    for company in watchlist:
        changes = alert_service.detect_changes(company["name"])
        if changes and abs(changes.get("score_delta", 0)) >= 10:
            await ws_manager.broadcast({
                "type": "risk_alert",
                "company": company["name"],
                "detail": changes,
            })
```

### 4.3 多轮对话深度上下文

**改动文件：** `backend/app/services/agent.py`（对话历史加载部分）

**现状：** 保留最近 10 轮对话，无摘要压缩。

**改为：**
- 对话超过 8 轮时，用 LLM 对前 4 轮生成摘要
- 摘要作为 system message 注入，后 4 轮完整保留
- 关键实体（公司名、风险指标）从摘要中提取

```python
async def _build_context_messages(session_id: str) -> list[dict]:
    history = load_history(session_id, limit=20)
    if len(history) <= 16:  # 8 轮 = 16 条消息
        return history

    # 对前 8 条消息生成摘要
    early = history[:8]
    summary = await _llm_summarize_conversation(early)

    return [
        {"role": "system", "content": f"对话历史摘要：{summary}"},
        *history[8:],  # 最近 4 轮完整保留
    ]
```

### 4.4 自动追问澄清

**改动文件：** `backend/app/services/agent.py`（系统提示词 + 处理逻辑）

**现状：** 用户意图模糊时 agent 可能猜测或报错。

**改为：** 在系统提示词中增加澄清规则：

```
当用户意图不明确时，不要猜测，而是返回澄清问题：
- 缺少企业名称时："请问您想分析哪家公司？"
- 缺少分析维度时："您关注哪些方面？风险评分、财务指标、舆情、还是全部？"
- 缺少时间范围时："您想看最近多久的数据？"

返回格式：{"clarify": "您的澄清问题"}
```

在 `_process_response()` 中处理 `clarify` 类型响应，直接推送给用户。

### 4.5 对话记忆与偏好学习

**新建服务：** `backend/app/services/user_preference.py`

**数据模型：**
```python
class UserPreference(BaseModel):
    user_id: str
    focused_industries: list[str] = []     # 关注的行业
    preferred_metrics: list[str] = []      # 偏好的指标
    report_format: str = "excel"           # 报告格式偏好
    recent_companies: list[str] = []       # 最近查询的企业（最多 20 个）
    updated_at: datetime
```

**使用方式：**
- agent 在生成回答时，系统提示词注入用户偏好
- 用户查询企业后，自动更新 `recent_companies`
- 推荐替代供应商时，优先考虑用户关注的行业

---

## 5. 执行顺序

```
阶段一（架构重构）
  2.1 原生 function calling → 2.2 真实流式 → 2.3 并行执行 → 2.4 错误恢复 → 2.5 结果管理

阶段二（新工具） — 可并行
  3.1 报告生成 | 3.2 趋势分析 | 3.3 多企业对比 | 3.4 财务查询 | 3.5 定时报告

阶段三（智能增强） — 依赖阶段一
  4.1 动态重规划 → 4.2 主动预警 → 4.3 上下文管理 → 4.4 自动追问 → 4.5 偏好学习
```

## 6. 验证方式

- **阶段一完成后：** `chat/stream` 端点使用 function calling 调用工具，不再出现"格式错误，正在重试"
- **阶段二完成后：** 用户可以说"生成海康威视报告"、"对比海康和大华"、"查看海康最近半年趋势"
- **阶段三完成后：** 监控列表企业风险变化时自动推送预警；长对话不丢失上下文；模糊查询时 agent 追问

## 7. 预估工作量

| 阶段 | 任务数 | 预估会话数 |
|------|--------|-----------|
| 架构重构 | 5 | 3-4 |
| 新工具接入 | 5 | 2-3 |
| 智能增强 | 5 | 3-4 |
| **总计** | **15** | **8-11** |
