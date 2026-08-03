# P0 企业评估与供应商边界修复 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 阻止企业评估、企业监控和工商数据抓取隐式创建供应商，并修复首次未命中缓存的评估在审计阶段返回 500。

**Architecture:** P0 不引入尚未建设的 `company_id` 主数据底座，而是在现有名称型兼容架构中先切断三个通用读/采集流程对 Supplier Repository 的自动创建权限。已有供应商仍通过 `resolve_supplier_id(name)` 关联；不存在供应商时继续保存 `supplier_id=None`，保持 Mongo 文档和 API 字段兼容。首次评估继续返回 `RiskCalculateResponse`，审计读取改用 Pydantic 属性。

**Tech Stack:** Python 3.11、FastAPI、Pydantic v2、PyMongo、pytest、`unittest.mock`。

## Global Constraints

- 不修改现有 API 正常响应结构或字段含义。
- 不新增第三方依赖。
- 不实现 P1 的 `company_id`、企业身份解析或 PostgreSQL Outbox。
- 评估、监控和工商数据抓取不得隐式创建供应商。
- 显式提交供应商准入申请的现有创建行为不在 P0 中改变。
- 使用测试驱动开发：先观察新增测试失败，再写最小实现并观察通过。
- 保留工作区中的所有无关改动，不修改供应商画像工作树。

---

### Task 1: 切断通用企业流程的供应商自动创建

**Files:**
- Create: `backend/tests/test_supplier_creation_boundaries.py`
- Modify: `backend/app/domains/alert/service.py`
- Modify: `backend/app/services/tianyancha_client.py`

**Interfaces:**
- Consumes: `resolve_supplier_id(name: str, auto_create: bool = False) -> str | None`。
- Produces: `save_snapshot()`、`add_to_watchlist()` 和 `_save()` 只查找已有供应商；未命中时保存或返回 `supplier_id=None`。
- Preserves: `create_access_application()` 仍是当前架构中的显式供应商业务动作，不在本任务修改。

- [ ] **Step 1: 写供应商创建边界失败测试**

创建 `backend/tests/test_supplier_creation_boundaries.py`：

```python
from unittest.mock import MagicMock

from app.domains.alert import service as alert_service
from app.schemas import RiskCalculateResponse
from app.services import tianyancha_client


def _capture_supplier_resolution(monkeypatch, supplier_id: str | None = None):
    auto_create_values: list[bool] = []

    def fake_resolve_supplier_id(
        name: str,
        auto_create: bool = False,
    ) -> str | None:
        auto_create_values.append(auto_create)
        return supplier_id

    monkeypatch.setattr(
        "app.domains.sourcing.supplier_repo.resolve_supplier_id",
        fake_resolve_supplier_id,
    )
    return auto_create_values


def test_save_snapshot_does_not_auto_create_supplier(monkeypatch):
    db = MagicMock()
    monkeypatch.setattr(alert_service, "get_db", lambda: db)
    auto_create_values = _capture_supplier_resolution(monkeypatch)

    alert_service.save_snapshot(
        "非供应商企业有限公司",
        RiskCalculateResponse(risk_score=20, risk_level="低风险"),
    )

    assert auto_create_values == [False]
    saved = db["alert_snapshots"].insert_one.call_args.args[0]
    assert saved["supplier_id"] is None


def test_save_snapshot_links_existing_supplier(monkeypatch):
    db = MagicMock()
    monkeypatch.setattr(alert_service, "get_db", lambda: db)
    auto_create_values = _capture_supplier_resolution(
        monkeypatch,
        supplier_id="supplier-123",
    )

    alert_service.save_snapshot(
        "已有供应商有限公司",
        RiskCalculateResponse(risk_score=20, risk_level="低风险"),
    )

    assert auto_create_values == [False]
    saved = db["alert_snapshots"].insert_one.call_args.args[0]
    assert saved["supplier_id"] == "supplier-123"


def test_add_to_watchlist_does_not_auto_create_supplier(monkeypatch):
    db = MagicMock()
    monkeypatch.setattr(alert_service, "get_db", lambda: db)
    monkeypatch.setattr(alert_service, "_broadcast_alert_update", lambda: None)
    auto_create_values = _capture_supplier_resolution(monkeypatch)

    result = alert_service.add_to_watchlist("监控企业有限公司")

    assert auto_create_values == [False]
    assert result["supplier_id"] is None
    update = db["watchlist"].update_one.call_args.args[1]["$set"]
    assert update["supplier_id"] is None


def test_tianyancha_save_does_not_auto_create_supplier(monkeypatch):
    db = MagicMock()
    monkeypatch.setattr(tianyancha_client, "get_db", lambda: db)
    auto_create_values = _capture_supplier_resolution(monkeypatch)

    tianyancha_client._save("baseinfo", "查询企业有限公司", {"ok": True}, "items")

    assert auto_create_values == [False]
    update = db["baseinfo"].update_one.call_args.args[1]["$set"]
    assert update["supplier_id"] is None
```

- [ ] **Step 2: 运行测试并确认 RED**

Run: `cd backend && pytest tests/test_supplier_creation_boundaries.py -v`

Expected: 4 个测试均 FAIL，`auto_create_values` 实际为 `[True]`。

- [ ] **Step 3: 写最小边界修复**

在 `backend/app/domains/alert/service.py` 的 `save_snapshot()` 和 `add_to_watchlist()` 中只查找已有供应商：

```python
"supplier_id": resolve_supplier_id(company_name),
```

```python
sid = resolve_supplier_id(company_name)
```

在 `backend/app/services/tianyancha_client.py` 的 `_save()` 中同样只查找已有供应商：

```python
sid = resolve_supplier_id(name)
```

继续写入现有 `supplier_id` 字段；没有匹配供应商时字段值为 `None`，不删除字段、不改变响应结构。

- [ ] **Step 4: 运行测试并确认 GREEN**

Run: `cd backend && pytest tests/test_supplier_creation_boundaries.py -v`

Expected: 4 tests PASS。

- [ ] **Step 5: 运行关联回归测试**

Run: `cd backend && pytest tests/test_alert.py tests/test_risk_service.py tests/test_supplier_creation_boundaries.py -v`

Expected: 全部 PASS。

- [ ] **Step 6: 提交边界修复**

```bash
git add backend/tests/test_supplier_creation_boundaries.py backend/app/domains/alert/service.py backend/app/services/tianyancha_client.py
git commit -m "fix: stop implicit supplier creation"
```

---

### Task 2: 修复首次未缓存评估的审计异常

**Files:**
- Create: `backend/tests/test_risk_api.py`
- Modify: `backend/app/domains/risk/api_risk.py`

**Interfaces:**
- Consumes: `assess_risk(request: RiskAssessRequest) -> RiskCalculateResponse`。
- Produces: 未命中缓存时返回同一个 `RiskCalculateResponse`，并调用 `log_action(details={"risk_score": int, "risk_level": str})`。
- Preserves: `/api/v1/risk/assess` 的响应模型、缓存字段和超时行为。

- [ ] **Step 1: 写首次评估失败测试**

创建 `backend/tests/test_risk_api.py`：

```python
import asyncio
from unittest.mock import MagicMock

from fastapi import BackgroundTasks

from app.domains.risk import api_risk
from app.schemas import RiskAssessRequest, RiskCalculateResponse


def test_uncached_risk_assessment_audits_pydantic_response(monkeypatch):
    db = MagicMock()
    db["alert_snapshots"].find_one.return_value = None
    monkeypatch.setattr("app.db.mongo.get_db", lambda: db)

    fresh = RiskCalculateResponse(
        risk_score=37,
        risk_level="中风险",
        risk_detail={"lawsuit_count": 1},
    )
    monkeypatch.setattr(api_risk, "assess_risk", lambda request: fresh)

    audit_call: dict = {}

    def fake_log_action(**kwargs) -> None:
        audit_call.update(kwargs)

    monkeypatch.setattr("app.domains.auth.audit.log_action", fake_log_action)

    result = asyncio.run(
        api_risk.risk_assess(
            RiskAssessRequest(company_name="首次评估企业有限公司"),
            BackgroundTasks(),
        )
    )

    assert result is fresh
    assert result.cached_at is not None
    assert result.cache_age_hours == 0
    assert result.is_stale is False
    assert audit_call["details"] == {
        "risk_score": 37,
        "risk_level": "中风险",
    }
```

- [ ] **Step 2: 运行测试并确认 RED**

Run: `cd backend && pytest tests/test_risk_api.py::test_uncached_risk_assessment_audits_pydantic_response -v`

Expected: FAIL with `AttributeError: 'RiskCalculateResponse' object has no attribute 'get'`。

- [ ] **Step 3: 改用 Pydantic 属性记录审计**

在 `backend/app/domains/risk/api_risk.py` 中把审计详情改为：

```python
details={
    "risk_score": fresh.risk_score,
    "risk_level": fresh.risk_level,
},
```

不把响应转换成 `dict`，继续返回 `RiskCalculateResponse`。

- [ ] **Step 4: 运行测试并确认 GREEN**

Run: `cd backend && pytest tests/test_risk_api.py::test_uncached_risk_assessment_audits_pydantic_response -v`

Expected: 1 test PASS。

- [ ] **Step 5: 运行风险 API 关联回归**

Run: `cd backend && pytest tests/test_risk.py tests/test_risk_api.py tests/test_risk_service.py -v`

Expected: 全部 PASS。

- [ ] **Step 6: 提交首次评估修复**

```bash
git add backend/tests/test_risk_api.py backend/app/domains/risk/api_risk.py
git commit -m "fix: audit uncached risk assessments"
```

---

### Task 3: P0 全量验证与创建路径审计

**Files:**
- Verify only: `backend/app/`
- Verify only: `backend/tests/`

**Interfaces:**
- Consumes: Task 1 和 Task 2 的两个独立提交。
- Produces: P0 验证证据和允许保留的显式供应商创建路径清单。

- [ ] **Step 1: 运行 P0 聚焦测试**

Run: `cd backend && pytest tests/test_supplier_creation_boundaries.py tests/test_risk_api.py tests/test_alert.py tests/test_risk.py tests/test_risk_service.py -v`

Expected: 全部 PASS，0 failures。

- [ ] **Step 2: 运行后端全量测试**

Run: `cd backend && pytest -v`

Expected: 全部 PASS，0 failures；若环境依赖测试被明确 skip，记录 skip 原因和数量。

- [ ] **Step 3: 审计剩余自动创建调用**

Run: `rg -n "auto_create=True" backend/app`

Expected: 通用企业读取、评估、预警和工商抓取路径中没有 `auto_create=True`。允许保留：

- `backend/app/domains/sourcing/repo.py` 的 `create_access_application()`，因为它是用户显式提交供应商准入申请的业务动作。
- `backend/app/domains/sourcing/supplier_repo.py` 中关于 `auto_create` 参数的实现和说明。

如出现其他调用，不直接删除；先判断是否为显式供应商写操作。非显式路径必须补充失败测试并改为默认查找。

- [ ] **Step 4: 检查提交与工作区**

Run: `git diff --check && git status --short && git log -3 --oneline`

Expected: `git diff --check` 退出码 0；工作区无未提交 P0 文件；日志包含 Task 1 和 Task 2 的两个提交。

## 完成定义

- 企业评估完成不会创建供应商。
- 添加企业监控不会创建供应商。
- 工商数据抓取不会创建供应商。
- 已存在供应商仍能写入 `supplier_id` 关联；不存在时保持 `None`。
- 首次未缓存评估成功返回 Pydantic 响应并写入正确审计详情。
- 聚焦测试和后端全量测试通过。
- 剩余 `auto_create=True` 仅存在于明确的供应商写业务或参数实现中。
