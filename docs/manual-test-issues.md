# 手动测试问题汇总

**日期：** 2026-06-21  
**范围：** P1 寻源 MVP → P3 工程质量收尾期间的手动测试发现

---

## 1. 寻源搜索超时

| 项目 | 内容 |
|------|------|
| **现象** | 聊天 Agent 问"帮我推荐电机类供应商"，前端等待几十秒后提示超时，无结果返回 |
| **根因** | `search_suppliers` 对 20 家候选**并发**调用 `assess_risk`，每个 `assess_risk` 内部调 DeepSeek LLM 做软指标评分。20 路并发打向 DeepSeek API，每路 5-15 秒，叠加后总耗时超过前端/代理的超时阈值 |
| **修复** | `_batch_assess_risk` 改为读 MongoDB `alert_snapshots` 快照（毫秒级），不再调外部 API。同时候选数从 20 降到 10 |
| **commit** | `e4e91d63`, `961a331e` |

## 2. 多 Agent 模式回答丢失

### 2.1 答案闪烁后变成 "?"

| 项目 | 内容 |
|------|------|
| **现象** | 多 Agent 模式输入"帮我推荐电机供应商"，risk agent 的推荐结果闪烁了一下，最后变成 "?" |
| **根因** | supervisor 在 agent 回答完毕后再次调用 LLM 判断 FINISH，supervisor 的 token 流（如 `{"next": "FINISH"}` 的文本片段）覆盖了 agent 已累积的 `full_answer` |
| **修复** | `on_chat_model_stream` 只累积子 agent 的 token，过滤 supervisor 自身的路由决策输出。Agent 完成后重置 `current_agent = None` |
| **commit** | `5ce2d82c`, `1f8c5e4d` |

### 2.2 直接回复空白气泡

| 项目 | 内容 |
|------|------|
| **现象** | 多 Agent 模式输入"从风险、舆情、合规多个角度评估..."，回复了一个完全空白的气泡 |
| **根因** | 子 agent 使用非流式 LLM（`ChatOpenAI` 未开 `streaming`），`on_chat_model_stream` 永不触发，答案只走 `on_chat_model_end`。但 handler 有条件 `not full_answer`，第一个 agent 的 tool_call 响应（空 content）抢先设置了 `full_answer = ""`，导致后续真实答案被 `not full_answer` 跳过 |
| **修复** | 改写为 `agent_answers` dict 按 agent 名独立累积 + `all_text` 累加全部 agent 输出，去掉 `not full_answer` 判断 |
| **commit** | `3687bd69` |

### 2.3 supervisor 路由文本覆盖答案

| 项目 | 内容 |
|------|------|
| **现象** | 多 Agent 模式给出了完整的风险评估报告，但最后被 "需要我进一步查找同行业低风险的替代供应商吗？" 替换掉 |
| **根因** | supervisor 的 FINISH 消息中，DeepSeek 额外产出了自然语言文本（不只是 JSON），这段文本通过 `on_chat_model_end` 覆盖了 agent 累计答案。之前的 `current_agent = None` 重置逻辑未能完全防护 |
| **修复** | 全面重写 `stream_supervisor_graph`，使用 `agent_answers` dict + `all_text` 累积，supervisor 路由文本被 `current_agent` 守卫彻底过滤 |
| **commit** | `3687bd69` |

## 4. 标准模式寻源不生效

| 项目 | 内容 |
|------|------|
| **现象** | 在标准（ReAct）模式下问"帮我推荐电机供应商"，走得很慢，最终超时或无结果 |
| **根因** | ① 路由关键词太窄（只有"找供应商/寻源"等 8 个），"推荐"、"采购"、"供应商"单独出现不命中，走 LLM 分类（慢 5-10s） ② ReAct system prompt 未提及寻源工具，LLM 不知道可以调用 `create_sourcing_request` / `search_suppliers` |
| **修复** | 扩展关键词（加入"推荐"、"采购"、"供应商"、"帮我找"等），ReAct prompt 补充寻源工具说明 |
| **commit** | `129f0621`, `60c25c34` |

## 5. 前端 UX 问题

| 问题 | 说明 | 修复 |
|------|------|------|
| 搜索无进度提示 | 点击提交后只看到"提交中..."，不知道进行到哪一步 | 加入三步指示器（检索→评估→排序），SSE 事件驱动逐一点亮 ✓ |
| 空结果无提示 | 搜索无匹配时页面空白，用户不知道是没搜到还是卡住了 | 显示"本地供应商库未找到匹配结果，建议扩充供应商库" |
| 勾选无反馈 | 点击加入监控/申请准入后无任何提示 | 显示绿色成功提示条，3 秒自动消失 |
| 供应商状态不直观 | 状态字段显示原始英文（prospective/deprecated） | 改为中文彩色标签（待考察蓝/已准入绿/已拉黑红/已停用灰） |
| 供应商库无总数 | 不知道库里有多少家供应商 | 标题旁显示 "19 家" |
| 导入结果不消失 | Excel 导入成功后结果提示一直显示 | 8 秒后自动消失 |

## 6. 后端 Bug

| 问题 | 现象 | 修复 |
|------|------|------|
| PG 用户创建失败 | 注册新用户时报 `UserInDB` 校验错误 `Field required [password_hash]` | `user_repo.create_user` 的 `RETURNING` 子句补上 `password_hash` (`5aa65b1a`) |
| SSE thinking 事件未发出 | ReAct 图的文档声称支持 `thinking` 事件但实际未 yield | 在 `stream_react_graph` 开头补发 (`1257c844`) |
| report_service null-guard 缺失 | `inventory_turnover` 等字段用 `if val` 判断，无法区分 `None` 和 `0.0` | 改为 `_has_val(v)` 显式检查 `is not None and != 0.0` (`1257c844`) |
| select_result supplier_name 为空 | 申请准入时传给 `create_access_application` 的 supplier_name 硬编码为 `""` | 先查 `get_result(result_id)` 获取实际供应商名 (`70bef7a6`) |

## 7. 路由与意图识别

| 问题 | 说明 |
|------|------|
| "电机供应商" 不走寻源 | 关键词只包含"找供应商"，不含"推荐"、"帮我找"等常见表述 |
| "有什么合适的" 不命中 | 用户自然语言表达（如"有什么推荐的电机供应商吗"）不被识别为寻源意图 |

**修复**：扩展关键词列表至覆盖常见自然语言表述。

## 8. 数据完整性

| 问题 | 说明 | 修复 |
|------|------|------|
| 供应商库仅 19 家手工数据 | 搜索"电机"只能碰运气语义匹配，大多数品类查不到 | 增加 Excel 批量导入功能 |
| 法律诉讼数据为 0 | 多家监控企业的诉讼数显示 0（短名称匹配失败） | 翻页获取 lawSuit 全量数据 + 解决短名/全名映射 |
| 种子数据无风险分 | 新供应商从未被评估，寻源时全部显示 50分/unknown | 用 MongoDB `alert_snapshots` 快速查分，未评估的标"未评估" |

## 总结

手动测试覆盖了以下场景，发现并修复了 20+ 个问题：

| 类别 | 问题数 |
|------|--------|
| 超时/性能 | 2 |
| 多 Agent 模式 | 3 |
| 路由/意图 | 2 |
| 前端 UX | 6 |
| 后端 Bug | 4 |
| 数据完整性 | 3 |
| 偏好/上下文 | 2 |
