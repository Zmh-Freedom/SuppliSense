# P1 企业身份主数据与 Transactional Outbox 设计

## 1. 目标

P1 在不切换现有评估、供应商和 Agent 读写路径的前提下，建立企业法定主体的 PostgreSQL 权威主数据，并提供可靠的 Transactional Outbox 基础设施。P1 完成后，系统能够确定性地搜索、解析、核验和合并企业身份，并保证企业领域事实与领域事件在同一数据库事务中提交。

P1 分为两个可独立评审的里程碑：

1. **P1-A 企业主数据**：企业、别名、核验、身份解析和逻辑合并。
2. **P1-B Transactional Outbox**：事件原子写入、租约领取、幂等消费、重试、积压监控和管理员回放。

## 2. 范围与非目标

### 2.1 本阶段范围

- 新增 `companies`、`company_aliases`、`company_merge_log`、`outbox_events` 和 `outbox_consumptions`。
- 新增企业身份领域的 repository、service、Pydantic schema 和 API。
- 新增企业创建、更新、核验、合并领域事件。
- 新增进程内 Outbox worker、消费者注册、重试、Prometheus 指标和管理员运维 API。
- 保持新增表和索引的初始化幂等，兼容当前 `ensure_pg_schema()` 启动模式。

### 2.2 非目标

- P1 不修改风险评分算法，不给 `assessment_history` 增加 `company_id`；这属于 P2。
- P1 不把 MongoDB 供应商迁移到 PostgreSQL，也不实现供应商状态机和审批；这属于 P3。
- P1 不改造监控、报告、图谱、向量元数据或 Agent 工具；这属于 P4。
- P1 不调用 LLM 判断企业身份，不自动合并企业，不自动创建供应商。
- P1 不引入 Kafka、RabbitMQ、Celery 或 Alembic 等新依赖。
- P1 不改变现有 API 的正常响应结构；新增 API 使用新的明确契约。

## 3. 代码边界

新增 `app/domains/company/`：

- `normalization.py`：名称标准化、统一社会信用代码格式与校验位验证；无数据库依赖。
- `repo.py`：企业、别名和合并日志的 PostgreSQL 数据访问；写方法接受调用方提供的 cursor，以支持事务组合。
- `service.py`：身份搜索、解析、创建、更新、核验和合并；唯一有权改变企业身份。
- `api.py`：`/api/v1/companies` 路由、RBAC、业务异常到统一错误信封的映射。

新增 `app/domains/outbox/`：

- `repo.py`：事件写入、租约领取、成功/失败状态、消费记录和回放状态的数据访问。
- `service.py`：事件序列化、消费者注册、批处理、重试和人工回放。
- `worker.py`：供 APScheduler 调用的单批轮询入口，不持有 Web 或 LangGraph 状态。
- `api.py`：管理员积压查询、失败事件查询和人工回放 API。

新增 `app/schemas/company.py` 和 `app/schemas/outbox.py` 存放公开请求与响应模型。Service 层保持框架无关，不依赖 FastAPI、LangGraph 或 APScheduler。

## 4. PostgreSQL 模型

### 4.1 `companies`

| 字段 | 类型与约束 |
|---|---|
| `id` | `UUID PRIMARY KEY` |
| `legal_name` | `VARCHAR(255) NOT NULL` |
| `normalized_name` | `VARCHAR(255) NOT NULL`，普通索引 |
| `unified_social_credit_code` | `VARCHAR(18) NULL`，非空值唯一 |
| `registration_status` | `VARCHAR(32) NULL` |
| `verification_status` | `VARCHAR(24) NOT NULL`，仅 `verified` / `pending_verification` |
| `identity_source` | `VARCHAR(32) NOT NULL` |
| `source_reference` | `VARCHAR(255) NULL` |
| `identity_version` | `INTEGER NOT NULL DEFAULT 1` |
| `merged_into_id` | `UUID NULL REFERENCES companies(id)` |
| `created_by` | `UUID NULL REFERENCES users(id) ON DELETE SET NULL` |
| `verified_by` | `UUID NULL REFERENCES users(id) ON DELETE SET NULL` |
| `verified_at` | `TIMESTAMPTZ NULL` |
| `created_at` / `updated_at` | `TIMESTAMPTZ NOT NULL DEFAULT NOW()` |

约束：企业不能合并到自身；已核验企业必须具有合法统一社会信用代码，或具有非空可信 `source_reference`。P1 支持的可信来源为 `tianyancha`、`import` 和 `admin_verified`；普通人工录入使用 `manual`，只能创建待核验主体。

### 4.2 `company_aliases`

保存 `company_id`、`alias_name`、`normalized_alias`、`alias_type`、`source`、`confidence`、创建人与时间。同一企业的同一标准化别名只保存一次；不同企业允许使用相同别名，以保留歧义。

`alias_type` 首期支持 `short_name`、`former_name`、`english_name` 和 `source_name`。`confidence` 范围为 `0..1`，只参与候选排序，不允许仅凭低置信度别名自动绑定。

### 4.3 `company_merge_log`

保存 `id`、`source_company_id`、`target_company_id`、`reason`、`operator_id`、源和目标合并前版本、补偿快照及时间。合并是逻辑重定向：源企业设置 `merged_into_id`，不删除企业、别名或历史。

### 4.4 `outbox_events`

保存 `event_id`、`event_type`、`aggregate_type`、`aggregate_id`、`schema_version`、`payload JSONB`、`occurred_at`、`published_at`、`attempt_count`、`last_error`、`next_attempt_at`、`locked_by`、`locked_until` 和 `dead_lettered_at`。

待处理索引覆盖 `published_at IS NULL AND dead_lettered_at IS NULL`，按 `next_attempt_at, occurred_at` 排序。`event_id` 是消费者跨存储幂等键。

### 4.5 `outbox_consumptions`

以 `(event_id, consumer_name)` 为联合主键，保存 `processed_at`。它防止同一消费者重复处理已成功事件。跨 PostgreSQL 的消费者仍必须在目标存储中以 `event_id` 建立幂等记录；系统承诺至少一次投递，不宣称跨数据库恰好一次。

## 5. 确定性身份规则

### 5.1 名称标准化

名称使用 Unicode NFKC、首尾去空白、连续空白折叠和 Unicode `casefold()`。不删除“有限公司”等法定后缀，不使用 LLM 或模糊模型生成标准名称，避免错误合并不同法定主体。

### 5.2 统一社会信用代码

输入转为大写并移除首尾空白后，按 GB 32100-2015 的 18 位字符集、位置权重和校验位验证。非法代码返回校验错误，不写入数据库。

### 5.3 搜索与解析

解析结果固定为三类：

- `exact`：信用代码唯一命中，或标准化法定名称唯一命中一个 `verified` 规范主体。
- `candidates`：法定名称、别名或前缀查询命中多个主体；按匹配类型、核验状态、置信度和法定名称稳定排序。
- `pending_verification`：无可靠命中，或唯一命中主体仍待核验。

别名精确命中即使只有一个，也只在该别名属于一个已核验规范主体且 `confidence >= 0.95` 时返回 `exact`；否则返回候选。包含匹配和前缀匹配永不自动返回 `exact`。

任何命中已合并企业的查询都沿 `merged_into_id` 解析到规范主体，并保留 `redirected_from`。解析设置最大跳数且检测重复 ID；发现循环或断链时抛出数据完整性错误，不猜测目标。

## 6. 写操作与并发控制

### 6.1 创建与更新

- `admin` 和 `analyst` 可以创建 `pending_verification` 企业。
- 只有 `admin` 可以直接创建或更新为 `verified`。
- 信用代码冲突返回 `409 COMPANY_ALREADY_EXISTS`，响应 detail 包含现有 `company_id`。
- 更新请求必须提交 `expected_version`；SQL 使用 `WHERE id = ... AND identity_version = ...`，冲突返回 `409 COMPANY_VERSION_CONFLICT`。
- 企业创建、更新、核验和合并都必须在同一 PostgreSQL 事务中写入对应 Outbox 事件。

### 6.2 核验

核验是独立管理员动作，需要信用代码或可信来源引用。成功后记录 `verified_by`、`verified_at`，递增 `identity_version`，并写入 `company.verified`。

### 6.3 合并

合并仅限管理员，输入 `target_company_id`、`reason`、源与目标 `expected_version` 和 `confirm=true`。Service 按 UUID 固定顺序 `SELECT ... FOR UPDATE` 锁定两行，验证二者均为当前规范主体，拒绝自合并、循环、已被并发修改和信用代码互相冲突的合并。

成功时写入 `company_merge_log`，设置源企业 `merged_into_id`，递增源和目标版本，在同一事务写审计记录与 `company.merged` 事件。事件 payload 包含源 ID、目标 ID、操作者、原因和新版本，不包含凭证或不必要的个人信息。

## 7. Outbox 处理模型

### 7.1 原子写入

`enqueue_event(cur, ...)` 必须接收当前业务事务的 cursor，禁止内部提交。企业 Service 通过一次 `get_cursor()` 上下文完成业务事实、审计和事件写入；任一 SQL 失败时整体回滚。

首批事件：`company.created`、`company.updated`、`company.verified`、`company.merged`，统一 `schema_version=1`。

### 7.2 领取与消费

worker 每批使用 `FOR UPDATE SKIP LOCKED` 领取到期事件，并写入 `locked_by` 与 `locked_until` 租约。每个消费者先检查 `(event_id, consumer_name)`；成功后记录 consumption。全部已注册消费者成功后设置 `published_at` 并清除租约。

P1 注册一个无外部副作用的 `company_event_audit` 基础消费者，用于验证注册、幂等和生命周期。Mongo、Redis、pgvector 等真实投影消费者在 P2/P4 增加，并必须以 `event_id` 实现目标端幂等。

### 7.3 重试与死信

失败后保存截断的安全错误文本，递增 `attempt_count`，清除租约，并设置：

`next_attempt_at = now + min(2 ** attempt_count, 300) seconds`

默认最大尝试次数为 8；达到上限设置 `dead_lettered_at`，不再自动领取。日志包含 `event_id`、事件类型、尝试次数和 request ID（如有），不得包含完整敏感 payload。

### 7.4 人工回放

管理员只能回放死信或明确失败的未发布事件，必须提交非空原因。回放清除死信、错误、租约和下次执行时间，但保留历史尝试次数以及已成功的 consumption 记录；因此只重试尚未成功的消费者。回放动作写入审计日志。

### 7.5 调度与配置

Outbox worker 作为 APScheduler interval job 启动。配置项：

- `OUTBOX_WORKER_ENABLED=true`
- `OUTBOX_POLL_SECONDS=5`
- `OUTBOX_BATCH_SIZE=50`
- `OUTBOX_MAX_ATTEMPTS=8`
- `OUTBOX_LEASE_SECONDS=60`

多进程部署允许多个 worker 并发运行，依靠租约和 `SKIP LOCKED` 分配事件。关闭功能开关只停止消费，不影响业务事务和事件写入，构成 P1 回滚开关。

## 8. API 契约与权限

企业 API 使用 `/api/v1/companies`：

- `GET /search?q=...`：所有已认证角色；返回 `resolution`、`exact`、`candidates`。
- `GET /{company_id}`：所有已认证角色；旧 ID 返回规范主体并包含 `redirected_from`。
- `POST /`：`admin`、`analyst`；analyst 只能创建待核验企业。
- `PATCH /{company_id}`：`admin`、`analyst`；analyst 不能改变核验状态和权威身份字段。
- `POST /{company_id}/verify`：仅 `admin`。
- `POST /{company_id}/merge`：仅 `admin`，要求二次确认字段。

运维 API 使用 `/api/v1/admin/outbox`，全部仅 `admin`：

- `GET /events?status=pending|failed|dead_letter&limit=...`
- `POST /events/{event_id}/replay`

Service 抛出带业务码的领域异常，由 `app/core/errors.py` 新增的领域异常处理器转换为现有统一信封：`{"error":{"code","message","detail"}}`。现有 `HTTPException`、校验异常和未处理异常格式保持不变。

## 9. 可观测性

新增低基数 Prometheus 指标：

- `outbox_pending_events`
- `outbox_oldest_pending_age_seconds`
- `outbox_events_processed_total{event_type,status}`
- `outbox_retries_total{event_type}`
- `company_identity_resolutions_total{resolution}`

结构化日志在适用位置携带 `company_id`、`event_id`、`event_type`、`attempt_count` 和 `operator_id`。不把企业查询文本作为 Prometheus label。

## 10. 测试策略

### 10.1 单元测试

- NFKC、空白和大小写标准化。
- 信用代码字符集与校验位。
- 信用代码、法定名称、别名和前缀匹配的三类解析结果。
- 待核验主体不能成为 `exact`。
- 合并重定向、自合并、循环、信用代码冲突和版本冲突。
- Outbox 序列化、退避上限、租约、消费幂等和死信回放。

### 10.2 PostgreSQL 集成测试

- 新表与索引初始化可重复运行。
- 企业写入与事件写入同时提交或同时回滚。
- 并发 worker 不重复领取同一事件。
- 重复投递不重复执行已成功消费者。
- 企业合并锁与乐观版本阻止并发覆盖。

集成测试使用现有本地 PostgreSQL，不删除非测试数据；每个测试使用独立 UUID 并在事务或显式清理中隔离。

### 10.3 API 与回归测试

- 未认证、角色不足、参数错误、冲突和不存在资源均返回正确 HTTP 状态及统一错误信封。
- 管理员核验、合并、查询积压和回放成功。
- 原有风险评估、供应商、监控和认证测试保持通过。
- 前端 `npm run lint` 与 `npm run build` 保持通过；P1 不新增前端页面。

## 11. 发布、回滚与验收门槛

发布顺序：先部署向后兼容 DDL，再启用企业 API，最后开启 Outbox worker。P1 表均为新增表，现有业务路径不读取它们。

回滚时先关闭 `OUTBOX_WORKER_ENABLED`，再回滚应用版本；不删除新表、不删除已写企业和事件。重新部署后可继续处理未发布事件。

P1 完成必须满足：

1. 企业身份写操作 100% 通过 Company Identity Service。
2. 企业事实和 Outbox 事件原子性集成测试通过。
3. 重复消费无重复副作用，失败可自动重试并由管理员回放。
4. 合并循环、错误信用代码、越权、并发覆盖和未确认合并全部被拒绝并审计。
5. 后端完整测试、前端 lint/build、Compose/脚本检查和真实 readiness 冒烟全部通过。
6. 现有评估和供应商 API 契约无变化，评估仍不会隐式创建供应商。
