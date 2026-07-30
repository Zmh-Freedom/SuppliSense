# 企业主数据、企业评估与供应商生命周期重构设计

## 1. 背景与问题

SuppliSense 当前把企业名称同时当作展示字段、查询参数和跨模块关联键。企业评估成功后，预警快照保存逻辑还会调用 `resolve_supplier_id(company_name, auto_create=True)`，从而把任何被评估企业自动创建为候选供应商。这导致“企业事实”和“采购关系”混在一起，也使简称、曾用名和法定名称变化可能产生重复档案、缓存分裂和历史记录断链。

当前问题的直接证据包括：

- `backend/app/domains/alert/service.py` 的 `save_snapshot()` 在保存评估快照时自动创建供应商。
- `backend/app/domains/sourcing/supplier_repo.py` 的 `resolve_supplier_id()` 先按名称精确查询，未命中时可创建 `status=prospective`、`source=auto` 的供应商。
- PostgreSQL `assessment_history` 仅保存 `company_name`，没有稳定的 `company_id`。
- 风险缓存、监控清单、告警、财务、舆情和供应商画像大量按 `company_name` 聚合。
- 前端企业评估请求仅提交 `company_name`，简称或模糊名称没有法定主体确认步骤。
- `backend/app/domains/risk/api_risk.py` 在未命中缓存的首次评估路径中，对 Pydantic 响应对象调用 `fresh.get(...)`，存在评估已执行但接口返回 500 的风险。

## 2. 已确认的业务决策

1. 企业评估可以评估任意企业，不以供应商身份为前提。
2. 成功评估自动创建或更新企业档案，但绝不自动创建供应商。
3. 企业档案整合在“企业评估”模块内，包含企业搜索、最近评估、企业档案和评估历史；不新增顶级“企业库”菜单。
4. 供应商库管理候选和正式供应商，生命周期为：

   `候选 → 考察中 → 已准入 → 合作中 → 暂停 / 淘汰 / 拉黑`

5. 简称或模糊名称必须展示候选法定主体并由用户选择；唯一精确命中可直接继续。
6. 供应商准入必须经过管理员或采购负责人的正式审批，并记录审批人、时间、意见和审计轨迹。
7. 采用完整企业主数据中心架构：企业评估、供应商、寻源、预警、报告和 Agent 最终统一使用 `company_id`。

## 3. 目标与非目标

### 3.1 目标

- 建立唯一、稳定、可合并、可审计的企业身份。
- 解耦企业事实、评估结果与供应商采购关系。
- 让评估、供应商准入、寻源、预警和 Agent 共享同一企业语义。
- 支持历史数据安全回填、双读验证、分阶段切换和回滚。
- 让跨 PostgreSQL、MongoDB、Redis 和 pgvector 的数据同步可重试、可观测、可回放。
- 保持现有 API 返回字段含义和现有前端技术栈兼容。

### 3.2 非目标

- 本项目不同时引入多租户或采购组织模型；当前供应商关系默认属于当前平台业务空间。
- 初期不引入 Kafka、RabbitMQ 等独立消息中间件，采用 PostgreSQL Transactional Outbox 和进程内消费者。
- 不一次性重写全部按名称读取的旧代码；通过兼容层和阶段门槛逐步切换。
- 不使用 LLM 自动决定法定主体、企业合并或供应商准入。
- 不在本次重构中修改风险评分算法本身。

## 4. 核心领域模型

### 4.1 Company：企业法定主体

`Company` 表示客观存在的法定主体，是评估、证据和供应商关系的共同根实体。

建议 PostgreSQL 表：

#### `companies`

| 字段 | 说明 |
|---|---|
| `id UUID PK` | 全局稳定 `company_id` |
| `legal_name VARCHAR(255)` | 当前法定名称 |
| `normalized_name VARCHAR(255)` | 标准化检索名称 |
| `unified_social_credit_code VARCHAR(32)` | 统一社会信用代码；非空时唯一 |
| `registration_status VARCHAR(32)` | 企业登记状态 |
| `verification_status VARCHAR(24)` | `verified` / `pending_verification` |
| `identity_source VARCHAR(32)` | 本地导入、天眼查、人工核验等 |
| `source_reference VARCHAR(255)` | 外部来源标识 |
| `identity_version INTEGER` | 乐观锁与身份变更版本 |
| `merged_into_id UUID NULL` | 被合并时指向规范企业 |
| `created_by UUID NULL` | 创建人 |
| `created_at / updated_at` | 时间戳 |

#### `company_aliases`

保存简称、曾用名、英文名和来源名称。别名不设置全局唯一约束，因为同一简称可能对应多个法定主体；通过标准化名称索引、来源和置信度支持候选查询。

#### `company_merge_log`

保存合并前后 ID、原因、操作者、时间和补偿信息。旧 `company_id` 不物理删除，而是通过 `merged_into_id` 解析到规范主体，保证历史引用可追溯。

### 4.2 Assessment：企业评估

评估属于企业，不属于供应商。现有 `assessment_history.id` 继续作为评估标识，逐步补充：

- `company_id UUID NULL`：迁移期可空，切换后非空并建立外键。
- `legal_name_snapshot VARCHAR(255)`：保留评估发生时的展示名称。
- `status VARCHAR(24)`：`running` / `completed` / `failed`。
- `evidence_snapshot_id VARCHAR(64)`：关联 Mongo 证据快照。
- `request_id`、`idempotency_key`：支持追踪和幂等。
- 继续保存 `scoring_version`、评分、风险详情和财务快照。

正式评分仅能绑定 `verification_status=verified` 的企业。无法可靠识别企业时，可保存“待核验档案”和调查线索，但不能生成正式评分。

### 4.3 Supplier：采购关系

供应商是企业在当前采购业务中的关系和状态，不是企业本身。目标态以 PostgreSQL 作为供应商主数据权威源，保留现有 `supplier_id` 语义：

#### `suppliers`

| 字段 | 说明 |
|---|---|
| `id UUID PK` | 稳定 `supplier_id` |
| `company_id UUID FK` | 指向法定企业 |
| `supplier_code VARCHAR(64) NULL` | 采购业务编码 |
| `status VARCHAR(24)` | 生命周期状态 |
| `source VARCHAR(24)` | `manual` / `import` / `sourcing` |
| `owner_id UUID NULL` | 负责人 |
| `version INTEGER` | 乐观锁 |
| `created_by UUID` | 显式创建人 |
| `created_at / updated_at` | 时间戳 |

同一企业在当前业务空间中只能存在一个有效供应商关系。评估、预警和只读 Agent 工具没有创建该记录的权限。

#### `supplier_status_history`

记录状态变更前后值、原因、操作者、来源请求和时间。

### 4.4 Approval：供应商准入审批

#### `approval_requests`

保存供应商、流程类型、当前状态、申请人、申请意见、材料快照、版本和时间。

#### `approval_decisions`

保存审批节点、审批人、决定、意见、审批时角色和时间。审批通过后由审批服务调用供应商状态机，不允许 API 或 Agent 绕过流程直接把供应商改为“已准入”。

### 4.5 Outbox：跨库领域事件

#### `outbox_events`

保存：

- `event_id UUID`
- `event_type`
- `aggregate_type`
- `aggregate_id`
- `schema_version`
- `payload JSONB`
- `occurred_at`
- `published_at`
- `attempt_count`
- `last_error`
- `next_attempt_at`

业务事实和 Outbox 事件在同一个 PostgreSQL 事务中提交。消费者按 `event_id + consumer_name` 幂等处理。

首批事件包括：

- `company.created`
- `company.updated`
- `company.merged`
- `assessment.completed`
- `assessment.failed`
- `supplier.created`
- `supplier.status_changed`
- `approval.submitted`
- `approval.decided`

## 5. 组件职责与数据所有权

### 5.1 Company Identity Service

唯一负责创建和修改企业身份、别名、验证状态和合并关系。输入名称后返回三类结果：

- `exact`：唯一高置信度命中，返回 `company_id`。
- `candidates`：存在多个候选，返回法定名称、信用代码、地区、登记状态和置信度，等待用户选择。
- `pending_verification`：没有可靠主体，只允许保存待核验档案。

身份解析必须使用确定性规则、统一社会信用代码和可信企业来源。LLM 只能解释候选差异，不能作权威绑定决定。

### 5.2 Assessment Service

负责评估任务、评分、模型版本、证据快照引用和历史。它可以读取企业身份和证据，但不能创建或修改供应商关系。

### 5.3 Supplier Relationship Service

负责显式创建供应商关系、生命周期状态机、负责人和状态历史。它可以读取企业档案和最新评估，但不能修改法定企业身份。

### 5.4 Admission Workflow Service

负责申请、审批、权限、并发控制和审计。只有合法审批结果可以触发受控的供应商状态变更。

### 5.5 Agent Action Gateway

LangGraph 工具优先接受 `company_id`。自然语言输入只有名称时，Agent 必须先调用身份解析工具：

1. 唯一命中时继续调用评估、查询或报告工具。
2. 多候选时向用户展示候选并暂停执行。
3. 未核验时只允许收集线索和创建待核验档案。
4. 准入、暂停、淘汰、拉黑和企业合并等高风险动作必须进行权限校验和二次确认。

Service 层保持框架无关，不引入 LangGraph 依赖。

### 5.6 数据存储职责

- PostgreSQL：企业身份、供应商关系、评估索引、审批、审计、Outbox 和 pgvector 元数据的权威事实。
- MongoDB：工商、司法、舆情、财务、图谱、报告输入和评估证据快照。每条新投影携带 `company_id` 和名称快照。
- Redis：使用 `company_id` 生成缓存键；企业更新、合并和评估完成事件触发精确失效。
- pgvector：向量元数据携带 `company_id`、`assessment_id` 或 `supplier_id`，支持重建和可追溯引用。

## 6. API 与兼容策略

### 6.1 新增企业身份 API

- `GET /api/v1/companies/search?q=...`
  - 返回精确结果或候选法定主体。
- `GET /api/v1/companies/{company_id}`
  - 返回企业主档和数据新鲜度。
- `POST /api/v1/companies`
  - 创建已验证或待核验企业档案；已验证创建需要可靠来源或管理员核验。
- `POST /api/v1/companies/{company_id}/merge`
  - 管理员动作，要求原因、版本和二次确认。
- `GET /api/v1/companies/{company_id}/assessments`
  - 返回企业评估历史。

### 6.2 企业评估兼容

保留现有 `POST /api/v1/risk/assess` 返回结构和字段含义：

- 请求新增可选 `company_id`，前端新流程优先提交它。
- 仅提交 `company_name` 时，API 先调用身份解析。
- 唯一命中时继续评估。
- 多候选时返回统一错误信封和业务冲突，前端转入候选选择流程。
- 待核验主体不能产生正式评分。
- `company_name` 在兼容期继续保留，作为展示快照而不是关联主键。

### 6.3 供应商与审批 API

- `POST /api/v1/suppliers`
  - 必须显式提交已验证的 `company_id`。
- `POST /api/v1/suppliers/{supplier_id}/status-transitions`
  - 只允许状态机定义的合法迁移。
- `POST /api/v1/suppliers/{supplier_id}/admission-requests`
  - 提交准入申请。
- `POST /api/v1/admission-requests/{request_id}/decisions`
  - 管理员或采购负责人审批，提交版本和意见。

现有供应商读取端点保持响应兼容；迁移期由适配器把 PostgreSQL 供应商事实与 Mongo 证据投影组合为原响应。

### 6.4 错误语义

沿用全局错误信封：

- `409 COMPANY_IDENTITY_AMBIGUOUS`：存在多个候选主体。
- `422 COMPANY_NOT_VERIFIED`：主体尚未核验，不能正式评估或准入。
- `409 INVALID_SUPPLIER_TRANSITION`：非法生命周期迁移。
- `409 APPROVAL_VERSION_CONFLICT`：审批并发版本冲突。
- `403 APPROVAL_FORBIDDEN`：无审批或高风险动作权限。
- `503 EVIDENCE_SOURCE_UNAVAILABLE`：关键数据源不可用且没有历史快照。

## 7. 核心数据流

### 7.1 企业评估

1. 用户输入名称或信用代码。
2. Identity Service 查询主数据和可信外部企业源。
3. 唯一命中直接返回 `company_id`；多候选由用户选择；未命中进入待核验。
4. Assessment Service 创建 `running` 任务并使用 `company_id` 获取证据。
5. Mongo 保存证据快照，评估结果保存到 PostgreSQL。
6. 结果和 `assessment.completed` 事件同事务提交。
7. 消费者更新 Mongo 读模型、失效 Redis、更新向量元数据并通知前端。
8. 整个流程不调用 Supplier Service。

### 7.2 供应商准入

1. 采购用户从已验证企业中显式创建候选供应商。
2. Supplier Service 创建 `候选` 关系和 `supplier.created` 事件。
3. 用户推进至 `考察中` 并提交准入申请及评估材料快照。
4. Admission Workflow 校验审批角色、请求版本和材料完整性。
5. 审批决定与审计记录同事务提交。
6. 审批通过后调用合法状态迁移进入 `已准入`；合作开始后进入 `合作中`。
7. 暂停、淘汰和拉黑均记录原因、操作者和状态历史。

### 7.3 Agent 查询与动作

1. IntentRouter 或 Supervisor 判断企业查询意图。
2. 身份解析工具把名称解析为 `company_id`。
3. 多候选时输出结构化候选并等待用户选择。
4. 只读工具通过 `company_id` 聚合企业、评估、供应商和证据。
5. 写操作通过 Agent Action Gateway 校验权限、版本和确认状态。
6. 工具结果保留来源、数据截至时间和可追踪 ID。

## 8. 异常处理与一致性

### 8.1 自动恢复

- Outbox 消费失败：业务事务保持成功，指数退避重试；超过阈值告警并进入人工回放队列。
- 重复请求或消息：评估使用 `idempotency_key`，消费者按事件 ID 幂等。
- 缓存失效失败：重试并缩短相关 TTL，权威查询可绕过缓存。

### 8.2 可控降级

- 外部企业、舆情或财务源不可用但存在历史快照：返回旧快照并明确 `as_of` 和 `stale`，由用户选择是否继续。
- Mongo 投影落后：企业身份和供应商状态读取 PostgreSQL，证据区域展示“同步中”。
- 未回填的旧记录：暂时按名称解析兜底并记录指标；歧义记录不得自动绑定。

### 8.3 必须阻断

- 企业主体存在歧义或未核验。
- 关键证据缺失且没有历史快照。
- 非法供应商状态迁移。
- 审批版本冲突、越权审批或高风险 Agent 动作未确认。
- 企业合并缺少管理员权限、原因或版本校验。

### 8.4 一致性不变量

- 评估、预警、寻源查询和只读 Agent 工具永远不能隐式创建供应商。
- 关键证据缺失时不能生成貌似完整的正式评分。
- 企业改名、合并和拆分必须保留来源、操作者和历史。
- 供应商准入和高风险状态变更只能通过受控工作流。
- PostgreSQL 业务事务成功后，领域事件不得丢失；下游重复消费不得产生重复副作用。

## 9. 前端产品结构

### 9.1 企业评估模块

保留现有顶级入口，内部形成统一工作台：

- 企业搜索框：支持法定名称、简称和信用代码。
- 候选主体选择：展示法定名称、信用代码、地区和登记状态。
- 最近评估：按当前用户和最近时间展示。
- 企业档案：工商基础信息、数据来源和新鲜度。
- 评估历史：评分趋势、模型版本、证据快照和失败记录。
- 评估动作：已验证企业可评估，待核验企业只显示核验入口。

### 9.2 供应商库模块

- 候选、考察中、已准入、合作中、暂停、淘汰、拉黑状态筛选。
- “新增供应商”先搜索并选择企业，再显式建立供应商关系。
- 供应商画像由供应商关系、企业档案、最新评估和证据投影组成。
- 准入申请、审批进度、审批意见和状态历史在画像中可见。
- 重新评估跳转或调用企业评估，不创建新的供应商记录。

## 10. 迁移与发布方案

### P0：切断错误耦合

- `save_snapshot()` 不再自动创建供应商。
- 监控清单改为监控企业并关联 `company_id`；是否同时存在供应商关系不影响监控。
- 修复首次评估对 Pydantic 对象调用 `.get()` 的异常。
- 添加“评估不改变供应商数量”的回归测试。

### P1：企业主数据底座

- 新建 `companies`、`company_aliases`、`company_merge_log`、`outbox_events` 和消费者处理记录。
- 实现身份搜索、解析、核验和合并服务。
- 建立 Outbox worker、重试、幂等、积压监控和人工回放能力。

### P2：评估域迁移

- `assessment_history` 增加可空 `company_id` 和名称快照。
- 新写入双写 `company_id + legal_name_snapshot`，但以 `company_id` 为主。
- 评估缓存、历史和前端逐步切换到 `company_id`。
- 对历史记录执行自动匹配、歧义报告和人工回填。
- 新读按 `company_id` 优先，旧名称读取只作临时兜底并计量。

### P3：供应商生命周期与审批

- 将供应商关系权威事实迁移到 PostgreSQL。
- 为 Mongo 供应商数据回填 `company_id`，处理重复供应商。
- 实现生命周期状态机、状态历史、准入申请和审批。
- 使用适配器维持现有供应商读取 API 的返回兼容。

### P4：下游模块与 Agent

- 寻源、监控、预警、报告、图谱和向量元数据改用 `company_id` 或 `supplier_id`。
- Agent 工具增加企业身份解析和高风险动作网关。
- 领域事件驱动投影刷新、缓存失效和通知。

### P5：正式切换与清理

- 历史歧义处理完成后，将关键 `company_id` 字段设为非空并添加外键。
- 删除按名称关联和自动创建供应商的兼容路径。
- 清理重复企业、旧缓存和失效投影。
- 观察期无阻断级告警后关闭回滚开关。

### 10.1 兼容与回滚

- 双写但不双主：`company_id` 是关联事实，名称只是快照。
- 新读优先、旧读兜底：兜底命中必须记录指标。
- 每阶段使用独立功能开关，例如身份解析、新评估读路径、供应商 PostgreSQL 读路径和事件消费者。
- 数据库变更保持向后兼容；回滚只关闭新读或消费者，不删除已经写入的 `company_id`。
- 迁移脚本默认 dry-run，输出总数、自动匹配、歧义、未匹配和校验摘要；禁止自动删除或合并记录。

## 11. 安全、审计与可观测性

- 企业合并、供应商准入、暂停、淘汰和拉黑使用 RBAC。
- 高风险 API 记录操作者、角色、请求 ID、旧值、新值、原因和时间。
- 不在日志、事件和 Agent 上下文中输出凭证或不必要的个人信息。
- 关键指标：
  - 企业身份解析结果分布和歧义率。
  - 重复企业率、未回填历史数和双读差异数。
  - Outbox 未发布数、最老积压时间、重试和死信数。
  - 供应商非法状态迁移和审批冲突数。
  - 投影延迟、缓存失效失败和 Agent 身份追问率。
- 日志和事件统一携带 `request_id`、`company_id`、`supplier_id`、`assessment_id` 和 `event_id` 中适用的字段。

## 12. 测试与验收

### 12.1 单元测试

- 名称标准化、信用代码匹配、别名候选和低置信度阻断。
- 企业合并重定向与循环合并防护。
- 供应商状态机的合法和非法迁移。
- 审批 RBAC、乐观锁和重复决定。
- Outbox 序列化、幂等键和重试策略。
- Redis 缓存键必须基于 `company_id`。

### 12.2 集成与契约测试

- PostgreSQL 业务事务与 Outbox 原子提交。
- 消费者重复投递不产生重复投影或状态变化。
- Mongo 投影、Redis 失效和 pgvector 元数据更新。
- 现有风险评估和供应商读取 API 响应保持兼容。
- 新旧读路径在相同企业上的结果一致。
- 全局错误信封和候选主体错误详情符合契约。

### 12.3 迁移测试

- 使用脱敏生产快照在预发布环境完整演练。
- 校验迁移前后记录总数、关键字段摘要和外键覆盖。
- 自动匹配结果抽样复核，所有歧义进入人工队列。
- 未解析存量清零前不得启用非空约束。
- 演练读路径回退、消费者暂停恢复和投影重建。

### 12.4 端到端旅程

1. 评估任意企业后企业档案更新，供应商数量不变。
2. 简称返回多个候选，未选择前无法正式评估。
3. 显式建立候选供应商，经授权审批进入已准入。
4. 非授权用户和 Agent 无法直接准入、淘汰或拉黑。
5. 外部证据源失败时正确显示旧快照或阻断正式评分。
6. 企业合并后旧 ID 可重定向，评估、供应商和投影引用一致。

### 12.5 上线门槛

- 新企业评估和供应商关系写入 100% 携带有效 `company_id`。
- 自动化测试证明评估、预警和只读 Agent 操作不会创建供应商。
- 历史未解析记录清零，歧义记录均已人工处理后，才启用非空约束。
- 故障注入证明 Outbox 不丢事件，重复投递无重复副作用。
- 供应商非法迁移、越权审批、并发覆盖和高风险 Agent 未确认动作全部被拒绝并审计。
- 预发布环境的切换和回滚演练通过。
- 相比当前基线，关键读写路径没有不可接受的性能退化。

### 12.6 停止发布条件

- 发现任一高置信度自动匹配绑定到错误法定主体。
- 发现领域事件丢失或重复消费产生业务副作用。
- 无法从权威数据重建 Mongo、Redis 或向量投影。
- 无法在预发布环境回退新读路径。
- 出现审批越权、审计缺失或非法状态迁移。

## 13. 实施计划拆分原则

本设计是一个目标架构，但不应形成单个巨型实施计划。书面设计审阅通过后，按依赖关系分别编写和执行：

1. P0 错误耦合与首次评估稳定性修复。
2. P1 企业主数据与 Outbox 底座。
3. P2 企业评估与历史迁移。
4. P3 供应商生命周期和准入审批。
5. P4 下游模块与 Agent 升级。
6. P5 数据切换、约束与清理。

每个子计划必须使用测试驱动开发，包含独立迁移脚本、兼容策略、验证命令和回滚检查点。只有前一阶段的发布门槛满足后，后续阶段才可进入生产切换。
