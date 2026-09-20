# 显式舆情与供应链能力路由修复方案

## 背景与结论

关联问题：`ISS-20260920-001`。

以下两类单主体只读问题，当前可能被模型抽取为通用 `risk`，从而令最终摘要错误地以“综合风险分析”为主回答：

- `分析青岛三祥科技股份有限公司的最新舆情和新闻动态`
- `分析青岛三祥科技股份有限公司的供应链关系和传染风险`

这不是工具缺失，也不是前端卡片渲染问题。现有 Harness 已能按消息关键词追加 `sentiment_analysis` 和 `contagion_analysis`；错误发生在同一轮状态中仍保存了模型给出的 `capability=risk`，而摘要路由优先信任该字段，早于消息和工具证据的回退判断。

## 修复目标

在不改变 HTTP/SSE 返回结构、不改 service 层、不影响写操作和多能力查询的前提下，确保显式、单一的窄能力请求在以下三个位置使用同一个主能力：

1. `llm_intent.capability`；
2. Harness `current_task.capability` 与任务计划；
3. 最终摘要和其证据边界。

本轮不再只收敛两条复现语句，而是建立受控的显式表达词表。只有“单一、高置信、只读”的能力表达可以覆盖模型误判的通用 `risk`；写操作、范围查询和复合能力表达保持现有路由，避免关键词扩大后损坏审批或多维分析。

## 能力表达覆盖审计

现有实现把能力词分散在 intent fallback、Harness 计划、外部数据任务和摘要路由四处。它们的表达覆盖不同，且摘要在收到 `capability=risk` 后直接短路，因此下列显式请求都存在同类风险，而不只是本次的两条问题：

| 能力 | 高置信表达（首批支持） | 典型易误入 `risk` 的问法 | 覆盖策略 |
| --- | --- | --- | --- |
| 舆情 | 舆情、新闻动态、新闻报道、负面信息、负面舆情、媒体报道 | “最近有什么新闻”“有没有负面报道” | 单一命中时覆盖为 `sentiment` |
| 供应链网络 | 供应链关系、上下游关系、关联企业、关联关系、风险传导、风险传染、传染风险 | “上游出问题会不会传导”“关联公司会影响它吗” | 单一命中时覆盖为 `risk_network` |
| 财务 | 财务数据、营收/营业收入、利润/净利润、现金流、负债率、资产负债、财报 | “现金流健康吗”“利润下滑了吗” | 单一命中时覆盖为 `financial` |
| 风险预测 | 风险预测、预测风险、未来 N 个月、未来一年、可能怎样变化、情景推演 | “如果营收下降会怎样”“未来半年风险如何” | 单一命中时覆盖为 `risk_prediction` |
| 历史趋势 | 历史风险变化、近 N 月变化、环比、同比、上月相比、风险走势 | “最近三个月怎么变了”“为何比上月高” | 单一命中时覆盖为 `risk_trend` |
| 主体核验 | 主体核验、核验主体、统一社会信用代码、是否同一主体、登记状态 | “这几个名字是不是同一家公司” | 单一命中时覆盖为 `identity_review` |
| 工商/联系方式 | 工商资料、注册资本、注册地址、股东、法人、联系人、联系方式、电话、官网 | “给我电话和官网”“查注册资本” | 单一命中时覆盖为 `company_profile` |
| 企业对比 | 对比、比较、哪家更适合、横向比较 | “A 和 B 哪家更可靠” | 单一命中且存在比较对象时覆盖为 `risk_comparison` |
| 质量/交付 | 质量风险、质量问题、不良率、交付风险、交期、准时交付 | “会不会延期交货”“产品质量稳定吗” | 单一命中时覆盖为 `quality` / `delivery` |
| 寻源 | 寻找供应商、找候选、推荐供应商、替代供应商、备选供应商 | “帮我找替代厂家” | 仅在存在品类/候选/替代的寻源语义时覆盖为 `sourcing` |
| 只读报告 | 生成报告、导出报告、下载风险报告 | “帮我生成风险报告” | 仅无定时/设置/确认等写操作标志时覆盖为 `report` |

下列能力不作为第一阶段的“关键词强制覆盖”对象，仍由模型和既有多维计划决定：

- 合规、司法、经营风险：`诉讼、处罚、失信、欠税、制裁` 经常并列出现；例如“诉讼和行政处罚”不应被武断地降成单一司法或经营能力。
- ESG：`环保、碳排、职业健康` 在供应链评估中也可能只是综合风险的一个维度，需要先补充领域词和反例语料。
- 监控清单、部门范围、排名：它们是范围/权限语义，不是单供应商能力；不能因出现“风险最高”而覆盖为普通 `risk`。
- 加入/移出监控、定时报告、批准/拒绝、停止/继续：属于写操作或 durable control，必须始终优先于只读能力归一化。

原始两类复现语句仍是 P1 验收用例：

| 显式表达 | 归一化主能力 | 主工具 | 摘要主文案 |
| --- | --- | --- | --- |
| `舆情`、`新闻`、`负面信息` | `sentiment` | `sentiment_analysis`（及已有新闻提供方查询） | 舆情分析 / 新闻与负面信息 |
| `供应链关系`、`关联关系`、`传染风险`、`风险传染` | `risk_network` | `contagion_analysis` | 供应链关系与传染风险分析 |

## 设计

### 1. 建立共享、纯函数的能力归一化模块

新增 `backend/app/graphs/agent_core/capability_routing.py`，只放无副作用的常量、正则和函数，不依赖 FastAPI、LangGraph、数据库或 service：

- `detect_explicit_capabilities(message: str) -> list[str]`：返回按“特异性、位置”稳定排序、去重后的候选能力；
- `explicit_primary_capability(message: str) -> str | None`：仅在候选恰为一个可强制覆盖的只读能力时返回该能力；
- `normalize_primary_capability(message: str, extracted_capability: str, requested_action: str = "none") -> str`：仅当模型结果为 `risk`/`none`、没有写操作/恢复控制标志、且候选唯一时覆盖；其他情况保持模型结果；
- 令匹配规则和关键词只维护一处，供意图抽取与 Harness 摘要共同使用。

规则采用“最长/最具体短语优先、再按文本出现位置”的确定性排序，并支持中文口语、常见业务同义词和数字时间窗。对同时命中多个不同能力的请求，不做强制单能力覆盖，保留模型能力、`analysis_dimensions` 和现有多工具计划；本问题的两个单能力请求不会受该保护分支影响。这样不会把“同时分析舆情和供应链关系”或“财务、司法、舆情和合规”的复合诉求静默缩减为其中一个能力。

每条规则必须同时定义：正向表达、必要上下文、排除表达、目标 capability、计划工具和摘要标签。禁止仅凭一个宽泛词（如“趋势”“影响”“风险”“供应链风险”）改变路由；例如“供应链风险”本身仍可表示综合风险，必须出现关系/上下游/传导等语义才归入 `risk_network`。

### 2. 在意图抽取完成后、进入共享执行上下文前归一化

修改 `backend/app/graphs/agent_core/intent_extractor.py`：

1. 完成 LLM payload 校验、实体校验、维度扩展和写操作识别后，调用共享归一化函数；
2. 用归一化后的 capability 重新计算 `task_type`，并同步归一化 `analysis_dimensions`：当模型的泛化 `risk` 被明确单能力取代时，移除该遗留 `risk`；`sentiment`、`financial`、`quality`、`delivery` 写入对应维度，其余专用能力不保留无关的综合风险任务；
3. 保持 `target_supplier_names`、`analysis_dimensions`、`provider_capabilities`、`requested_action` 的现有含义不变；
4. 日志增加 `raw_capability` 与 `normalized_capability`，便于审计模型误判，不记录用户敏感正文。

归一化必须发生在 `llm_intent` 写入执行上下文之前。这样 `adapter.validate_execution_context()` 中“`llm_capability_not_preserved`”的当前契约仍然成立：`llm_intent` 与 `current_task` 保存的是同一个归一化结果，而不是在下游偷偷改写 `current_task`。

同时修正 `_deterministic_extraction_fallback` 的同一缺口：它已能将“舆情”写入 dimension，却会把 capability 置为 `risk`。fallback 也调用同一纯函数，保证 LLM 不可用时结果一致。对寻源、主体核验、比较等需要结构化上下文的规则，fallback 只在确定性前置条件满足时覆盖，避免凭词面伪造 task 或目标。

### 3. Harness 仅消费归一化能力，并保留旧状态的防御性兼容

修改 `backend/app/graphs/harness/graph.py`：

- `_append_capability_tasks` 继续从 `current_task.capability` 读取，因此归一化后会只将 `sentiment` 或 `risk_network` 作为请求的主工具；消息关键词追加逻辑保留为旧 state/直接构造 HarnessState 的兼容兜底；
- `_primary_summary_capability` 先调用共享的 `normalize_primary_capability(message, capability)`。这只会修正泛化 `risk`/`none`，因此可覆盖历史持久化 state、旧测试构造的 state 以及未来任何绕过 extractor 的兼容入口；
- 仍按现有工具/证据逻辑决定“无可验证数据”的具体说明，不伪造舆情、新闻或关联主体事实；
- 不变更 SSE 事件类型、`AgentAnswer`、证据 JSON、API 路由或前端 `StreamCallbacks`。

## 冲突检查与边界

| 风险点 | 控制措施 | 预期结论 |
| --- | --- | --- |
| `llm_intent` 与 `current_task` 不一致导致执行上下文拒绝 | 在 extractor 内归一化，再生成二者；不在 adapter 或图中单独覆写状态 | 不触发 `llm_capability_not_preserved` |
| 复合查询被错误缩成单一能力 | 多种能力同时命中时不覆盖模型的非确定主能力；后续可另立 issue 设计多能力主摘要 | 本轮仅强制覆盖“唯一且高置信”的只读能力请求 |
| 通用“风险新闻”应仍输出综合风险 | 只有符合词表正向表达和必要上下文的单一能力才覆盖 `risk`；无这些词的泛化风险问题不变 | 既有综合风险回归保留 |
| 写操作误落到只读 Harness | 归一化只处理 `risk`/`none`，不改变 `requested_action`；写操作仍由现有明确写意图和 Supervisor 路径接管 | 审批、取消、恢复不受影响 |
| “设置每周风险报告”误作只读报告 | 所有 `requested_action != none`、定时/设置/确认/批准/拒绝、停止/继续表达均禁止能力覆盖 | Q35、Q46、Q47、Q60 不回归 |
| 直接构造 state 的旧测试/恢复 state | 摘要层调用同一纯函数作为防线；任务规划的关键词兼容逻辑保留 | 不要求数据迁移，不破坏历史会话展示 |
| 证据维度混淆 | 保持工具 task 的 `dimension=sentiment` / `risk_network` 与既有 claim 绑定；当能力已纠正时移除 LLM 遗留的泛化 `risk` dimension，不再执行或附加无关风险 score | 回答、证据卡与工具调用一致 |
| 术语重叠导致错误单能力化 | 候选能力超过一个、规则缺少必要上下文或命中排除表达时不覆盖，只记录审计日志 | 多维请求保持原计划 |

明确不修改：`app/services/`、数据库 schema、PostgreSQL 控制面、持久化 Run/SSE 协议、前端 API 类型、外部数据提供方和写操作审批实现。

## 测试计划

### A. 意图归一化单元测试

在 `backend/tests/test_agent_intent_extractor.py` 新增“表达矩阵”参数化回归，模拟 LLM 分别返回 `capability=risk` 和正确主体：

1. 每个首批能力至少覆盖 3 个同义/口语表达（例如新闻动态/媒体报道/负面舆情，上下游关系/关联企业/风险传导，现金流/负债率/营收，未来半年/情景推演/预测风险）；
2. 两条本次复现问题必须归一化为 `sentiment` 和 `risk_network`；
3. 对模型本已正确返回的 `financial`、`legal_risk`、`sourcing`、写操作不覆盖；
4. LLM 故障的 deterministic fallback 对可确定的舆情、供应链、财务表达也返回正确能力；
5. “舆情 + 供应链”“财务 + 司法 + 舆情 + 合规”“诉讼 + 行政处罚”“风险趋势 + 未来预测”等复合/重叠表达不被单能力规则强制选择；
6. “设置每周报告”“加入监控”“停止刚才复核”“继续未完成任务”等 control/write 表达，即使同时含“风险/报告/新闻”词，也不得被改为只读能力。

### B. Harness 计划与摘要回归

在 `backend/tests/test_agent_harness_task14.py` 新增两组“脏的历史/直构 state”测试：

1. `current_task.capability=risk` + 首批每种单能力表达 + 对应 claim/tool，摘要必须选择对应主文案，不得出现“综合风险分析”；
2. 两条本次复现用例重点断言“舆情分析”与“供应链关系与传染风险分析”；
3. 从 extractor 结果进入 `_build_default_plan` 后，分别包含该能力所需的主工具，且单一舆情/供应链请求不再包含 `assess_risk`；
4. 复合能力的 Harness state 继续保留多工具和既有综合/多维摘要语义，不以词表规则删除任务；
5. 现有通用风险摘要、Q35 审批、Q46/Q47/Q60 durable control 测试继续断言既有终态，防止关键词优先级扩大。

### C. 执行上下文契约与 HTTP/SSE 定向回归

1. 运行相关 adapter/intent/harness 测试，确认标准化后的 `llm_intent` 可通过 `validate_execution_context`；
2. 以认证用户、隔离 `E2E_TEST` 数据调用真实 `/api/v1/chat/stream`，运行两条复现问题和每个首批能力的至少一个代表表达；
3. 断言 SSE 包含对应 tool call、`agent_answer` 和 `done`，终态成功；
4. 断言最终 `agent_answer` 的主文案为窄能力，不断言外部数据内容（外部数据可能为空）；
5. 检查 PostgreSQL Run/Turn 的 capability 与工具/证据维度一致。

### D. 浏览器回归（修复获批并通过后执行）

仅使用已有 Chrome 标签页中新标签页或 Codex 内部浏览器：

1. 打开既有工作台，分别提交两条复现问题，并抽测财务、预测/趋势、主体核验、工商资料、寻源、质量/交付各一条代表表达；
2. 检查回答首段、工具/证据卡的维度和“无数据”文案是否一致；
3. 刷新后确认历史答案仍以舆情或供应链主能力显示；
4. 收集截图、DOM/可访问性文本、SSE 摘要、控制台 error/warning 计数和 Run 状态；
5. 关闭本次新增标签页，更新 `docs/issue-log.md` 的验证结果和关联提交。

## 实施顺序与提交边界

1. 先新增共享纯函数及其单元测试；
2. 修改 extractor 与 fallback，运行意图/adapter 定向测试；
3. 修改 Harness 摘要防线与计划回归，运行 Harness 定向测试；
4. 运行后端非集成回归、前端 TypeScript/lint/Vitest/build（预期前端无代码改动）；
5. Docker 服务健康后执行真实 HTTP/SSE 和浏览器验证；
6. 在 issue 条目补齐验证和提交号；将代码、测试、问题记录、浏览器证据作为同一交付批次提交。

建议使用一个原子提交：`fix(agent): normalize explicit read-only capabilities`。如果任一定向测试表明复合能力、审批或恢复链路受影响，停止在对应阶段，不扩大关键词覆盖范围，并在 ISS-20260920-001 记录回归证据。

## 验收条件

- 两条复现问题即使模型返回 `capability=risk`，最终也分别以“舆情分析”和“供应链关系与传染风险分析”为主回答；
- 首批词表中的单一、高置信表达在模型误判为 `risk` 时均进入相应能力；复合、写操作、范围和权限表达不被强制单能力化；
- `llm_intent`、`current_task`、任务工具、claim/evidence dimension 和 SSE 最终答案一致；
- 通用风险、写操作审批、恢复/取消和复合能力兼容用例不回归；
- 不修改既有 API/SSE 返回结构，不产生测试数据残留；
- 浏览器控制台无新增 error/warning，新增标签页在验收后关闭。

## 实施结果（2026-09-20）

- 已新增共享能力归一化模块，并在 intent extractor、Harness 摘要和 adapter 的专用能力空维度覆盖处接入。
- 定向 Harness/intent/控制面回归 170 项通过；后端非集成回归 912 项通过；前端 TypeScript、ESLint、Vitest（125 项）和生产构建在本轮前端未改动条件下通过。
- 真实认证 HTTP/SSE 与同端口 Vite 代理均验证了两条原始复现语句：舆情请求只执行舆情/新闻工具；供应链请求只执行 `contagion_analysis`，终态均为 `completed`。
- 浏览器页面自动化未获得可用终态证据：Codex 内部浏览器登录后未呈现本地 SSE 终态，Chrome 既有标签页连接不可用。已记录 `ISS-20260920-002`；未启动独立 Chrome，也不将浏览器验收标记为通过。
