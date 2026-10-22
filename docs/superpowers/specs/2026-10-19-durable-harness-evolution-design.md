# Harness 运行可靠性与评估记录演进设计

日期：2026-10-19

代码基线：`328a569d`，`codex/2026-09-23`

状态：第一阶段已完成并验收；第二阶段实现、开发库增量迁移、独立审查修复及自动化门禁已完成，待用户验收；第三阶段 3A 已实现并通过独立审查修复与专库门禁，3B 待开始；3C/3D 未实现。

## 1. 目标与实施顺序

按用户指定顺序推进，每阶段独立验收后再进入下一阶段：

1. 统一 Run 生命周期、取消、恢复和终态事件，在现有部署中验证。
2. 建立稳定评估 ID、证据清单和政策版本，使恢复能核对业务结果。
3. 把同一 Harness 移到独立 worker，再拆 scheduler 和投递角色，验证进程退出后的接管。

成功标准是取消后不再启动新工具、旧执行者不能覆盖新状态、最终结果可以完整回放、同一次评估不会因恢复重复生成、退出的执行进程可被接管。保留现有只读 Harness 和写操作 Supervisor 审批兼容入口。

**已验收的默认规则：**关闭页面、切换会话或网络断开只取消订阅，只读分析继续执行；显式“停止”才发出取消命令。第一阶段仅保证所属 API 进程存活期间继续，进程故障后的自动接管到第三阶段验收。该规则随本设计一并验收。

## 2. 不变量与范围

- PostgreSQL 是控制状态、执行命令、结构化评估及事件顺序的唯一事实源。MongoDB 保存原始业务证据和展示投影。
- 不新增按聊天 mode 选择执行内核的活动分支；独立 worker 执行现有 Harness。
- 一个请求只加载一次 `execution_context`，持久化后由图和 worker 消费同一快照；当前权限在敏感操作前独立校验，不能以旧上下文代替授权。
- service 保持框架无关，LangGraph 调用留在 graphs/runtime 边界。供应商引用继续使用共享 adapter 收集器。
- 审批恢复原样传递 LangGraph `Command`，不得改造成普通消息重跑。
- 保持已有 REST 返回结构、SSE 事件名及字段语义。新增内部字段通过兼容投影隔离；新增查询契约用独立版本入口，不能暗改旧响应。
- 不删除现有测试；新问题先登记 `docs/issue-log.md`，修复提交补验证记录和提交号。
- 第一轮复用 PostgreSQL、现有 checkpointer 和 Outbox，不引入 Celery、RabbitMQ、Temporal 或第二套 Agent 框架。
- 本项目仍为同一代码库的模块化单体；进程分工不引入领域之间的远程 RPC。

## 3. 现状与问题对应

| 阶段 | 已登记问题 | 当前代码落点 |
| --- | --- | --- |
| 1 | ISS-20261018-003、004、011、012、026 | `agent_run/state_store.py` 快照覆盖；`service.py` 取消与事件回放；`graphs/streaming.py` 持有执行任务；前端 ChatView 共享流状态 |
| 2 | ISS-20261018-002、006、016、017、018 | `risk/repo_assessment.py` 每次保存生成新 UUID；`risk/service.py` 双写；`alert/service.py` 快照身份与版本；scheduler 沿用旧分数 |
| 3 | ISS-20261018-007、008、019，并完成 004 的进程恢复部分 | `main.py` 内启动调度与 schema；`services/scheduler.py` 选主；`outbox/worker.py` 批次投递；Compose 多 Web worker |

003/004 跨阶段关闭：阶段 1 解决状态、订阅和可恢复边界；阶段 3 完成故障接管演练后才能关闭完整问题。001 的付费权限缺口不因进程拆分自动修复；任务迁移到后台前必须统一操作者、范围、预算和审计，否则相应后台功能不开放。

## 4. 阶段 1：统一 Run 生命周期

### 4.1 分开记录执行状态与业务结果质量

内部执行状态采用以下有限状态集合；原业务阶段继续单独保存，不把 SCORING、INVESTIGATING 等阶段当作执行所有权。

| 内部状态 | 含义 | 允许后继 |
| --- | --- | --- |
| QUEUED | 已提交，尚未取得执行权 | RUNNING、CANCELLED、FAILED |
| RUNNING | 当前 attempt 持有执行权 | PAUSED、CANCEL_REQUESTED、SUCCEEDED、FAILED |
| PAUSED | 等审批、澄清，或故障后等待安全恢复 | QUEUED、CANCELLED、FAILED |
| CANCEL_REQUESTED | 已收到停止命令，禁止启动新工具 | CANCELLED |
| SUCCEEDED / FAILED / CANCELLED | 执行终态 | 无 |

`PAUSED` 必须有原因、checkpoint/恢复依据和下一步动作。租约到期先失效旧 attempt，再判断安全恢复条件；不能直接伪造成功。无法验证检查点或外部写结果时保持暂停并提示人工核对。

结果质量单独记录 `complete / partial / needs_review / unavailable`。SUCCEEDED 表示本次执行完成，不意味着证据充分或供应商安全。SUCCEEDED 结合质量映射原有 COMPLETED/PARTIAL/NEEDS_REVIEW 等外部状态；失败、取消沿用原错误与状态契约。历史 sourcing V2 的细粒度状态先经适配器归一化，保留业务转换约束，不全局替换旧枚举。

### 4.2 唯一协调入口与执行权

增加框架无关的 RunCoordinator 应用边界，所有活动状态写入委托此边界：

- 接收 start、pause、resume、cancel、finish；在短 PostgreSQL 事务内验证合法转换。
- 持久化 `attempt_id`、递增 `fencing_token`、`owner_id`、`lease_until`、`cancel_requested_at` 和终态事件位置。
- 每次图快照、工具结果、结果提交均验证当前 attempt、token、有效租约和非终态。仅比较最新 `version` 不足以阻止旧执行者覆盖。
- 同一 Session 的活动 Turn 创建使用数据库互斥和唯一性约束，Redis 只作加速；Redis 故障不能降级为不同进程各自单活。
- 一个 Session 同时只运行一个会修改其上下文的 Turn。PAUSED 运行未解决时，可响应查询/取消/审批；新执行请求按现有冲突格式拒绝，用户选择终止旧运行后再开始。
- 阶段 1 由 API 进程中的受管理 runner 持有执行权，任务注册表只用于唤醒和清理，数据库记录决定是否允许继续。

节点开始、每个工具启动前、工具返回后和最终提交前都检查取消/执行权。并行工具逐个检查，不仅检查整个批次。取消事务与新工具的执行授权事务按 Run 锁排序；取消提交前已获得授权的调用视为在途调用。

### 4.3 取消及竞争规则

- QUEUED/PAUSED 可直接取消；RUNNING 先记录取消意图并撤销后续执行授权，随后完成 CANCELLED 收尾。
- 取消与成功并发时，以第一个合法提交为准。成功先提交则停止请求返回当前结果；取消意图先提交则后续成功不可覆盖取消。
- 显式停止幂等；不能仅靠一句包含“取消”的自然语言误判撤销采购或监控业务。
- 同步提供方调用不能保证立刻中止。取消只保证不启动后续工作，丢弃迟到结果的业务提交；已完成的外部写入不会被撤回。
- 已发出的写调用若结果未知，动作回执标记待核对，禁止盲重试。Run 可取消，动作待核对状态独立保留；不能把取消解释成“业务未发生”。
- 默认取消收尾等待上限 10 秒；超时后完成逻辑取消并拒绝旧 attempt 的提交。实际线程/连接按提供方自身超时释放，指标保留在途调用数。

### 4.4 终态与事件提交

最终可信答案、Turn 响应、会话上下文增量、Run 终态、最终公共事件和 Mongo 展示投影 Outbox 在同一 PostgreSQL 事务提交。事务失败则保持未完成；不能先写终态再补公共答案。展示文本分块属于进度，前端以最终结构化答案和终态回放收敛。

内部设置每 Run 唯一的终态事件引用和单调公共游标。终态后不追加会改变结果的公共事件；诊断写独立日志/审计记录。事件订阅必须排空到已提交的终态游标，不能只看到终态标记就提前返回；重连按 run 和 event cursor 去重。

**兼容例外必须保留：**当前 Supervisor 的 `done(status=waiting_approval)` 结束的是这次 SSE 响应，Run 仍处于 PAUSED。它不是 Run 的唯一终态事件；同一 Run 审批恢复后可以有后续事件。前端继续识别该状态，不将其清理为已完成，也不新增同名事件的新语义。失败流沿用现有 error 结束方式，仍须持久化可回放的失败结果。

### 4.5 断连、恢复与审批

- SSE generator 退出只解除订阅，不直接 cancel 受管理 runner。切换会话后旧 run 状态独立保存，回调不得写进新 run。
- 前端按用户、Session、Run 保存恢复句柄和最后确认游标；刷新先查授权状态再订阅。清理登录态时清理句柄，不持久化认证令牌到新位置。
- 相同 Run、原输入、原证据与政策继续执行产生新 attempt；已终态运行重新分析必须新建 Run，并关联原 Run。修改输入或政策同样新建。
- 恢复前核对 LangGraph checkpoint、已提交步骤和工具/动作回执。checkpointer 与领域数据库不是一个事务，已提交业务结果可先于 checkpoint；不可只根据图节点重做副作用。
- 审批恢复绑定 proposal、参数哈希、过期时间、当前权限及幂等回执；重复点击返回同一结果，不重复执行动作。
- 阶段 1 的进程重启通过运维 reconciliation 扫描**租约已过期**的运行，失效旧 attempt：原 RUNNING 置 PAUSED/interrupted；原 CANCEL_REQUESTED 完成 CANCELLED 及终态事件收尾。多 Web worker 中一个启动时不得暂停其他有效运行。不承诺本阶段自动接管所有中断点。

### 4.6 阶段 1 交付与验收

| 工作包 | 交付 | 验收证据 |
| --- | --- | --- |
| 1A | 内部生命周期、兼容映射、RunCoordinator、租约及 attempt fencing | 真实 PG 并发取消/完成；旧 attempt 延迟写入被拒；Redis 不可用时同 Session 不产生两条活动运行 |
| 1B | Harness/Supervisor 状态写入、协作取消和恢复校验接入 | 工具在途时停止，后续工具启动数为 0；原 Command 恢复；重复审批仅一个写回执 |
| 1C | 原子结果与终态事件、受管理 runner 与订阅分离 | 在终态提交点注入失败，结果和事件同成同败；断 SSE 后 runner 完成，回放最终答案完整 |
| 1D | 前端按 run 隔离、断流对账、恢复句柄 | A/B 会话事件交错不串数据；审批 EOF 不永久 submitting；刷新恢复正确 run |

阶段门：上述场景全部有自动回归；真 PG 事务/竞争测试必须实际运行，不能用 mock 结果替代。现有 Agent E2E 保持无外部依赖；真实 PG 并发测试使用 integration 标记与独立 CI 服务。

## 5. 阶段 2：稳定评估记录

### 5.1 身份与版本

保留 `assessment_history.id` 作为稳定 assessment ID，扩展现有表和关联表。UUID 在评估开始时分配，恢复复用；不再在每次保存时生成新 ID。

| 对象 | 必需内容与约束 |
| --- | --- |
| Assessment | ID、创建者/授权范围、规范 company_id、适用的 monitor_target_id/采购关系、Run/task 关联、状态与质量、时间、评分和完整性 |
| Evidence manifest | evidence_id、来源、payload 引用及内容哈希、观察/采集时间、维度、可用性、新鲜度、冲突和查询回执 |
| Policy snapshot | policy_id、版本、不可变配置及哈希：维度、权重、阈值、缺失政策、新鲜度、适用范围、评分方向 |
| Execution provenance | 请求/输入快照、计划与逻辑评估键、模型/提示词/工具实现版本或部署版本、计算实现版本 |
| Review/receipt | 原 assessment ID、复核人、理由、决定、动作幂等键及真实回执，不覆盖原分数 |

一个 Run 可以评估多家企业，唯一键用 `run_id + logical_assessment_key`，键由任务、对象和评估范围确定，不能用 run_id 单独限一条。非聊天入口使用操作者范围内的请求幂等键；新的主动分析产生新评估。缓存复用须引用原 assessment，不能把旧分数包装成新评估。

评估开始冻结政策；采集期清单可追加，进入计算前冻结证据清单和输入摘要。恢复不读取“最新政策”或悄悄替换证据；冻结后新增证据创建关联的新评估。原始证据内容变更必须产生新引用或新版本。

### 5.2 缺失数据与评分兼容

证据状态区分 `available / empty / unavailable / stale / conflicting / not_requested`。empty 必须有成功查询回执；unavailable 不能参与“无风险”计分。覆盖率以本次请求和冻结政策的必需维度为分母，财务单项查询不会因未查 ESG 被误扣完整性。

沿用当前 v3 的“高分更安全”方向；改权重、缺失策略或阈值必须新政策版本并记录计算实现版本。缺乏最低证据时内部评估为 unavailable，禁止造默认分数。旧成功响应要求整数分数的端点只返回达到既有成功契约的结果，否则使用现有错误信封和适当错误状态；允许不完整结果的新查询放到版本化入口。不能直接把旧整数改成 null。

财务更新仅更新财务证据；综合分保持原 assessment ID 和原评估时间，或真正重算新评估。禁止改 checkedAt/版本却沿用旧综合分。趋势只比较同对象、同范围、同政策的结果；跨版本展示分段且提示，不拼成同一条同口径序列。

### 5.3 落库、恢复与历史迁移

1. 在现有 raw-payload staging/compensation 机制中保存证据原文及哈希，校验可读性。
2. PostgreSQL 事务提交评估结构、冻结清单、任务业务结果及 Outbox。若该评估就是 Run 最终结果，通过阶段 1 的协调事务同时完成终态；多评估 Run 允许先提交单项结果。
3. Outbox 以 assessment ID 幂等生成 Mongo 快照/展示记录；显示未同步与评估失败分开记录，重试不改原评估。
4. 故障恢复先按逻辑键查评估及工具回执：已有已提交结果直接复用；未提交原始证据按 staging 所有权和现有补偿流程处理。

历史 UUID 保留。可证明的身份和版本才回填；无法证实的清单标记 legacy_unverified，不伪造证据哈希或来源时间。同名记录不得自动合并，限制旧名称回退在缺失稳定 ID 且无歧义的记录中；歧义交人工处理。

评估版本在 PG 内原子分配，Mongo 以 assessment ID 唯一投影；重试不使用读 latest+1 分配版本。旧快照前驱关系通过新记录关联迁移，保留历史。

### 5.4 阶段 2 交付与验收

| 工作包 | 交付 | 验收证据 |
| --- | --- | --- |
| 2A | 评估身份、逻辑幂等键、政策快照及证据清单 schema，历史迁移报告 | 同一逻辑任务重试只有一个 ID；同 Run 两家企业各有记录；同名不同公司/采购范围不串读 |
| 2B | 评分消费冻结输入和真实可用性，评估时间与证据时间分离 | 提供方失败不贡献安全分；新政策不污染旧评估；财务更新不伪造新综合评估 |
| 2C | 评估/Outbox 原子提交，Mongo 幂等投影及恢复对账 | PG 提交后、Mongo 写前崩溃，重放只产生一条投影；checkpoint 落后时不重复计算/动作 |
| 2D | 趋势、复核和旧端点兼容 | 旧成功响应结构保持；证据不足明确失败/部分质量；复核保留原始分数和版本 |

阶段门：可以从任何新 assessment ID 查清“评谁、依据哪些证据、用何政策、何时采集/计算、属于哪次运行”；跨库故障可补偿，未知历史不被标记为可验证。

## 6. 阶段 3：独立执行与进程接管

### 6.1 角色与数据流

| 角色 | 职责 | 长期工作所有权 |
| --- | --- | --- |
| API | 认证、一次性上下文、创建 Run 与任务、审批/取消、状态查询、SSE/WS | 不持有图执行任务，不启动 scheduler |
| Agent worker | 领取 durable job、同一 Harness/Supervisor、续租、恢复、结果提交 | Run attempt 租约 |
| Scheduler | 计算到期计划，提交带周期键的任务 | 计划触发，不直接抓取/评分/发通知 |
| Delivery worker | Outbox 投影、通知、重试和死信 | 单条投递租约，独立并发与重试预算 |
| Migration job | 发布前 schema/约束/索引检查与升级 | 独立一次性部署步骤 |

同一镜像提供不同入口。先迁 Agent，再迁 scheduler 和 delivery；新旧入口切换通过同一数据库 owner/部署开关防止双执行。迁移 job 失败则部署停止，不依赖每个 Web worker 启动时争抢 DDL。

API 在单一 PG 事务创建 Run/Turn 与执行 job；SSE 随后订阅原公共事件，路由和响应契约保持。API 在订阅前退出也不会丢任务。Redis/NOTIFY 可唤醒，PG 轮询是兜底；共享缓存丢失不丢运行。

### 6.2 PostgreSQL durable job

复用现有 Outbox 的短事务 `FOR UPDATE SKIP LOCKED` 领取方式，但使用独立执行任务表。Agent 工作项与通知投递分别配置，不把长图执行塞进现有短通知批次。

- job 保存 run、队列、输入引用、幂等键、可执行时间、attempt 上限和失败原因；RunCoordinator 继续掌握运行状态。
- 默认 heartbeat 5 秒、lease 30 秒、到期扫描 5 秒；以 PG 时间为准。启动校验 lease 至少为 heartbeat 的 3 倍。恢复次数默认最多 3 次，超过上限失败或进入可核对暂停，禁止无限重放。
- 租约领取事务同时分配新的 fencing token；过期 worker 即使恢复网络也不能提交。执行期间不持有数据库行锁或长事务。
- 节点 checkpoint 与领域回执共同决定安全恢复点。外部调用按至少一次执行设计，写操作必须业务幂等/对账；不声明外部动作 exactly-once。
- 活动运行默认总执行预算仍沿用当前 Harness 120 秒；恢复继承剩余预算和已用调用计数，等待审批不计入活动时间，不通过重试重置额度。
- 优雅退出停止领取，给在途工作最长 30 秒排空；不能安全完成则交出/等待租约过期，接管者核对已提交步骤再恢复。SIGKILL 不依赖 finally。

### 6.3 Scheduler、投递与资源

计划唯一触发键为 `schedule_id + schedule_revision + target_id + logical_period`，使用唯一约束。周期按计划时区计算，同时保存 UTC 边界；夏令时重复小时不重复逻辑周期。默认错过周期只补最近一次，显式批量补历史需另行授权和预算。多个 scheduler 重复插入返回既有任务，执行正确性不依赖 leader 恰好只有一个。

付费任务携带操作者或明确 service principal、目标范围、预算预留和必需审计；执行前验证当前权限，范围撤销后不得继续外部读取/写入。投递结果以消费键幂等，不假设网络通知天然只发一次；未知外部送达结果保留待对账。

Agent、通知与投影分别限并发。数据库总连接预算按 API 副本×池、worker 副本×池、checkpointer、scheduler、delivery、迁移/运维余量求和，必须低于 PG 配额；只增加 Web worker 数不等于增加模型并发预算。

跨进程 WS 使用共享分发传递失效通知，持久化快照/通知记录负责重连对账。共享分发短暂丢消息不能成为数据丢失；前端收到事件和重连时统一失效相应 Query key。

### 6.4 阶段 3 交付与验收

| 工作包 | 交付 | 验收证据 |
| --- | --- | --- |
| 3A | durable job、独立 Agent worker、租约与恢复 | 两 worker 并发领取只有一个有效 owner；API 在提交后退出仍完成；杀死 worker 后接管 |
| 3B | scheduler 独立入口、周期键、权限预算对齐 | 双 scheduler 同周期仅一个任务；付费任务越权/额度不足时不外呼；错过周期按固定政策补发 |
| 3C | delivery 独立入口、跨进程分发、前端对账 | 通知进程退出可恢复；不同 API worker 上连接均收失效通知；分发断开后重连读到最新状态 |
| 3D | 一次性迁移入口、角色部署、探针、故障注入与回滚手册 | 旧 API 不执行新 job；DDL 失败不放流量；SIGKILL、失权旧 worker、PG/Redis/Mongo 故障演练通过 |

阶段门：在真实 PG、Mongo、Redis 和至少两个执行进程中演练。工具和 LLM 可用固定本地替身控制屏障、超时及副作用，避免用真实付费调用制造故障。按上述默认值，租约失效检测应在最后成功续租后 35 秒内发生；随后存在可用 worker 时，60 秒内观察到新 attempt 或明确不可恢复的暂停原因。耗时任务完成时间单独评估，不把“接管”误当成“完成”。

## 7. 发布、观测与回滚

预计代码边界如下，后续各阶段实施计划再锁定函数签名与迁移 SQL：

| 边界 | 主要现有文件 | 计划新增职责 |
| --- | --- | --- |
| 生命周期 | `backend/app/domains/agent_run/{state_store,service,harness_contracts,repo}.py` | 同领域 `coordinator.py` 集中状态转换，`execution_jobs.py` 管理阶段 3 的领取与租约 |
| 图执行 | `backend/app/graphs/harness/{graph,durable_actions}.py`、`backend/app/graphs/streaming.py` | `graphs/harness/runner.py` 从 SSE 中分离图执行；不在领域 service 导入图 |
| 评估 | `backend/app/domains/risk/{service,repo_assessment}.py`、`backend/app/domains/sourcing_risk/{evidence_service,policy_service}.py` | 复用证据/政策能力，由 risk 领域拥有规范评估记录；不复制第二套评分实现 |
| 展示与恢复 | `frontend/src/api.ts`、`types.ts`、`components/ChatView.tsx`、`hooks/useSourcingRiskRun.ts` | 运行订阅及恢复 hook，保持现有扁平目录 |
| 角色与部署 | `backend/app/main.py`、`services/scheduler.py`、`domains/outbox/worker.py`、`db/init_pg.py`、`docker-compose.yml` | `backend/app/workers/` 提供角色入口；独立迁移命令复用现有 DDL，版本化并记录执行结果 |

每阶段按“增加 schema → 新写入兼容 → 灰度启用 → 验收 → 清理旧入口”推进。先备份 PG 控制面及 Mongo 原始证据，演练恢复；保留旧列和迁移报告，不在本轮直接删除历史数据。

至少观测：各执行状态数量和年龄、取消收尾耗时、过期租约、fencing 拒绝、活动工具数、队列等待时间、终态事件滞后、重复逻辑评估、投影积压和死信、审批待核对、模型调用/成本及连接池占用。日志携带 run_id、attempt_id、task_id、assessment_id 和事件游标，避免写证据原文或令牌。

阶段 1 回滚仅停用新任务入口，继续支持已写生命周期记录；不能换回会覆盖终态的旧写入。阶段 2 关闭新评估生产但保留新记录和投影补偿。阶段 3 先停止领取并排空/失效独立 worker，再切回兼容的进程内 runner；同一 job 不能有两种 owner 同时活动。可回滚的应用版本必须预先验证兼容新增字段和内部状态；任意旧版本不能直接回滚。

每个工作包形成单独可审阅提交，包含对应回归和 issue-log 更新；整个阶段通过门禁再进入下一阶段。仓库既有测试、frontend lint/build、离线 Agent E2E 为基础门禁，真库并发与故障测试补充其无法覆盖的风险。

## 8. 文档与参考的使用方式

本文件固化设计和阶段门。详细到函数签名、测试断言与逐项提交的实施计划，在本设计审阅通过后按阶段分别编写，避免第三阶段提前锁死第一阶段尚待验证的接口。

依据：`docs/architecture-review-2026-10-18.md`、`docs/github-architecture-research-2026-10-18.md` 和 `docs/issue-log.md`。外部项目的固定提交与源码证据在研究文档中保留：Dify 提供协调/暂停参考；Onyx 提供订阅与执行分离参考；ERPNext/CISO 提供评估身份和历史参考。这里采用这些职责边界，具体持久化和 API 仍遵循本项目约束。

审阅重点：断连继续的默认行为；等待审批期间的同 Session 冲突策略；取消和已发外部写的区别；历史评估的不可验证标记；三阶段门禁与自动恢复边界。
