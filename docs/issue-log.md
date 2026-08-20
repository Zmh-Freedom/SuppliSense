# 项目问题记录

本文件记录开发、测试和验收过程中发现的问题，作为后续修复、回归测试和上线评审的依据。

## 记录规范

- 发现问题时先登记，再开始修复。
- 每条问题必须包含：发现日期、现象、影响、根因、修复方案、验证结果和关联提交。
- 修复过程中发现新的根因或回归问题，必须在原条目下补充，不得只依赖聊天记录。
- 已关闭问题保留记录，不删除；如再次出现，新增复发记录并关联原问题。

## ISS-20260819-001 Agent 外部供应商准入陷入重复推理

- 发现日期：2026-08-19
- 状态：已修复；外部候选真实浏览器准入验收待单独执行
- 优先级：P0
- 现象：用户明确要求对“深圳市云钥科技有限公司”执行准入申请后，Agent 反复讨论 `result_id`、寻源结果和待核验状态，没有调用外部候选准入工具，也没有生成准入申请记录。
- 影响：外部供应商无法从会话上下文稳定进入人工确认和准入申请链路；Agent 可能输出很长的自我推理，降低可用性并造成“已执行但未落库”的误判。
- 初步根因：
  1. 外部候选使用 `candidate_id`，本地寻源结果使用 `result_id`，Agent 缺少确定性分流。
  2. 会话引用抽取未保留 `candidate_id`、候选类型和身份核验状态。
  3. 天眼查直接返回的候选未统一经过唯一身份核验，无法满足准入工具的 `identity_status=exact` 门禁。
  4. ReAct 图缺少针对“直接执行准入”意图的确定性路由和工具循环上限。
- 修复方案：保留候选身份上下文；统一外部候选身份核验；根据候选类型确定性选择准入工具；增加准入意图路由和循环上限；补充端到端回归测试。
- 修复内容：
  1. `SupplierReference` 和引用抽取保留 `candidate_id`、`candidate_type`、`identity_status`。
  2. 联网和天眼查候选统一经过天眼查身份核验。
  3. 准入意图向 Agent 注入确定性工具路由，外部候选禁止误用 `result_id`。
  4. 图级硬路由在唯一 `external + exact` 候选下直接生成 `select_external_supplier_candidate` 工具调用。
  5. Agent 工具调用增加 12 次上限，避免重复推理持续扩大。
- 验证结果：29 项准入、上下文、发现流程回归测试通过；Python 编译检查和 `git diff --check` 通过。外部候选类型路由已由自动化测试覆盖；真实浏览器外部候选准入仍需使用可用的唯一核验候选单独验收。
- 关联提交：`81c2bd7a fix(agent): route external supplier admission deterministically`

### 2026-08-20 回归：本地寻源结果准入未进入审批

- 状态：已修复，真实工作台验收完成
- 现象：用户从“工业相机供应商”推荐结果中指定“深圳市康斯得电子有限公司”申请准入后，Agent 仅以文字称将调用准入，随后流结束，前端没有人工审批卡片。
- 影响：本地供应商推荐不能稳定进入准入申请；用户无法区分“已提出申请”和“未执行”。
- 根因：
  1. 本地寻源候选的 `result_id` 与 `candidate_type=local` 未持久保留到会话引用，确定性路由只覆盖了外部候选。
  2. ReAct 图未使用可恢复 checkpoint；LangGraph 的 `interrupt` 在 SSE 事件流中以 `on_tool_error(GraphInterrupt)` 结束，而流适配器只捕获了外层异常，未转换为 `approval_required`。
  3. 本地准入工具在缺少审批上下文时错误地默认批准，违反所有写操作必须人工确认的边界。
  4. `build_input_messages` 在存在执行上下文但未传供应商引用的降级路径中遍历 `None`，会中断流式响应。
- 修复方案：同时保留本地 `result_id` 和外部 `candidate_id`；按候选类型确定性路由准入；ReAct/Reflection 图接入持久 checkpoint；将工具级中断事件转为审批 SSE；无审批上下文一律失败闭环。
- 验证结果：
  1. 37 项定向回归全部通过：本地/外部候选引用保留、类型路由、会话上下文、审批流 SSE、安全门禁和固定多轮评测。
  2. Supervisor、V2 寻源风险图、执行快照和审计轨迹共 56 项断言均执行通过；测试进程在异步资源收尾阶段未在 30 秒内返回汇总，未将其计为完整通过。
  3. 依赖恢复后，`/health/ready` 的 MongoDB、Redis、PostgreSQL 与 checkpoint 均为 `ok`；真实工作台返回 9 家本地候选，并正确展示本地 `result_id` 的人工审批卡片。未批准动作，未创建准入申请。
- 关联提交：`d52141b2 fix(agent): complete admission approval flow`

## ISS-20260820-002 集成验证环境依赖不可用

- 发现日期：2026-08-20
- 状态：已恢复
- 优先级：P1
- 现象：`GET /health/ready` 返回 `503`，MongoDB、Redis、PostgreSQL 均为 `unavailable`，而进程存活检查正常。
- 影响：无法继续执行依赖会话持久化、寻源结果、审批恢复和供应商准入记录的集成/浏览器验收。
- 根因：开发基础设施当时未运行；应用进程存活但依赖就绪检查失败。
- 修复方案：由环境启动方恢复三项依赖后，先确认 `/health/ready` 全部为 `ok`，再执行准入审批的真实链路回归。
- 验证结果：2026-08-20 已恢复，MongoDB、Redis、PostgreSQL 与 sourcing-risk checkpoint 全部为 `ok`；后续真实工作台寻源和审批卡链路已通过。

### 2026-08-20 再次复发：供应商画像计划验收依赖不可连接

- 状态：阻塞（等待开发基础设施恢复）
- 现象：执行 `python -m pytest tests/test_supplier_profile_service.py tests/test_risk.py -v` 时，画像服务测试 3 项通过；`test_risk.py` 在 FastAPI lifespan 阶段因 PostgreSQL `localhost:5432` 拒绝连接而报错，同时 MongoDB `localhost:27017` 索引初始化失败。
- 影响：本轮不能执行依赖 PostgreSQL/MongoDB 的 API 集成验收；不影响无外部依赖的画像聚合和前端定向回归。
- 处置：按集成测试依赖不可用即停止的约束，未尝试修复业务代码或绕过依赖。待基础设施恢复后，重跑画像服务测试、`test_risk.py` 以及画像 API 联调。

## ISS-20260820-003 寻源子图在数据库重启后使用失效连接

- 发现日期：2026-08-20
- 状态：已修复
- 优先级：P1
- 现象：真实工作台中“帮我找电机供应商”“推荐工业相机供应商”均在 `langgraph-sourcing` 子图返回“LLM 服务连接失败”，但基础 DeepSeek 调用和绑定寻源工具的首次模型调用均成功。
- 影响：本地与外部寻源均无法完成，因而无法在真实 UI 验收后续的本地候选准入审批。
- 已排除：MongoDB、Redis、PostgreSQL 和 LangGraph checkpoint 均为 `ok`；ReAct 风险评估在真实工作台完成；DeepSeek 基础调用与工具绑定首轮调用返回正常。
- 根因：数据库容器在后端运行期间重启，旧后端进程仍持有失效的长连接（停止时也记录到 PostgreSQL 连接已关闭）。寻源子图依赖的持久 checkpoint/数据库连接因此失败，但流封装将该内部错误泛化为“LLM 服务连接失败”。
- 修复方案：重启后端以重新建立数据库与 checkpoint 连接；在寻源流异常出口补充结构化日志，避免后续将非 LLM 异常误判为模型网络问题。
- 验证结果：后端重启且 `/health/ready` 四项检查均为 `ok` 后，真实工作台“帮我找电机供应商”成功返回 9 家本地候选；随后对“八方电气（苏州）股份有限公司”发起准入，正确展示 `select_sourcing_result` 的人工审批卡片和有效 `result_id`。未批准动作，未创建准入申请。
- 关联提交：`2c77c302 fix(agent): log sourcing stream failures`

## ISS-20260820-004 聊天请求重复加载会话上下文

- 发现日期：2026-08-20
- 状态：已修复
- 优先级：P1
- 现象：聊天 API 已在路由与澄清前加载一次 `ConversationState`，但各执行模式的流函数又独立从 MongoDB 加载会话上下文。
- 影响：同一请求存在重复数据库读取；在并发写入或恢复场景中，预检、路由和实际执行可能基于不同会话快照，导致重复目标解析或澄清结果不一致。
- 根因：统一执行上下文已引入，但尚未作为请求级依赖传递至全部执行模式。
- 修复方案：聊天 API 只构建一次 `execution_context`，将其传入各 LangGraph 流函数；补充回归测试，保证执行层不再次加载会话状态。
- 验证结果：聊天 API 现将唯一 `execution_context` 透传给 ReAct、Plan-Execute、Supervisor、Sourcing、Agent Supervisor、Parallel 与 Reflection 图；30 项上下文/图/审批回归与 `agent_e2e` 快照复用场景通过。
- 关联提交：`db943db6 refactor(agent): converge context and approval recovery`

## ISS-20260820-005 ReAct 审批恢复不支持 LangGraph Command

- 发现日期：2026-08-20
- 状态：已修复
- 优先级：P0
- 现象：本地供应商准入可正确产生 `approval_required` 卡片，但点击批准后恢复流返回 `LLM 服务异常: 'Command' object has no attribute 'get'`，未继续执行准入工具。
- 影响：人工审批通过后，写操作无法恢复，用户可能误以为已完成准入但没有实际结果。
- 根因：`ReactGraphWithSystemPrompt.astream_events()` 假定输入一定是字典并调用 `.get()`；审批恢复传入的是 LangGraph `Command`。
- 修复方案：图包装器仅在输入为字典时注入系统提示词；`Command` 原样透传至底层编译图。将“本地候选 → 审批卡 → API 批准恢复 → 成功回执”加入 CI 自动 E2E 回归集。
- 验证结果：CI 自动 E2E 已覆盖“本地候选 → `approval_required` → `/resume` 批准 → 工具成功回执”，2 项 `agent_e2e` 通过；图包装器对普通输入注入系统提示词，对 `Command` 原样透传。
- 关联提交：`db943db6 refactor(agent): converge context and approval recovery`

## ISS-20260820-006 全量回归中的健康检查与调度锁测试隔离失败

- 发现日期：2026-08-20
- 状态：已修复
- 优先级：P1
- 现象：真实 `/health/ready` 返回 MongoDB、Redis、PostgreSQL 和 checkpoint 均为 `ok`，但全量 pytest 中 3 项健康检查单测返回 `503`，另有 1 项 PostgreSQL scheduler advisory-lock 测试无法取得首个锁。
- 影响：CI 全量后端回归不能稳定作为合并门槛；当前不影响已运行实例的 readiness 判断。
- 根因：健康检查单测没有固定 `AGENT_RUN_V2_ENABLED`，在开发环境启用 checkpoint 时会多出未 mock 的第四项检查；调度锁测试复用了生产 scheduler 的固定 advisory-lock key，与正在运行的后端竞争同一锁。
- 修复方案：健康检查单测 fixture 明确关闭可选 checkpoint 检查以保持三项依赖测试边界；`SchedulerLeadership` 支持注入锁 key，测试使用专属 key 验证互斥，不影响生产默认 key。
- 验证结果：`test_health.py` 与 `test_scheduler.py` 共 12 项通过；全量后端测试按时间窗口拆分执行，662 项均已覆盖，修复后无剩余失败。
- 关联提交：`db943db6 refactor(agent): converge context and approval recovery`

## ISS-20260820-007 供应商画像测试对可重复来源文案作唯一匹配

- 发现日期：2026-08-20
- 状态：已修复
- 优先级：P2
- 现象：供应商画像定向测试以 `getByText("来源：company_website")` 断言来源文案；当官网与邮箱都来自官网抓取时，测试因匹配到两个正常元素而失败。
- 影响：前端定向回归出现误报，无法作为画像功能的稳定验收依据。
- 根因：测试将“单个来源文本”错误建模为页面唯一内容，未考虑来源是字段级元数据、可合法重复。
- 修复方案：改为断言至少一个匹配项，并继续验证官网链接和告警变更详情，避免弱化真实业务覆盖。
- 验证结果：前端画像定向测试通过；断言继续覆盖官网链接、来源展示与告警变更详情。
- 关联提交：`315b0a44 feat(supplier): complete profile operations and traceability`

## ISS-20260820-008 后端 readiness 在依赖容器健康时返回 500

- 发现日期：2026-08-20
- 状态：已修复
- 优先级：P1
- 现象：`sra-postgres`、`sra-mongo`、`sra-redis` 均为 healthy，但 `GET http://127.0.0.1:8002/health/ready` 返回 500 通用错误信封，而不是约定的带检查明细的 200 或 503。
- 影响：无法将当前 8002 实例作为画像 API 集成与浏览器验收的可靠前置条件。
- 根因：8002 监听的是基础设施启动前遗留的 Uvicorn reload 进程，进程内的 checkpoint 未完成初始化；当前代码新建 `TestClient` 实例的 readiness 为 200，说明不是路由实现缺陷。
- 修复方案：使用当前工作区配置重启本地后端，使其重新建立 PostgreSQL、MongoDB、Redis 和 checkpoint 连接。
- 验证结果：重启后 `GET /health/ready` 返回 200，MongoDB、Redis、PostgreSQL 与 sourcing-risk checkpoint 均为 `ok`；`tests/test_supplier_profile_service.py tests/test_risk.py` 共 5 项通过。
- 关联提交：`38c6f684 fix(supplier): restore profile section aggregation`。

## ISS-20260820-009 供应商画像浏览器验收缺少前端开发服务器

- 发现日期：2026-08-20
- 状态：已修复
- 优先级：P2
- 现象：浏览器打开 `http://127.0.0.1:50008/suppliers` 返回 `ERR_CONNECTION_REFUSED`，而后端 8002 已 ready。
- 影响：无法执行供应商画像页的真实 UI 联调。
- 根因：本地 Vite 前端开发服务器未运行。
- 修复方案：启动前端开发服务器，确认页面可访问后执行不落库的页面验收。
- 验证结果：启动 Vite 后 `http://127.0.0.1:50008/` 返回 200，浏览器可进入登录页；后续画像页验收等待已登录会话。
- 关联提交：`38c6f684 fix(supplier): restore profile section aggregation`。

## ISS-20260820-010 供应商画像聚合函数漏传企业名称

- 发现日期：2026-08-20
- 状态：已修复
- 优先级：P1
- 现象：真实浏览器打开供应商画像后，风险、财务、舆情、合规、ESG、告警和关联标签均显示为空或“暂无数据”；后端日志记录多个 `TypeError`，提示画像聚合函数缺少 `company_name` 或 `master` 参数。
- 影响：画像页虽然可访问，但无法展示核心风险与关系数据；关系图和关联供应商跳转也无法验收，用户可能误判为企业确实没有相关数据。
- 根因：`build_supplier_profile()` 通过 `_try_build()` 调用各画像子函数时，未将当前企业名称传入风险、财务、舆情、合规、ESG、告警和关联函数；财务函数同时缺少主数据参数。异常被 `_try_build()` 隔离并返回空值，导致页面静默降级。修复参数后进一步发现，合规统计未兼容天眼查无结果记录中 `items.result = null` 的数据形态。
- 修复方案：按各子函数签名补齐统一的 `company_name` 和 `master` 参数；将合规统计的结果读取改为兼容 `null`、非字典和缺失值；补充回归测试，确保画像各分区调用参数正确、无结果数据不会中断整页聚合，并能渲染已有数据；重新执行后端、前端和浏览器画像验收。
- 修复期间新增测试问题：合规回归测试初版将不存在的 `assess_sanctions` 作为桩函数，实际模块 API 为 `check_sanctions`；该问题仅影响测试桩初始化，不影响生产代码执行。
- 浏览器验收期间新增测试问题：关联跳转已成功，但验收脚本随后对未限定名称的 `heading` 使用严格匹配，因页面同时存在多个标题而产生自动化断言错误；已改为按画像标题精确定位，不影响产品功能。
- 验证结果：已补齐画像聚合各分区的 `company_name`/`master` 参数，并兼容合规集合中 `items.result = null` 的无结果缓存。供应商画像定向测试 5 项通过；风险 API 定向测试 2 项通过；真实浏览器验证风险、财务、舆情、合规、ESG、告警和关联数据均可加载，关系图显示 2 个关联实体，点击“大明电子股份有限公司”成功跳转；编辑、重新评估和加入监控均展示人工确认卡且未执行写操作；审计标签正常加载并在无记录时显示空状态。前端画像测试 6 项、lint、类型检查和生产构建通过；`/health/ready` 四项检查均为 `ok`。
- 关联提交：`38c6f684 fix(supplier): restore profile section aggregation`。
