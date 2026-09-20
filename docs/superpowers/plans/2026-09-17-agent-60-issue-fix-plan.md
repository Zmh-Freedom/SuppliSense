# Agent 60 条问题修复实施计划

## 目标

修复 60 条 Agent 固定语料中确认的 P0/P1 问题，同时保持现有 Harness Runtime、证据契约、财务跨入口修复、SSE refresh/retry 修复和前端空输入修复不回归。

## 2026-09-19 浏览器失败复盘与冲突检查

本轮复用现有 Chrome 标签页完成页面实测后，新增失败集中在“已修复后端分支的未覆盖自然语言变体”，不是三库或 SSE 基础设施故障：

| 问题 | 根因 | 方案 | 冲突检查 |
| --- | --- | --- | --- |
| Q23“结合刚才…继续筛选”被当作恢复 | 恢复正则只要命中“继续”即可，覆盖了“继续筛选” | 恢复意图必须同时包含明确恢复对象；“继续筛选/继续优化”留给寻源多轮 | 只收紧 `chat_recovery.py`，不改变 `/chat/resume`、取消链路或 API 结构 |
| Q26“这些候选都不合适”重复发现 | 无合适候选词典缺少“都不合适/不要重复调用/下一步建议” | 扩展 `_apply_sourcing_follow_up` 的无结果分支，复用候选快照并生成行动建议 | 只影响已有 sourcing follow-up 状态，不修改候选工具、外部数据或正式供应商写入边界 |
| Q35“每周生成一次…”确认后重复澄清 | 共享实体归一化没有剥离定时报告前缀，且显式写操作识别器未覆盖该口语变体，导致请求先落入只读分析 | 增加定时报告口语前缀归一化，并扩展确定性定时报告写操作识别；保持原始报告动作和频率，确认后仍进入审批 | 只改实体/意图边界和对应回归；不触碰 report service、审批 proposal 或写入逻辑 |

实施顺序：先补纯函数回归（恢复、寻源 follow-up、实体归一化），再跑后端定向测试和前端测试，重启 8000/5174，最后用同一 Chrome 标签页重测 Q23/Q26/Q35 及刷新回放。若任一测试破坏既有 Q46/Q47/Q60 或 Q30，立即停在上一阶段并记录回归。

## 冲突控制

- 现有工作区有未提交修改，所有改动在当前文件基础上增量进行，不执行 reset、checkout 或覆盖式生成。
- 先改后端纯函数和契约测试，再改图路由，最后改前端/浏览器脚本；每个阶段先定向测试，避免多个层同时变更导致根因不可区分。
- 不改 `backend/app/domains/supplier/service.py`、`frontend/src/components/SupplierProfilePage.tsx`、`frontend/src/api.ts` 中已完成并已回归的 P1 修复，除非新增测试证明存在直接回归。
- 不在 service 层引入 LangGraph；实体归一化放在 `conversation_state.py`/`clarification.py`，意图和写边界放在 `intent_extractor.py`/Harness。
- 所有新发现先写入 `docs/issue-log.md`；每个问题关闭前补充定向测试、全量回归和真实浏览器证据。

## 文件与问题映射

| 阶段 | 问题 | 允许修改文件 | 明确不触碰 |
| --- | --- | --- | --- |
| A 测试数据 | ISS-005、010 | `backend/tests/conftest.py`、`backend/tests/evals/`、新增 E2E fixture/脚本、测试文档 | 生产数据、现有业务数据迁移 |
| B 实体状态 | ISS-006 | `backend/app/services/conversation_state.py`、`backend/app/services/clarification.py`、`backend/app/graphs/agent_core/intent_extractor.py` | `backend/app/domains/supplier/service.py` 财务逻辑 |
| C 写操作路由 | ISS-008 | `intent_extractor.py`、`graphs/agent_core/adapter.py`、`graphs/harness/graph.py`、必要的 durable action contracts | 只读 tool/service 的返回结构 |
| D 寻源追问 | ISS-007 | `graphs/harness/graph.py`、`graphs/agent_core/contracts.py`、`domains/sourcing_risk/requirement_service.py`、对应测试 | 外部候选入库边界和 service 层框架无关性 |
| E 定时报告 | ISS-009 | `intent_extractor.py`、`graphs/harness/graph.py`、`domains/risk/tools_report.py`、对应测试 | 报告生成格式和已有 report service |
| F 恢复闭环 | ISS-010 | `domains/agent_run/chat_interrupt_repo.py`、`domains/agent_run/service.py`、`api/chat.py`、恢复测试 | 已完成 SSE 401 refresh/retry 实现 |

## 实施顺序

### Phase 0：建立可重复测试前置

1. 将 60 条语料改为 JSONL，明确每题角色、session 链、预期 capability/action、是否允许写入。
2. 建立 `E2E_TEST` 供应商、近似主体、外部候选、无数据/无交易、冲突/过期证据和 paused run fixture。
3. 启动测试时校验 fixture 数量、责任范围和清理标记；测试结束按 run 前缀清理。

### Phase 1：P0 实体与写边界

1. 抽取统一的实体文本规范化函数，覆盖 NFKC、空白、括号和“看看/先查/是/给/为”等口语前缀。
2. 候选确认后只保存 canonical identity；后续轮次不再重新触发 formal identity clarification。
3. 扩展 action contract 支持 add/remove/batch/report，写意图识别失败时进入安全澄清，禁止回退到只读风险分析。
4. 先完成 ISS-006、ISS-008 的定向回归，再跑 Agent E2E。

### Phase 2：P1 多轮业务语义

1. 在当前任务状态中保存 sourcing constraints、candidate list version 和 selected candidate ID。
2. 增加预算/替代/序号/无结果追问解析，保持外部候选为待核验状态。
3. 增加“给/为/替…设置每周报告”解析，创建/更新/取消/重复操作全部走审批与幂等。

### Phase 3：取消、刷新和恢复

1. 为 paused、approval-required、cancelled、completed、denied 建立可注入状态。
2. 恢复接口原样透传 LangGraph Command，按 Last-Event-ID 回放并校验 session/run/user 绑定。
3. 无可恢复任务时返回结构化状态，不再返回泛化“请指定供应商”澄清。

### Phase 4：验证门槛

- 定向后端/前端测试 100% 通过。
- 后端非集成、Agent E2E、前端 Vitest、lint、build 全量通过。
- 60 条固定问题全部使用正确 session 链执行；每条至少 3 个措辞变体。
- P0 业务语义 100% 通过；P1 不得有阻断。
- 真实浏览器仅使用 Codex 内部浏览器或现有 Chrome 新标签页；回归结束关闭新增标签页/会话。

## 回滚策略

- 每个 Phase 使用独立小提交；失败时只回滚该 Phase 的提交，不触碰既有 P1 修复和用户未提交文件。
- 若写操作或恢复改动导致 Harness 主链失败，立即停留在上一阶段通过版本，保留 issue 复现数据和日志。

## 当前执行进度（2026-09-17）

- Phase 1：已完成实体归一化、主体确认上下文复用、add/remove/batch 写意图安全路由；相关定向测试通过，真实 SSE 已验证 remove/batch 不再落入只读风险分析。
- Phase 2：已完成定时报告 action-draft 与 Durable Action Service 审批提案接入；预算/成本约束合并（含 LLM 降级确定性提取）、泛化成本词保护、候选列表版本化、候选序号消费和无候选行动建议已完成。新增“每周生成一次风险报告”写操作识别回归；真实认证 SSE 与同一 Chrome 标签页均完成 Q22→Q23→Q25→Q26。
- Phase 0：已完成 60 条 JSONL、E2E_TEST manifest、隔离命名空间和 paused/waiting-approval/refreshable recovery fixture 校验；已新增 PostgreSQL/MongoDB/Redis 幂等 seed/teardown、HTTP/SSE runner 和隔离报告输出。
- Phase 3：已完成 Harness Run/Turn 的持久化取消、聊天取消意图绕过单活锁、PostgreSQL interrupt claim/release/ack 恢复和无恢复任务结构化终态；真实三库 `agent_e2e_live` 通过 7 项，Q46/Q47/Q60 证据已保存，并提供 `RUN_AGENT_60_LIVE_ALL=1` 的全量 manifest HTTP/SSE runner 入口。
- 全量 HTTP/SSE：已使用真实网络 HTTP 客户端跑完 Q01-Q60，报告保存在 `docs/evidence/agent60-full-http-2026-09-19.json`；当前 26/60 按契约通过，剩余问题主要是业务 fixture 未完整种入、审批终态不符和个别请求超时。
- 浏览器门槛：Docker、8000/5174 已 ready；复用现有 Chrome 标签页 `2064620264` 完成 Q22/Q23/Q26、Q30、Q35 和刷新历史回放，控制台 0 error/0 warning，未启动独立 Chrome。
- ISS-011：已修复前端 SSE CRLF/尾部 buffer 未 flush 的终态丢失路径；ChatView 27/27、TypeScript 和生产构建通过；Q35 `approval_required`、拒绝终态和刷新回放已在浏览器验证。
- 剩余阻塞：ISS-20260919-003 的完整业务 fixture 尚未写入 Mongo/业务读模型，因此 `RUN_AGENT_60_LIVE_ALL=1` 全量 60 题仍不能作为严格全量验收；控制面 live 7 通过、1 个可选 manifest 测试跳过。该问题已单独保留，未冒充为已完成。
