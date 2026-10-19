# Run 生命周期第一阶段 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** 在现有部署下保证 Run 单活、取消不可反弹、结果原子提交和断连后的可靠对账。

**Architecture:** 同领域 RunCoordinator 管理独立内部执行记录，以 PostgreSQL 行锁、attempt 和租约保护既有 agent_runs。图通过回调消费执行许可；SSE 仅订阅受管理任务。保持旧 API、业务状态和审批 Command。

**Tech Stack:** FastAPI、psycopg2、LangGraph、React、pytest、Vitest；不增加依赖。

**Spec:** `docs/superpowers/specs/2026-10-19-durable-harness-evolution-design.md` 第 2—4、7 节。

## Global Constraints

- 保持已有 REST 返回结构、SSE 事件名及字段语义。
- 一个请求只加载一次 `execution_context`，图不重新加载会话。
- service 保持框架无关，审批原样传递 LangGraph `Command`。
- PostgreSQL 为事实源；Mongo 展示失败不能回滚已完成执行。
- 先记录问题，不删除测试；原工作区已有文档修改不混入代码提交。
- 页面断开只退订；显式停止才取消，终态不可覆盖。

## Review Focus

- Cancel 与完成同时提交：第一个合法事务获胜，后续提交不得反弹（Task 1/3）。
- 同 Session 首次创建并发、Redis 故障：最多一个活动 Turn（Task 1）。
- 已过期/被替换 attempt 迟到：不得更新快照、答案、上下文或公开事件（Task 1/2）。
- SSE 在第一个事件后断开：图继续且最终输出可回放，队列不能无限积压（Task 3）。
- A 会话断流后 B 会话开始：旧回调不修改 B，审批 waiting_approval 仍可恢复（Task 4）。

## File Structure

- 新增 `backend/app/domains/agent_run/coordinator.py`：内部状态、执行许可、租约、取消和最终事务。
- 修改 `backend/app/db/init_pg.py`：独立执行记录表和索引；不增加旧响应字段。
- 修改 `backend/app/domains/agent_run/state_store.py`：Session 单活、快照 fencing、委托结果提交。
- 修改 `backend/app/domains/agent_run/service.py`：取消委托、按终态游标排空。
- 修改 `backend/app/graphs/harness/graph.py`：节点/工具前后执行检查。
- 新增 `backend/app/graphs/harness/runner.py`：受管理任务、续租、订阅生命周期。
- 修改 `backend/app/graphs/streaming.py`、`backend/app/api/chat.py`：接入 runner，最终答案一致提交。
- 修改 `frontend/src/api.ts`、`components/ChatView.tsx`；新增扁平 hook/测试（按现有接口拆分）。
- 新增 `backend/tests/test_run_coordinator.py`、`test_run_coordinator_integration.py`、`test_harness_managed_runner.py`；扩展已有图与聊天测试。

## Task 1: RunCoordinator 与 PostgreSQL 单活

**Interfaces:** `ExecutionPermit(run_id, attempt_id, fencing_token)`；`RunCoordinator.claim(run_id, owner_id) -> ExecutionPermit`；`check(permit) -> None`；`heartbeat(permit) -> bool`；`cancel(run_id, user_id) -> dict`。内部状态不进入既有响应。当前代码中的 legacy Run 在首次领取时初始化内部记录。

- [x] 写测试：终态/取消后 check 抛执行失权；租约过期旧 permit 不能续租；两个不同 owner 只一个可领取。
- [x] 写真 PG 测试：以屏障竞争 cancel/finish；同 Session 并发首次 start 只有一个成功；真实回滚不残留终态事件。
- [x] 运行新增用例观察 RED；测试数据库使用独立临时容器，禁止连接工作数据库。
- [x] 实现执行记录 DDL、RunCoordinator、start_harness_turn 的 ON CONFLICT + Session 行锁与活动检查。
- [x] 运行 `cd backend && .venv/bin/python -m pytest tests/test_run_coordinator.py tests/test_run_coordinator_integration.py -q`，显式提供临时 PG 环境；期望全部 PASS。
- [x] 更新 issue-log 中 003/026 的实施范围、证据与提交记录，提交相关文件。

## Task 2: 活动图执行检查与恢复边界

**Consumes:** Task 1 的 permit/check；**Produces:** `run_harness(..., control: Callable[[], Awaitable[None]] | None)`，节点与工具启动/返回时调用。`persist_harness_snapshot(..., permit=...)` 检查同一许可，最终 snapshot 不提前写 Run 终态。

- [x] 写图测试：第一波工具在途时取消，依赖它的第二个工具启动次数为 0；取消异常不能被普通工具失败分支吞掉。
- [x] 写持久化测试：旧 attempt 的 render_answer/persist_turn 不能覆盖已取消 Run 或上下文。
- [x] 运行测试观察 RED。
- [x] 接入控制回调、快照保护、持久化取消、checkpoint 对账暂停；保留无外部依赖的历史定向测试调用方式。
- [x] 运行新增测试及现有 Harness、工具、审批 Command 回归，期望 PASS。
- [x] 更新 003/004 的进度，提交相关文件。

## Task 3: 原子结果和受管理 runner

**Consumes:** Task 1/2；**Produces:** 独立于 HTTP 的图任务和公开事件订阅，最终结果事务含 Run、Turn、上下文、最终事件及展示 Outbox。等待审批的 done 是连接边界，非 Run 终态。

- [x] 写测试：订阅退出后图完成；续租失权后停止；终态事件排空；失败事务重试无重复终态；展示写失败后 Run 仍可恢复最终答案。
- [x] 运行新增测试观察 RED。
- [x] 实现 runner 强引用注册表、独立续租、有限内存订阅与 durable replay；最终结果通过 Coordinator 同事务提交，旧事件格式原样投影。
- [x] 将 Mongo 展示持久化委托 Outbox；API finally 仅退订/释放加速锁，PG 单活覆盖后台阶段。
- [x] 运行 `test_harness_managed_runner.py`、`test_agent_harness_streaming.py`、真 PG 原子性测试和离线 `-m agent_e2e`；期望 PASS。
- [x] 更新 004 进度，保留第三阶段进程接管待验收状态，提交。

## Task 4: 前端按 Run 隔离与断流对账

**Consumes:** 既有 Run/事件查询契约；**Produces:** 按用户/Session/Run 恢复句柄、去重游标、EOF 后明确状态；不增加前端目录层级或状态库。

- [x] 写测试：A/B 流交错、切换会话退订、刷新恢复、approval EOF、401 不无限重试、waiting_approval 不被当作最终完成。
- [x] 运行 Vitest 对应用例观察 RED。
- [x] 实现 run 归属校验与局部累积器、恢复句柄、统一 EOF 对账；登出清理恢复句柄。
- [x] 运行 frontend Vitest、lint、build 及后端聊天上下文/审批/事件回放回归，期望 PASS。
- [x] 更新 011/012/004 的验证，提交；阶段 1 真 PG 并发、取消、断连门禁通过后才进入阶段 2。

## 执行记录

- 用户于 2026-10-19 验收设计并授权开工；本会话直接实施，不重复请求开工许可。
- 当前工作目录是用户的 `codex/2026-09-23` 开发分支；保持当前目录并精确提交，避免新增 worktree 丢失既有未提交问题记录。
- 临时 PG 容器：`supplisense-run-lifecycle-test`，仅 127.0.0.1:55439，数据库 `run_lifecycle_test`。
- 阶段 2/3 的详细计划在阶段 1 接口验收后编写，本计划不声称交付进程崩溃自动接管。

### 2026-10-19 首批实施结果

| 范围 | 当前进度 |
| --- | --- |
| 1A Harness 生命周期 | 已实现内部执行记录、QUEUED 创建、许可/租约、原子取消、Session 数据库单活；真 PG 竞争与事务回滚通过 |
| 1B Harness 执行控制 | 已实现节点和工具前后检查、迟到结果 fencing；Supervisor 全面接入、原始 checkpoint 安全续跑尚未交付 |
| 1C 持久化与断连 | 已实现受管理生产者、周期续租、原子最终事件与上下文、Outbox 展示、过期暂停及回放关闭；自动接管仍在阶段 3 |
| 1D 前端 | 已实现每次执行局部累积器、订阅归属、切换退订、刷新重放、显式停止、审批 EOF 检测及事件流认证重试 |
| 发布门禁 | 增加独立 CI 真库步骤；本地真 PG/Mongo 竞争和生产 Harness E2E、前端全量、审批 Command 定向测试通过，离线全量执行结果另记 issue-log |

- 独立审查发现的回放异常跨会话、活动回答提前投影、Mongo 独有历史被覆盖，均已补充失败回归并修复。
- 部署前必须执行新增 schema。内部 `agent_run_executions` 不加入旧 API 返回模型。
- 不将本批交付标记为第一阶段全部完成：完成 Supervisor 生命周期和 checkpoint 恢复门禁后，才进入评估记录阶段。
- 执行裁定：在当前开发分支按后端与前端形成相关提交；数据库迁移和所有调用点一起提交，避免中间版本缺少新表/许可参数。代价是后端提交覆盖面较大，需要真库与独立审查共同验证。

### 首批验证与提交

- 文档与问题依据：`d285e32a`。
- 后端核心与 CI：`6a06f317`；前端隔离与恢复：`d97e7fcc`。
- 后端定向回归 53 passed，包含真实 PG/Mongo 的并发、回滚、投影与生产 Harness 链路；测试库为独立 `run_lifecycle_test`。
- 离线标记集：`pytest -m "agent_e2e and not integration"` 100 passed。
- 前端全量 19 文件、143 passed；`npm run lint`、`npm run build` 通过。
- 独立审查三项发现均已通过 RED→GREEN 回归；另补 QUEUED 遗留、满队列尾部及 PAUSED 回放回归。
- 尚未完成的门禁：Supervisor 统一协调、可信 checkpoint 校验及安全续跑；阶段 2/3 未开始。阶段 1 总验收保持开放。

### 续作：Supervisor 与安全恢复

用户已授权继续；沿用当前分支与 inline 执行。冻结执行输入与 LangGraph 配置保存到内部执行表，ContextVar 只传递当前 attempt，不作为事实源。

- [x] S1：Coordinator 增加原子 pause/resume，新的 attempt/fencing token，绑定冻结输入及 checkpoint 指纹；统一创建读写 Turn 和 Session 单活。
- [x] S2：managed Supervisor 节点/适配器/动作执行检查许可，checkpoint 使用 Run 身份，审批暂停与最终公开事件经 Coordinator 原子提交。保留历史定向测试适配入口。
- [x] S3：动作领取与真实回执记录；图内/Outbox 并发只有一个可开始，未知结果暂停；已完成结果可重复读取，当前权限和参数/期限仍校验。
- [x] S4：恢复核对 checkpoint 身份/输入/计划与 PG 快照；原始 Command 透传，已提交工具结果从 PG 补齐，未完成写不自动重放；HTTP 使用持久化恢复元数据，不重载 Session 上下文。
- [x] S5：真 PG + 持久化 LangGraph saver 验证 restart/resume、重复审批、过期/改参/权限撤销、取消及终态事件；现有离线 E2E、前端门禁与独立审查。

执行裁定：历史审批元数据缺少运行/输入绑定时保持待核对，不将旧消息重跑冒充恢复；代价是少量旧任务需要用户取消后重新发起。checkpointer 与领域 PG 不共用事务，恢复先核对领域回执，再决定图能否继续。

- Ruling: 托管图使用 `thread_id=run_id` 和根 `checkpoint_ns=""`，以冻结输入摘要绑定 runtime；LangGraph 把非空 namespace 当子图路径，实际 `aget_state` 会报 `Subgraph chat not found`。保留历史配置生成器仅供兼容定向测试。
- 2026-10-19 新根因（关联 ISS-20261018-004）：持久化 saver 回归发现 `chat:agent-supervisor` 命名空间不能被根图恢复读取；先记录后修复，新增真实 saver 重建用例覆盖。


### 2026-10-19 续作交付记录

S1–S5 实现完成，第一阶段代码交付范围已完成；阶段 2、3 尚未启动。首批记录中的未完成项由本节收敛。

- Supervisor 与 Harness 使用同一 Session 数据库单活、执行许可、取消事务和原子终态提交；审批等待只结束 SSE 连接。
- 执行输入不可变，恢复校验 Run/Session/用户、输入摘要、固定 checkpoint_id、计划与已提交结果。Harness 可从 PG 补回领先于 saver 的只读结果，已发起但无结果的调用仍计入预算。Supervisor 的不一致检查点保持暂停，不替换审批输入或盲重放写操作。
- 写操作领取与真实回执独立持久化。图与 Outbox 不能重复启动；未知结果暂停核对；取消期间已返回的真实写回执仍保留，不复活 Run。
- saver 初始化失败释放恢复领取；旧审批缺少冻结输入时可取消后重新发起。兼容 Run API 的取消也走同一终态事务与版本校验。
- 独立 fresh-context 审查发现两项 Important：持续进度漏续租、历史审批缺少取消出口；均已先记录问题并执行 RED→GREEN 回归。未遗留该次审查的 Important/Critical。
- 真 PG + 实际 AsyncPostgresSaver 覆盖关闭连接并重建图后恢复、审批/拒绝、过期、改参、权限撤销、取消/完成竞争、旧 attempt、实际回执重用、未知结果与 PG/saver 提交窗口。没有使用外部 LLM。
- 本批最终后端定向门禁 123 passed，提交记录随 issue-log 同步；离线 Agent E2E 为 100 passed，前端为 143 passed，lint/build 通过。
- 部署需应用 `init_pg.py` 新增列与 `agent_action_receipts` 表；未新增依赖，未修改已有 REST 返回模型。
- 保留当前开发分支并形成可审阅提交。独立 worker、scheduler/投递拆分及进程退出自动接管仍按第三阶段实施；本轮未将其标记完成。
