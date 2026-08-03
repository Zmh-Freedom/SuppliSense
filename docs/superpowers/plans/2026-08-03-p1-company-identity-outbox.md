# P1 企业身份主数据与 Transactional Outbox Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 建立可确定性解析、核验和逻辑合并的 PostgreSQL 企业主数据，并让企业写操作与可重试、可回放的 Outbox 事件原子提交。

**Architecture:** 新增 `company` 与 `outbox` 两个框架无关领域包；repository 的事务写方法接收调用方 cursor，由 Company Service 在单个 `get_cursor()` 中组合企业事实、审计和事件。Outbox 使用 PostgreSQL 租约与 `FOR UPDATE SKIP LOCKED` 实现至少一次投递，APScheduler 只负责触发无状态批处理入口。

**Tech Stack:** Python 3.12、FastAPI、Pydantic v2、PostgreSQL 16、psycopg2、APScheduler、Prometheus client、pytest。

## Global Constraints

- 不修改现有风险评估、供应商、监控和 Agent API 的正常响应结构。
- 不给 `assessment_history` 增加 `company_id`，不迁移 MongoDB 供应商；这些分别属于 P2、P3。
- 不引入 Kafka、RabbitMQ、Celery、Alembic 或其他新依赖。
- Service 层不得依赖 FastAPI、LangGraph 或 APScheduler。
- 企业身份只能由 Company Identity Service 修改；评估不得创建供应商。
- 名称匹配、核验与合并只使用确定性规则，不让 LLM 作权威决定。
- 跨数据库只承诺至少一次投递；消费者必须以 `event_id` 实现目标端幂等。
- 所有写任务先观察新增测试失败，再实现最小代码并观察通过。

---

### Task 1: 领域错误、企业 Schema 与确定性身份规则

**Files:**
- Create: `backend/app/schemas/company.py`
- Create: `backend/app/domains/company/__init__.py`
- Create: `backend/app/domains/company/normalization.py`
- Modify: `backend/app/core/errors.py`
- Test: `backend/tests/test_company_identity.py`
- Test: `backend/tests/test_errors.py`

**Interfaces:**
- Produces: `DomainError(code: str, message: str, status_code: int, detail: dict | None)`。
- Produces: `normalize_company_name(value: str) -> str`、`normalize_credit_code(value: str | None) -> str | None`、`validate_credit_code(value: str) -> str`。
- Produces: `VerificationStatus`、`IdentityResolutionType`、`CompanyCreateInput`、`CompanyUpdateInput`、`CompanyVerifyInput`、`CompanyMergeInput`、`CompanyResponse`、`CompanyCandidate`、`IdentityResolutionResponse`。

- [ ] **Step 1: 写错误信封和标准化失败测试**

```python
def test_normalize_company_name_uses_nfkc_casefold_and_collapses_spaces():
    assert normalize_company_name("  ＡＣＭＥ   有限公司  ") == "acme 有限公司"


@pytest.mark.parametrize("code", ["", "123", "91110000710925032I", "911100007109250325"])
def test_validate_credit_code_rejects_invalid_values(code):
    with pytest.raises(ValueError, match="统一社会信用代码"):
        validate_credit_code(code)


def test_validate_credit_code_accepts_valid_checksum():
    assert validate_credit_code(" 911100007109250324 ") == "911100007109250324"


def test_domain_error_handler_preserves_business_code():
    app = FastAPI()
    app.add_exception_handler(DomainError, domain_error_handler)

    @app.get("/boom")
    def boom():
        raise DomainError("COMPANY_CONFLICT", "企业冲突", 409, {"company_id": "c-1"})

    response = TestClient(app).get("/boom")
    assert response.status_code == 409
    assert response.json() == {"error": {"code": "COMPANY_CONFLICT", "message": "企业冲突", "detail": {"company_id": "c-1"}}}
```

- [ ] **Step 2: 运行测试确认 RED**

Run: `cd backend && pytest tests/test_company_identity.py tests/test_errors.py -v`

Expected: collection FAIL，因为 `app.domains.company.normalization`、企业 Schema 和 `DomainError` 尚不存在。

- [ ] **Step 3: 实现最小领域错误和确定性规则**

```python
# app/core/errors.py
class DomainError(Exception):
    def __init__(self, code: str, message: str, status_code: int, detail: dict | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code
        self.detail = detail


async def domain_error_handler(request: Request, exc: DomainError):
    return JSONResponse(
        status_code=exc.status_code,
        content={"error": {"code": exc.code, "message": exc.message, "detail": exc.detail}},
    )
```

```python
# app/domains/company/normalization.py
import re
import unicodedata

_CREDIT_CODE_CHARS = "0123456789ABCDEFGHJKLMNPQRTUWXY"
_CREDIT_CODE_WEIGHTS = (1, 3, 9, 27, 19, 26, 16, 17, 20, 29, 25, 13, 8, 24, 10, 30, 28)


def normalize_company_name(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).strip().casefold()
    if not normalized:
        raise ValueError("企业名称不能为空")
    return re.sub(r"\s+", " ", normalized)


def validate_credit_code(value: str) -> str:
    code = unicodedata.normalize("NFKC", value).strip().upper()
    if len(code) != 18 or any(char not in _CREDIT_CODE_CHARS for char in code):
        raise ValueError("统一社会信用代码格式无效")
    total = sum(_CREDIT_CODE_CHARS.index(char) * weight for char, weight in zip(code[:17], _CREDIT_CODE_WEIGHTS))
    expected = _CREDIT_CODE_CHARS[(31 - total % 31) % 31]
    if code[-1] != expected:
        raise ValueError("统一社会信用代码校验位无效")
    return code


def normalize_credit_code(value: str | None) -> str | None:
    return validate_credit_code(value) if value else None
```

企业 Schema 使用以下精确字段；`CompanyResponse` 另包含数据库返回的时间字段和可空 `redirected_from`：

```python
class VerificationStatus(str, Enum):
    VERIFIED = "verified"
    PENDING_VERIFICATION = "pending_verification"


class IdentityResolutionType(str, Enum):
    EXACT = "exact"
    CANDIDATES = "candidates"
    PENDING_VERIFICATION = "pending_verification"


class CompanyAliasInput(BaseModel):
    alias_name: str = Field(min_length=1, max_length=255)
    alias_type: Literal["short_name", "former_name", "english_name", "source_name"]
    source: str = Field(default="manual", min_length=1, max_length=32)
    confidence: float = Field(default=1.0, ge=0, le=1)


class CompanyCreateInput(BaseModel):
    legal_name: str = Field(min_length=2, max_length=255)
    unified_social_credit_code: str | None = None
    registration_status: str | None = Field(default=None, max_length=32)
    verification_status: VerificationStatus = VerificationStatus.PENDING_VERIFICATION
    identity_source: str = Field(default="manual", min_length=1, max_length=32)
    source_reference: str | None = Field(default=None, max_length=255)
    aliases: list[CompanyAliasInput] = Field(default_factory=list)


class CompanyUpdateInput(BaseModel):
    expected_version: int = Field(ge=1)
    legal_name: str | None = Field(default=None, min_length=2, max_length=255)
    unified_social_credit_code: str | None = None
    registration_status: str | None = Field(default=None, max_length=32)
    identity_source: str | None = Field(default=None, min_length=1, max_length=32)
    source_reference: str | None = Field(default=None, max_length=255)


class CompanyVerifyInput(BaseModel):
    expected_version: int = Field(ge=1)
    unified_social_credit_code: str | None = None
    identity_source: Literal["tianyancha", "import", "admin_verified"]
    source_reference: str | None = Field(default=None, max_length=255)


class CompanyMergeInput(BaseModel):
    target_company_id: UUID
    source_expected_version: int = Field(ge=1)
    target_expected_version: int = Field(ge=1)
    reason: str = Field(min_length=2, max_length=500)
    confirm: bool


class CompanyResponse(BaseModel):
    company_id: UUID
    legal_name: str
    normalized_name: str
    unified_social_credit_code: str | None
    registration_status: str | None
    verification_status: VerificationStatus
    identity_source: str
    source_reference: str | None
    identity_version: int
    merged_into_id: UUID | None
    created_at: datetime
    updated_at: datetime
    verified_at: datetime | None
    redirected_from: UUID | None = None


class CompanyCandidate(BaseModel):
    company_id: UUID
    legal_name: str
    unified_social_credit_code: str | None
    registration_status: str | None
    verification_status: VerificationStatus
    match_type: Literal["credit_code", "legal_name", "alias", "prefix"]
    confidence: float
    redirected_from: UUID | None = None


class IdentityResolutionResponse(BaseModel):
    resolution: IdentityResolutionType
    exact: CompanyCandidate | None = None
    candidates: list[CompanyCandidate] = Field(default_factory=list)
```

- [ ] **Step 4: 运行测试确认 GREEN**

Run: `cd backend && pytest tests/test_company_identity.py tests/test_errors.py -v`

Expected: 全部 PASS。

- [ ] **Step 5: 提交基础类型**

```bash
git add backend/app/core/errors.py backend/app/schemas/company.py backend/app/domains/company backend/tests/test_company_identity.py backend/tests/test_errors.py
git commit -m "feat: add company identity primitives"
```

---

### Task 2: 幂等 PostgreSQL 表与事务内审计

**Files:**
- Modify: `backend/app/db/init_pg.py`
- Modify: `backend/app/domains/auth/audit_repo.py`
- Test: `backend/tests/test_company_schema.py`
- Test: `backend/tests/test_audit_repo.py`

**Interfaces:**
- Produces: 五张 P1 表和对应约束、索引。
- Produces: `create_log_with_cursor(cur: PgCursor, action: str, user_id: str | None = None, resource_type: str | None = None, resource_id: str | None = None, details: dict | None = None, ip_address: str | None = None, user_agent: str | None = None) -> str`；现有 `create_log()` 保持签名和行为。

- [ ] **Step 1: 写 DDL 与事务内审计失败测试**

```python
def test_p1_ddl_contains_required_tables_and_indexes():
    ddl = "\n".join(DDL_STATEMENTS + INDEX_STATEMENTS)
    for table in ("companies", "company_aliases", "company_merge_log", "outbox_events", "outbox_consumptions"):
        assert f"CREATE TABLE IF NOT EXISTS {table}" in ddl
    assert "FOR UPDATE" not in ddl
    assert "idx_outbox_pending" in ddl


def test_create_log_with_cursor_does_not_open_or_commit_transaction():
    cur = MagicMock()
    log_id = create_log_with_cursor(cur, action="company.merge", user_id=None)
    assert UUID(log_id)
    cur.execute.assert_called_once()
```

- [ ] **Step 2: 运行测试确认 RED**

Run: `cd backend && pytest tests/test_company_schema.py tests/test_audit_repo.py -v`

Expected: FAIL，缺少 P1 DDL 和 `create_log_with_cursor`。

- [ ] **Step 3: 添加表、约束和索引**

在 `DDL_STATEMENTS` 的 users/audit 表之后添加五张表。关键 SQL 必须包括：

```sql
CREATE TABLE IF NOT EXISTS companies (
    id UUID PRIMARY KEY,
    legal_name VARCHAR(255) NOT NULL,
    normalized_name VARCHAR(255) NOT NULL,
    unified_social_credit_code VARCHAR(18) UNIQUE,
    registration_status VARCHAR(32),
    verification_status VARCHAR(24) NOT NULL CHECK (verification_status IN ('verified', 'pending_verification')),
    identity_source VARCHAR(32) NOT NULL,
    source_reference VARCHAR(255),
    identity_version INTEGER NOT NULL DEFAULT 1 CHECK (identity_version > 0),
    merged_into_id UUID REFERENCES companies(id),
    created_by UUID REFERENCES users(id) ON DELETE SET NULL,
    verified_by UUID REFERENCES users(id) ON DELETE SET NULL,
    verified_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CHECK (merged_into_id IS NULL OR merged_into_id <> id)
)
```

`company_aliases` 对 `(company_id, normalized_alias)` 唯一；`company_merge_log` 保存双版本和 `compensation_snapshot JSONB`；`outbox_events` 包含租约、死信与重试字段；`outbox_consumptions` 以 `(event_id, consumer_name)` 为主键并引用事件。索引包含标准化名称、别名、合并目标和待处理 Outbox 部分索引。

将 `audit_repo.create_log()` 重构为打开事务后调用：

```python
def create_log_with_cursor(
    cur: PgCursor,
    action: str,
    user_id: str | None = None,
    resource_type: str | None = None,
    resource_id: str | None = None,
    details: dict | None = None,
    ip_address: str | None = None,
    user_agent: str | None = None,
) -> str:
    log_id = str(uuid.uuid4())
    cur.execute(
        """INSERT INTO audit_logs
           (id, user_id, action, resource_type, resource_id, details, ip_address, user_agent)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s)""",
        (
            log_id,
            user_id,
            action,
            resource_type,
            resource_id,
            Json(details) if details is not None else None,
            ip_address,
            user_agent,
        ),
    )
    return log_id
```

- [ ] **Step 4: 运行测试和真实幂等初始化**

Run: `cd backend && pytest tests/test_company_schema.py tests/test_audit_repo.py -v`

Run: `cd backend && python -c "from app.db.init_pg import ensure_pg_schema; ensure_pg_schema(); ensure_pg_schema()"`

Expected: 测试 PASS，连续初始化两次均退出 0。

- [ ] **Step 5: 提交数据库底座**

```bash
git add backend/app/db/init_pg.py backend/app/domains/auth/audit_repo.py backend/tests/test_company_schema.py backend/tests/test_audit_repo.py
git commit -m "feat: add company and outbox database schema"
```

---

### Task 3: Outbox 事务原语、重试和幂等消费

**Files:**
- Create: `backend/app/schemas/outbox.py`
- Create: `backend/app/domains/outbox/__init__.py`
- Create: `backend/app/domains/outbox/repo.py`
- Create: `backend/app/domains/outbox/service.py`
- Test: `backend/tests/test_outbox_service.py`

**Interfaces:**
- Produces: `enqueue_event(cur, event_type: str, aggregate_type: str, aggregate_id: str, payload: dict, schema_version: int = 1) -> str`。
- Produces: `register_consumer(event_type: str, consumer_name: str, handler: Callable[[dict], None]) -> None`。
- Produces: `process_outbox_batch(worker_id: str, batch_size: int, max_attempts: int, lease_seconds: int) -> dict`。
- Produces: `list_events(status: str, limit: int) -> list[dict]`、`replay_event(event_id: str, reason: str, actor_id: str | None) -> dict`。

- [ ] **Step 1: 写 Outbox 失败测试**

```python
def test_enqueue_event_uses_supplied_cursor_and_serializes_payload():
    cur = MagicMock()
    event_id = enqueue_event(cur, "company.created", "company", "c-1", {"company_id": "c-1"})
    assert UUID(event_id)
    assert cur.execute.call_args.args[1][5].adapted == {"company_id": "c-1"}


def test_process_batch_skips_already_consumed_handler(monkeypatch):
    event = {"event_id": "e-1", "event_type": "company.created", "payload": {}}
    handler = MagicMock()
    register_consumer("company.created", "audit", handler)
    monkeypatch.setattr(outbox_repo, "claim_events", lambda **kwargs: [event])
    monkeypatch.setattr(outbox_repo, "is_consumed", lambda event_id, consumer_name: True)
    monkeypatch.setattr(outbox_repo, "mark_published", MagicMock())
    result = process_outbox_batch("worker-1", 50, 8, 60)
    handler.assert_not_called()
    assert result == {"claimed": 1, "published": 1, "failed": 0}


@pytest.mark.parametrize("attempt,seconds", [(1, 2), (7, 128), (9, 300)])
def test_retry_delay_is_exponential_and_capped(attempt, seconds):
    assert retry_delay_seconds(attempt) == seconds
```

- [ ] **Step 2: 运行测试确认 RED**

Run: `cd backend && pytest tests/test_outbox_service.py -v`

Expected: collection FAIL，Outbox 模块不存在。

- [ ] **Step 3: 实现 repository 与 service**

`claim_events()` 使用一个短事务执行：

```sql
SELECT * FROM outbox_events
WHERE published_at IS NULL
  AND dead_lettered_at IS NULL
  AND next_attempt_at <= NOW()
  AND (locked_until IS NULL OR locked_until < NOW())
ORDER BY next_attempt_at, occurred_at
FOR UPDATE SKIP LOCKED
LIMIT %s
```

然后在同一事务更新 `locked_by`、`locked_until` 并返回字典。`process_outbox_batch()` 逐事件调用已注册消费者；成功消费写 `(event_id, consumer_name)`，全部成功后 `mark_published()`。失败调用 `mark_failed()`，递增尝试次数，设置 `min(2 ** attempt_count, 300)` 秒后重试；达到 `max_attempts` 时写 `dead_lettered_at`。

消费者注册表使用 `(event_type, consumer_name)` 唯一键；重复注册同名不同 handler 直接抛 `ValueError`。注册 `company_event_audit` 空副作用消费者：

```python
def _company_event_audit(event: dict) -> None:
    logger.info("company_event_consumed", event_id=event["event_id"], event_type=event["event_type"])


for event_type in ("company.created", "company.updated", "company.verified", "company.merged"):
    register_consumer(event_type, "company_event_audit", _company_event_audit)
```

`app/schemas/outbox.py` 使用以下模型：

```python
class OutboxReplayInput(BaseModel):
    reason: str = Field(min_length=2, max_length=500)


class OutboxEventResponse(BaseModel):
    event_id: UUID
    event_type: str
    aggregate_type: str
    aggregate_id: str
    schema_version: int
    occurred_at: datetime
    published_at: datetime | None
    attempt_count: int
    last_error: str | None
    next_attempt_at: datetime
    dead_lettered_at: datetime | None


class OutboxReplayResponse(BaseModel):
    event_id: UUID
    status: Literal["queued"]
    reason: str
```

- [ ] **Step 4: 运行测试确认 GREEN**

Run: `cd backend && pytest tests/test_outbox_service.py -v`

Expected: 全部 PASS。

- [ ] **Step 5: 提交 Outbox 核心**

```bash
git add backend/app/schemas/outbox.py backend/app/domains/outbox backend/tests/test_outbox_service.py
git commit -m "feat: add transactional outbox core"
```

---

### Task 4: 企业 Repository、规范主体重定向与身份解析

**Files:**
- Create: `backend/app/domains/company/repo.py`
- Create: `backend/app/domains/company/service.py`
- Test: `backend/tests/test_company_service.py`

**Interfaces:**
- Produces: `get_company(company_id: str) -> dict | None`，返回规范主体并带 `redirected_from: str | None`。
- Produces: `search_identity(query: str, limit: int = 10) -> dict`，返回 `resolution`、`exact`、`candidates`。
- Repository 写函数接收 `PgCursor`；读函数自行使用 `get_cursor()`。

- [ ] **Step 1: 写身份解析失败测试**

```python
def test_verified_unique_legal_name_is_exact(monkeypatch):
    monkeypatch.setattr(company_repo, "search_identity_rows", lambda query, limit: [
        {"id": "c-1", "legal_name": "示例有限公司", "verification_status": "verified", "match_type": "legal_name", "confidence": 1.0, "merged_into_id": None}
    ])
    result = search_identity("示例有限公司")
    assert result["resolution"] == "exact"
    assert result["exact"]["company_id"] == "c-1"


def test_pending_unique_match_requires_verification(monkeypatch):
    monkeypatch.setattr(company_repo, "search_identity_rows", lambda query, limit: [
        {"id": "c-2", "legal_name": "待核验公司", "verification_status": "pending_verification", "match_type": "legal_name", "confidence": 1.0, "merged_into_id": None}
    ])
    assert search_identity("待核验公司")["resolution"] == "pending_verification"


def test_low_confidence_alias_never_auto_resolves(monkeypatch):
    monkeypatch.setattr(company_repo, "search_identity_rows", lambda query, limit: [
        {"id": "c-3", "legal_name": "别名公司", "verification_status": "verified", "match_type": "alias", "confidence": 0.8, "merged_into_id": None}
    ])
    assert search_identity("简称")["resolution"] == "candidates"


def test_redirect_cycle_raises_integrity_error(monkeypatch):
    monkeypatch.setattr(company_repo, "get_company_row", lambda company_id: {"id": company_id, "merged_into_id": {"a": "b", "b": "a"}[company_id]})
    with pytest.raises(DomainError) as exc:
        get_company("a")
    assert exc.value.code == "COMPANY_MERGE_INTEGRITY_ERROR"
```

- [ ] **Step 2: 运行测试确认 RED**

Run: `cd backend && pytest tests/test_company_service.py -v`

Expected: FAIL，repository/service 尚不存在。

- [ ] **Step 3: 实现查询与解析**

`search_identity_rows()` 对信用代码精确、法定名称精确、别名精确和名称前缀分别查询，再按 company ID 去重。稳定排序键为：信用代码、法定名称、别名、前缀；verified 优先；confidence 降序；legal_name 和 ID 升序。

`get_company()` 最多跟随 20 次 `merged_into_id`，使用 `seen: set[str]` 检测循环；未找到返回 `None`，断链或循环抛 `COMPANY_MERGE_INTEGRITY_ERROR`。`search_identity()` 把所有候选先规范化重定向再判定三类结果。

- [ ] **Step 4: 运行测试确认 GREEN**

Run: `cd backend && pytest tests/test_company_service.py -v`

Expected: 全部 PASS。

- [ ] **Step 5: 提交企业读取与解析**

```bash
git add backend/app/domains/company/repo.py backend/app/domains/company/service.py backend/tests/test_company_service.py
git commit -m "feat: add deterministic company identity resolution"
```

---

### Task 5: 企业创建、更新和核验的原子事务

**Files:**
- Modify: `backend/app/domains/company/repo.py`
- Modify: `backend/app/domains/company/service.py`
- Test: `backend/tests/test_company_commands.py`
- Test: `backend/tests/test_company_transactions.py`

**Interfaces:**
- Produces: `create_company(data: CompanyCreateInput, actor_id: str | None, actor_role: str) -> dict`。
- Produces: `update_company(company_id: str, data: CompanyUpdateInput, actor_id: str | None, actor_role: str) -> dict`。
- Produces: `verify_company(company_id: str, data: CompanyVerifyInput, actor_id: str | None, actor_role: str) -> dict`。

- [ ] **Step 1: 写权限、冲突和原子性失败测试**

```python
def test_analyst_cannot_create_verified_company():
    data = CompanyCreateInput(legal_name="示例有限公司", verification_status="verified", identity_source="admin_verified", source_reference="manual-review")
    with pytest.raises(DomainError) as exc:
        create_company(data, "u-1", "analyst")
    assert exc.value.code == "COMPANY_VERIFICATION_FORBIDDEN"


def test_create_writes_company_audit_and_event_with_same_cursor(monkeypatch):
    cur = MagicMock()
    @contextmanager
    def fake_get_cursor():
        yield MagicMock(), cur
    monkeypatch.setattr(company_service, "get_cursor", fake_get_cursor)
    monkeypatch.setattr(company_repo, "find_by_credit_code_with_cursor", lambda cur, code: None)
    monkeypatch.setattr(company_repo, "insert_company", MagicMock(return_value={"id": "c-1", "identity_version": 1}))
    enqueue = MagicMock(return_value="e-1")
    monkeypatch.setattr(company_service, "enqueue_event", enqueue)
    monkeypatch.setattr(company_service, "create_log_with_cursor", MagicMock())
    result = create_company(CompanyCreateInput(legal_name="示例有限公司"), "u-1", "analyst")
    assert result["company_id"] == "c-1"
    assert enqueue.call_args.args[0] is cur


def test_update_version_conflict_returns_domain_error(monkeypatch):
    monkeypatch.setattr(company_repo, "update_company", lambda *args, **kwargs: None)
    with pytest.raises(DomainError) as exc:
        update_company("c-1", CompanyUpdateInput(expected_version=2, registration_status="active"), "u-1", "admin")
    assert exc.value.code == "COMPANY_VERSION_CONFLICT"
```

- [ ] **Step 2: 运行测试确认 RED**

Run: `cd backend && pytest tests/test_company_commands.py tests/test_company_transactions.py -v`

Expected: FAIL，命令函数尚不存在。

- [ ] **Step 3: 实现三个命令**

所有命令只打开一个 `get_cursor()`；repository SQL 均使用该 cursor。创建前校验角色、核验来源和信用代码冲突，并在企业行后用同一 cursor 插入 `data.aliases`（别名同样先标准化）；更新使用 `WHERE identity_version = expected_version AND merged_into_id IS NULL` 并递增版本；核验只允许 admin 且要求合法信用代码或可信来源引用。

每个成功命令依次写企业事实、`create_log_with_cursor()` 和 `enqueue_event()`，事件 payload 至少包含 `company_id`、`legal_name`、`identity_version`、`verification_status`。捕获 PostgreSQL `UniqueViolation` 并转为 `409 COMPANY_ALREADY_EXISTS`，detail 返回冲突企业 ID（能查询到时）。

- [ ] **Step 4: 运行测试确认 GREEN**

Run: `cd backend && pytest tests/test_company_commands.py tests/test_company_transactions.py -v`

Expected: 全部 PASS，失败分支离开 context 时触发 rollback。

- [ ] **Step 5: 提交企业命令**

```bash
git add backend/app/domains/company/repo.py backend/app/domains/company/service.py backend/tests/test_company_commands.py backend/tests/test_company_transactions.py
git commit -m "feat: add transactional company commands"
```

---

### Task 6: 乐观锁企业合并与循环防护

**Files:**
- Modify: `backend/app/domains/company/repo.py`
- Modify: `backend/app/domains/company/service.py`
- Test: `backend/tests/test_company_merge.py`

**Interfaces:**
- Produces: `merge_company(source_company_id: str, data: CompanyMergeInput, actor_id: str | None, actor_role: str) -> dict`。
- Repository produces: `lock_companies_for_merge(cur, ids: list[str]) -> list[dict]`，按 UUID 排序并 `FOR UPDATE`。

- [ ] **Step 1: 写合并失败测试**

```python
SOURCE_ID = "00000000-0000-0000-0000-000000000001"
TARGET_ID = "00000000-0000-0000-0000-000000000002"


@pytest.mark.parametrize("role", ["viewer", "analyst"])
def test_merge_requires_admin(role):
    with pytest.raises(DomainError) as exc:
        merge_company(SOURCE_ID, CompanyMergeInput(target_company_id=TARGET_ID, reason="重复档案", confirm=True, source_expected_version=1, target_expected_version=1), "u-1", role)
    assert exc.value.status_code == 403


def test_merge_requires_explicit_confirmation():
    with pytest.raises(DomainError) as exc:
        merge_company(SOURCE_ID, CompanyMergeInput(target_company_id=TARGET_ID, reason="重复档案", confirm=False, source_expected_version=1, target_expected_version=1), "u-1", "admin")
    assert exc.value.code == "COMPANY_MERGE_CONFIRMATION_REQUIRED"


def test_merge_rejects_conflicting_credit_codes(monkeypatch):
    monkeypatch.setattr(company_repo, "lock_companies_for_merge", lambda cur, ids: [
        {"id": SOURCE_ID, "identity_version": 1, "unified_social_credit_code": "911100007109250324", "merged_into_id": None},
        {"id": TARGET_ID, "identity_version": 1, "unified_social_credit_code": "91440300708461136T", "merged_into_id": None},
    ])
    data = CompanyMergeInput(
        target_company_id=TARGET_ID,
        reason="重复档案",
        confirm=True,
        source_expected_version=1,
        target_expected_version=1,
    )
    with pytest.raises(DomainError) as exc:
        merge_company(SOURCE_ID, data, "u-1", "admin")
    assert exc.value.code == "COMPANY_MERGE_IDENTITY_CONFLICT"
```

- [ ] **Step 2: 运行测试确认 RED**

Run: `cd backend && pytest tests/test_company_merge.py -v`

Expected: FAIL，合并命令和锁函数不存在。

- [ ] **Step 3: 实现原子逻辑合并**

拒绝源等于目标；固定顺序锁两行；校验两行存在、未被合并、版本一致且信用代码不冲突。检查目标重定向链不包含源。成功后写 `company_merge_log` 的补偿快照，更新源 `merged_into_id`，递增两边版本，同事务写 `company.merge` 审计和 `company.merged` 事件。

返回：

```python
{
    "source_company_id": source_company_id,
    "target_company_id": target_company_id,
    "source_version": source_version + 1,
    "target_version": target_version + 1,
    "merged": True,
}
```

- [ ] **Step 4: 运行测试确认 GREEN**

Run: `cd backend && pytest tests/test_company_merge.py tests/test_company_transactions.py -v`

Expected: 全部 PASS。

- [ ] **Step 5: 提交合并能力**

```bash
git add backend/app/domains/company/repo.py backend/app/domains/company/service.py backend/tests/test_company_merge.py
git commit -m "feat: add auditable company merging"
```

---

### Task 7: 企业 API、RBAC 与统一业务错误

**Files:**
- Create: `backend/app/domains/company/api.py`
- Modify: `backend/app/main.py`
- Test: `backend/tests/test_company_identity_api.py`

**Interfaces:**
- Produces: `/api/v1/companies/search`、`/{company_id}`、根 POST、`/{company_id}` PATCH、`/{company_id}/verify`、`/{company_id}/merge`。
- Consumes: Tasks 4-6 的 Company Service 公共函数。

- [ ] **Step 1: 写路由、权限和错误信封失败测试**

```python
def test_company_search_requires_auth(client):
    assert client.get("/api/v1/companies/search", params={"q": "示例"}).status_code == 401


def test_company_search_returns_resolution(client, auth_headers, monkeypatch):
    monkeypatch.setattr(company_api, "search_identity", lambda q, limit: {"resolution": "candidates", "exact": None, "candidates": []})
    response = client.get("/api/v1/companies/search", params={"q": "示例"}, headers=auth_headers)
    assert response.status_code == 200
    assert response.json()["resolution"] == "candidates"


def test_domain_conflict_uses_business_error_code(client, auth_headers, monkeypatch):
    def fail(*args, **kwargs):
        raise DomainError("COMPANY_VERSION_CONFLICT", "企业版本冲突", 409, {"expected_version": 2})
    monkeypatch.setattr(company_api, "update_company", fail)
    response = client.patch("/api/v1/companies/00000000-0000-0000-0000-000000000001", json={"expected_version": 2, "registration_status": "active"}, headers=auth_headers)
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "COMPANY_VERSION_CONFLICT"
```

- [ ] **Step 2: 运行测试确认 RED**

Run: `cd backend && pytest tests/test_company_identity_api.py -v`

Expected: FAIL/404，路由未注册。

- [ ] **Step 3: 实现并注册路由**

router 使用 `prefix="/companies"` 和 `dependencies=[Depends(get_current_user)]`。读 API 使用任何已认证角色；创建/更新使用 `require_admin_or_analyst`；核验和合并使用 `require_admin`。同步 service 全部通过 `asyncio.to_thread()` 调用。操作者 ID 和角色从 dependency 返回的 `UserInDB` 获取，不信任请求体角色。

在 `main.py` 注册 `DomainError` handler 和 company router；OpenAPI 增加 `companies` 标签。不存在返回 `DomainError("COMPANY_NOT_FOUND", "企业不存在", 404)`，不使用自由格式 dict 响应。

- [ ] **Step 4: 运行测试确认 GREEN**

Run: `cd backend && pytest tests/test_company_identity_api.py tests/test_company.py tests/test_auth.py -v`

Expected: 全部 PASS。

- [ ] **Step 5: 提交企业 API**

```bash
git add backend/app/domains/company/api.py backend/app/main.py backend/tests/test_company_identity_api.py
git commit -m "feat: expose company identity API"
```

---

### Task 8: Outbox Worker、调度配置与 Prometheus 指标

**Files:**
- Create: `backend/app/domains/outbox/worker.py`
- Modify: `backend/app/core/config.py`
- Modify: `backend/app/core/metrics.py`
- Modify: `backend/app/domains/company/service.py`
- Modify: `backend/app/services/scheduler.py`
- Modify: `backend/.env.example`
- Modify: `.env.docker.example`
- Test: `backend/tests/test_outbox_worker.py`
- Test: `backend/tests/test_scheduler.py`

**Interfaces:**
- Produces: `run_outbox_once() -> dict`。
- Produces settings: `OUTBOX_WORKER_ENABLED`、`OUTBOX_POLL_SECONDS`、`OUTBOX_BATCH_SIZE`、`OUTBOX_MAX_ATTEMPTS`、`OUTBOX_LEASE_SECONDS`。
- Produces metrics: `OUTBOX_PENDING_EVENTS`、`OUTBOX_OLDEST_PENDING_AGE_SECONDS`、`OUTBOX_EVENTS_PROCESSED_TOTAL`、`OUTBOX_RETRIES_TOTAL`、`COMPANY_IDENTITY_RESOLUTIONS_TOTAL`。

- [ ] **Step 1: 写 worker 与调度失败测试**

```python
def test_worker_uses_configured_limits(monkeypatch):
    process = MagicMock(return_value={"claimed": 0, "published": 0, "failed": 0})
    monkeypatch.setattr(outbox_worker, "process_outbox_batch", process)
    monkeypatch.setattr(settings, "OUTBOX_BATCH_SIZE", 25)
    monkeypatch.setattr(settings, "OUTBOX_MAX_ATTEMPTS", 6)
    monkeypatch.setattr(settings, "OUTBOX_LEASE_SECONDS", 45)
    run_outbox_once()
    assert process.call_args.kwargs["batch_size"] == 25
    assert process.call_args.kwargs["max_attempts"] == 6
    assert process.call_args.kwargs["lease_seconds"] == 45


def test_scheduler_adds_interval_outbox_job_when_enabled(monkeypatch):
    add_job = MagicMock()
    monkeypatch.setattr(scheduler._scheduler, "add_job", add_job)
    monkeypatch.setattr(settings, "OUTBOX_WORKER_ENABLED", True)
    monkeypatch.setattr(settings, "OUTBOX_POLL_SECONDS", 7)
    scheduler._add_outbox_job()
    add_job.assert_called_once_with(scheduler._scheduled_outbox, "interval", seconds=7, id="outbox_worker", max_instances=1, coalesce=True)
```

- [ ] **Step 2: 运行测试确认 RED**

Run: `cd backend && pytest tests/test_outbox_worker.py tests/test_scheduler.py -v`

Expected: FAIL，worker、配置和调度入口不存在。

- [ ] **Step 3: 实现 worker、指标和 interval job**

`run_outbox_once()` 生成 `f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:8]}"` worker ID，调用批处理并刷新 pending 数与最老积压秒数。处理成功/失败与 retry 分别更新低基数 Counter；身份解析函数按 `resolution` 更新 Counter。

`start_scheduler()` 在现有 cron jobs 之后调用 `_add_outbox_job()`；关闭开关时不注册。`stop_scheduler()` 在 scheduler 未启动时不抛异常，以保持测试和部分启动失败时可清理。

两个环境示例文件增加五项非敏感配置及注释，不修改用户本机 `.env`。

- [ ] **Step 4: 运行测试确认 GREEN**

Run: `cd backend && pytest tests/test_outbox_worker.py tests/test_scheduler.py tests/test_scripts.py tests/test_compose.py -v`

Expected: 全部 PASS。

- [ ] **Step 5: 提交 worker 与可观测性**

```bash
git add backend/app/domains/outbox/worker.py backend/app/core/config.py backend/app/core/metrics.py backend/app/domains/company/service.py backend/app/services/scheduler.py backend/.env.example .env.docker.example backend/tests/test_outbox_worker.py backend/tests/test_scheduler.py
git commit -m "feat: run and monitor outbox worker"
```

---

### Task 9: 管理员 Outbox 运维 API、真实集成与发布门槛

**Files:**
- Create: `backend/app/domains/outbox/api.py`
- Modify: `backend/app/domains/outbox/repo.py`
- Modify: `backend/app/domains/outbox/service.py`
- Modify: `backend/app/main.py`
- Create: `backend/tests/test_outbox_api.py`
- Create: `backend/tests/test_company_outbox_integration.py`
- Modify: `README.md`

**Interfaces:**
- Produces: `GET /api/v1/admin/outbox/events` 和 `POST /api/v1/admin/outbox/events/{event_id}/replay`。
- Consumes: `list_events()`、`replay_event()` 与所有 P1 企业命令。

- [ ] **Step 1: 写管理员 API 与 PostgreSQL 集成失败测试**

```python
@pytest.fixture
def pg_cursor():
    with get_cursor() as (_conn, cur):
        yield cur


def test_outbox_events_requires_admin(client, monkeypatch):
    analyst = UserInDB(
        id="00000000-0000-0000-0000-000000000010",
        username="p1-analyst",
        email="p1-analyst@example.com",
        role="analyst",
        password_hash="unused",
        created_at=datetime.now(timezone.utc),
        is_active=True,
    )
    monkeypatch.setattr("app.core.deps.get_user_by_id", lambda user_id: analyst)
    token = create_access_token({"sub": analyst.id})
    response = client.get("/api/v1/admin/outbox/events", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 403


def test_replay_requires_reason(client, auth_headers):
    response = client.post("/api/v1/admin/outbox/events/00000000-0000-0000-0000-000000000001/replay", json={"reason": ""}, headers=auth_headers)
    assert response.status_code == 422


def test_company_and_event_commit_atomically(pg_cursor):
    unique_name = f"P1集成测试-{uuid.uuid4()}"
    company = create_company(CompanyCreateInput(legal_name=unique_name), None, "admin")
    pg_cursor.execute("SELECT event_type, payload FROM outbox_events WHERE aggregate_id = %s", (company["company_id"],))
    row = pg_cursor.fetchone()
    assert row[0] == "company.created"
    assert row[1]["company_id"] == company["company_id"]


def test_company_and_event_roll_back_together(monkeypatch, pg_cursor):
    unique_name = f"P1回滚测试-{uuid.uuid4()}"
    monkeypatch.setattr(company_service, "enqueue_event", MagicMock(side_effect=RuntimeError("event insert failed")))
    with pytest.raises(RuntimeError):
        create_company(CompanyCreateInput(legal_name=unique_name), None, "admin")
    pg_cursor.execute("SELECT COUNT(*) FROM companies WHERE legal_name = %s", (unique_name,))
    assert pg_cursor.fetchone()[0] == 0
```

- [ ] **Step 2: 运行测试确认 RED**

Run: `cd backend && pytest tests/test_outbox_api.py tests/test_company_outbox_integration.py -v`

Expected: API 测试 404，集成测试在缺少完整事务行为处 FAIL。

- [ ] **Step 3: 实现运维 API 与集成 fixture**

运维 router 使用 `prefix="/admin/outbox"`、`Depends(require_admin)`；列表 status 只能是 `pending`、`failed`、`dead_letter`，limit 为 `1..100`。回放 body 使用 `OutboxReplayInput(reason: str = Field(min_length=2, max_length=500))`，Service 只允许未发布的失败/死信事件，保留成功 consumption，写事务内审计。

集成 fixture 生成独立 UUID 和名称，测试结束仅按这些 UUID 删除 `outbox_consumptions`、`outbox_events`、`company_merge_log`、`company_aliases`、`companies`，禁止清空整表。

README 增加企业身份 API、Outbox 配置、积压查看和死信回放说明，明确 P1 尚未切换评估与供应商读写。

- [ ] **Step 4: 运行 P1 全部测试**

Run: `cd backend && pytest tests/test_company_identity.py tests/test_errors.py tests/test_company_schema.py tests/test_audit_repo.py tests/test_outbox_service.py tests/test_company_service.py tests/test_company_commands.py tests/test_company_transactions.py tests/test_company_merge.py tests/test_company_identity_api.py tests/test_outbox_worker.py tests/test_scheduler.py tests/test_outbox_api.py tests/test_company_outbox_integration.py -v`

Expected: 全部 PASS。

- [ ] **Step 5: 运行完整验证门槛**

Run: `cd backend && pytest -v`

Expected: 全部 PASS。

Run: `cd frontend && npm run lint`

Expected: exit 0。

Run: `cd frontend && npm run build`

Expected: exit 0。

Run: `docker compose --env-file backend/.env -f docker-compose.dev.yml config --quiet`

Expected: exit 0。

Run: `bash -n start.sh stop.sh backup.sh restore.sh`

Expected: exit 0。

Run: 在一个 PTY 中执行 `cd backend && python -m uvicorn app.main:app --host 127.0.0.1 --port 8011`，看到 `Application startup complete` 后在另一命令执行 `curl --fail --silent --show-error --max-time 15 http://127.0.0.1:8011/health/ready`，最后向 PTY 发送 Ctrl-C。

Expected: HTTP 200，`mongo`、`redis`、`postgres` 均为 `ok`；随后正常关闭临时进程。

- [ ] **Step 6: 最终边界审计**

Run: `rg -n "auto_create=True|company_id|outbox" backend/app`

Expected: `auto_create=True` 仅存在于显式供应商准入路径；P1 企业写入不调用 Supplier Service；Outbox 业务写入均传递已有 cursor。

- [ ] **Step 7: 提交运维 API 与集成验证**

```bash
git add backend/app/domains/outbox/api.py backend/app/domains/outbox/repo.py backend/app/domains/outbox/service.py backend/app/main.py backend/tests/test_outbox_api.py backend/tests/test_company_outbox_integration.py README.md
git commit -m "feat: expose outbox operations and integration tests"
```
