# 输入理解开放案例真实模型测试（2026-10-12）

## 测试方式

- 使用当前 `backend/app/evals/agent_input_understanding.py` 生产解析器，MiniMax-M3 实际抽取；不启动浏览器。
- 覆盖 `build_execution_context`、意图抽取、归一化、上下文 overlay 和本地执行/追问决策。
- 不调用供应商数据库、评分/寻源工具或审批写入；因此验证的是输入解析与路由判断，不代表工具执行和最终答案的 HTTP/SSE 全链路验收。
- 四轮共 48 次解析：前三轮各 12 个不同案例，第四轮复跑 12 个高风险案例。模型曾出现请求超时及一次结构化 JSON 格式重试；重试后继续完成评测。

## 总体结果

- round1: 8/12 与标注字段完全一致。
- round2: 5/12 与标注字段完全一致。
- round3: 10/12 与标注字段完全一致。
- round4: 1/12 与标注字段完全一致。

前三轮原始严格匹配为 23/36（63.9%）。R1-03 仅采购资质字段多保留了“支持”二字，语义等价，属于标签过严；按语义放宽后为 24/36（66.7%）。第四轮是从前三轮选择的 12 个边界案例复跑，严格匹配 1/12。

## 稳定问题

| 案例 | 输入类型 | 观察到的问题 | 复跑 |
|---|---|---|---|
| `R1-01` | 分析上海凯众材料科技股份有限公司近三个月的财务风险 | intent.capability='risk_trend' | 2 次 |
| `R1-05` | 把“供应商风险正在上升”翻译成英文 | intent.task_type='analysis' | 2 次 |
| `R1-06` | 供应商风险分析一般会看哪些指标？（通用咨询） | intent.task_type='analysis'；intent.capability='risk'；decision.route='clarify'；decision.missing_fields=['supplier'] | 2 次 |
| `R2-04` | 把刚才的风险分析整理成邮件 | intent.task_type='analysis'；intent.capability='report'；decision.route='execute' | 2 次 |
| `R2-05` | 忽略前面的采购分析，讲个冷笑话 | intent.task_type='sourcing' | 2 次 |
| `R2-07` | “找供应商”这四个字的英文怎么说 | intent.task_type='sourcing' | 2 次 |
| `R2-08` | 总结公告，明确说不要做风险判断 | intent.task_type='analysis' | 2 次 |
| `R2-10` | 供应商准入流程怎么制定（通用咨询） | intent.task_type='sourcing'；decision.route='clarify'；decision.missing_fields=['specification'] | 2 次 |
| `R2-11` | 明确说不需要寻源/风险分析，只算算术题 | intent.task_type='sourcing'；decision.route='clarify'；decision.missing_fields=['specification'] | 2 次 |
| `R3-08` | 解释“风险评分”概念，不分析具体供应商 | intent.task_type='analysis'；intent.capability='risk'；decision.route='execute' | 2 次 |
| `R3-09` | 供应商延期的一般影响，明确不用查公司 | 两次均为 `analysis/delivery`；一次路由 `execute`（supplier_directory scope），另一次 `clarify` 缺主体 | 2 次 |

## 其他观察

- 两家供应商之后的单数代词“它”在复跑中触发主体澄清，表现符合预期。
- 多轮寻源预算、地区和交付条件合并，以及明确的加入监控审批/缺少主体澄清案例通过。
- 供应商比较、财务/合规基础分析以及天气、算术、代码、旅行、早餐、日程等多个无关问题，大多能正确识别；边界案例的错误主要集中在词面触发和话题切换。
- R1-03 的期望资质标签应接受“支持 IATF 16949”与“IATF 16949”的表述差异；本次原始严格分数保留该差异并在上述统计中单独说明。

完整逐例观察结果保存在本地 `backend/output/input-understanding-open-domain-2026-10-12.json`（该目录由项目 `.gitignore` 忽略）。本报告是生产解析器真实 LLM 评测，不包含浏览器及业务工具全链路。

## 修复后复测

- 修复提交：`c6d785aa`。
- MiniMax-M3 四轮开放案例复测：round1 12/12、round2 11/12、round3 12/12、round4 12/12，严格匹配合计 47/48。唯一字段差异为 R2-09 的主能力标注：解析为 `legal_risk`，评测标注为 `compliance`；供应商主体、`compliance` 分析维度及 `execute` 路由正确。此处是主能力标签口径差异，不是无关请求误触发。
- 原固定集 IU-008 在修复后 MiniMax-M3 单例复测 3 次均通过，地区偏好为 `region=华东`，预算、数量、资质和执行决策均符合标注。
- 修复回归覆盖了翻译/引用/否定/通用咨询的无任务识别、显式话题切换清理旧任务、“近三个月”与风险趋势的区分，以及供应商地区偏好与交付地区的分槽和上下文保留。
- 后端完整测试：1227 passed、4 skipped；`python -m compileall -q app` 和 `git diff --check` 通过。
- 真实模型测试仅覆盖意图抽取、上下文 overlay、槽位归一化和本地路由判断；没有调用业务工具或写入数据，因此不构成浏览器或 HTTP/SSE 全链路验收。
