# 简洁对话与业务卡片实施计划

> 执行方式：逐任务实施、验证并提交；实施前阅读对应设计和项目规则。

**目标：** 让聊天自然回答问题，仅在有可展示业务数据时提供卡片，并保留真实证据与审批边界。

**架构：** 继续使用 Harness、单次会话上下文和现有 SSE/AgentAnswer 契约；服务端区分非业务对话与业务任务，前端以一处正文呈现答案，再按真实数据与事件展示卡片、依据、进度和审批。

**技术栈：** FastAPI、LangGraph、pytest、React、TypeScript、Vitest。

**设计：** `docs/superpowers/specs/2026-10-29-conversational-chat-design.md`

## 全局约束

- 不改已有 API 返回结构、SSE 事件契约、service 分层或人工审批入口；不新增活动聊天内核。
- 未知、失败、未查询和确认空结果分别表述；不将缺证据称为无风险或已发现风险。
- 仅根据真实数据、已有授权和可恢复事件呈现内容；不假定历史投影保存完整过程。
- 修复前先在 `docs/issue-log.md` 记录问题；该文件已有用户未提交改动，提交前必须隔离本次条目，禁止包含已有改动。

## 重点回归

- 混合问候和业务请求仍进入业务路径；短确认、主体名称和候选序号不打断现有会话。
- 数据失败、未查询和已确认空结果不能互换；无证据不显示风险发现。
- 模型删除限制、反转判断、虚构建议或引用时回退到可信内容。
- 实时消息、刷新、旧历史字段缺失和跨设备恢复以实际持久化能力为准。
- 写操作确认、拒绝和恢复保持原流程；流式与最终答案不重复。

### 任务 1：记录问题并处理非业务对话

**文件：** `docs/issue-log.md`、`backend/app/graphs/harness/graph.py`、必要时 `backend/app/graphs/agent_core/capability_routing.py`、`backend/tests/test_agent_chat_full_chain.py` 和相关 Harness 测试。

- [ ] 补记问候落入兜底、错误复核和固定界面的问题，标明与历史 ISS-20261012-025 的关系及本次尚未验证的状态。
- [ ] 新增 API/SSE 离线测试：问候、感谢、能力介绍给出自然完成态且零业务工具；问候加业务问题正常执行；待确认上下文中的“好的”、主体名和候选序号保留原语义。先运行确认失败。
- [ ] 在 Harness 的无任务回答分支中，依据已有归一化任务及上下文识别明确的纯对话，提供简短本地回答；歧义及业务混合输入继续走原路由。
- [ ] 运行上述测试及相关意图、澄清和 Harness 测试；回填问题记录并只提交本任务相关变更。

### 任务 2：约束自然语言答案

**文件：** `backend/app/graphs/agent_core/narrator.py`、`backend/app/graphs/harness/graph.py`、`backend/tests/test_agent_narrator.py`、相关 Harness 测试。

- [ ] 为模型输出新增回归：缺失关键限制、反转已验证结论、无依据采购建议以及混淆来源失败/确认空结果时，不采用不安全输出；保持原有实体、数字、引用和动作校验。先确认失败。
- [ ] 扩展校验或采用可验证的确定性拼装；不能可靠核实的语义不作为事实显示，并保留结构化限制和原答案作为回退。模型不可用与预算耗尽可正常返回。
- [ ] 运行 narrator 和相关 Harness 测试，记录结果并提交本任务相关文件。

### 任务 3：正文、卡片和证据

**文件：** `frontend/src/components/ChatMessageList.tsx`、`frontend/src/components/StructuredAgentResult.tsx`、`frontend/src/components/answerCapabilities.ts`、`frontend/src/__tests__/ChatView.test.tsx`、`frontend/src/__tests__/StructuredAgentResult.test.tsx`。

- [ ] 先增加测试：纯问候仅有正文；流式 `agent_answer` 与 `answer_chunk` 不重复；有支持数据时出现对应卡片；无证据无空报告卡和“发现信号”；失败、无结果及限制可按真实状态查看。
- [ ] 保持一个正文来源，去除重复摘要及无依据的默认采购建议；卡片仅由可展示的真实数据驱动，依据按现有授权和有效性展示；`needs_review` 不单独映射为发现风险。
- [ ] 验证风险、寻源、比较、监控以及筛查等已有卡片不丢失，运行相关 Vitest、typecheck 和 lint；提交本任务相关文件。

### 任务 4：真实进度、历史与审批

**文件：** `frontend/src/components/AgentWorkflowPanel.tsx`、`frontend/src/components/ChatMessageList.tsx`、`frontend/src/components/ChatView.tsx`、相关前端测试；必要时检查 `backend/app/graphs/streaming.py` 和历史投影。

- [ ] 核对实时 SSE 与会话持久化字段，列出历史可恢复范围；添加测试覆盖运行中、完成后折叠、失败/取消、历史字段缺失、刷新和审批确认/拒绝。
- [ ] 移除仅由关键词和生命周期推断的完成里程碑；只显示已有事件可支持的进度，历史无过程记录时不重建，审批卡片及操作保持可见和可用。
- [ ] 运行针对性 Vitest、typecheck、lint 及 build；提交本任务相关文件。

### 任务 5：端到端验收与收尾

**文件：** 相关 API/SSE 与前端测试、`docs/issue-log.md`。

- [ ] 运行离线 API/SSE 和前端回归、项目要求的 Agent E2E；依照项目数据库约定确认隧道和服务，真实模型及浏览器验证问候、业务查询、缺数据、审批和刷新。无法执行时记录具体限制，不能以 mock 冒充真实验证。
- [ ] 复核答案、事实来源、状态、步骤、审批和窄屏/键盘体验；问题先记日志再修复。回填验证和关联提交，检查 `git diff --check`、分支状态及改动范围。
