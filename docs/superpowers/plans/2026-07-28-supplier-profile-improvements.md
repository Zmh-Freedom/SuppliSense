# 供应商画像完善 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让供应商画像具备可靠主数据、可执行动作、趋势与审计追溯能力。

**Architecture:** 后端在供应商领域服务中按需从工商缓存或查询结果回填行业，并在不破坏既有响应的前提下扩展画像数据。前端复用现有风险和监控接口，以 TanStack Query mutation 驱动动作后缓存失效，并在原画像页增强展示。

**Tech Stack:** FastAPI、Pydantic v2、PyMongo、pytest、React 19、TypeScript、TanStack Query、Tailwind、recharts、@xyflow/react。

## Global Constraints

- 不改变既有 API 字段含义或删除字段，只增加可选字段。
- 不新增第三方依赖。
- API 层以 `asyncio.to_thread()` 包装同步 service/repository。
- 生产代码前先写并运行失败测试（RED），实现后再运行通过测试（GREEN）。
- 保留工作区内与本任务无关的未提交改动。

---

### Task 1: 行业按需回填

**Files:**
- Modify: `backend/app/domains/supplier/service.py`
- Modify: `backend/app/domains/supplier/repo.py`
- Modify: `backend/tests/test_supplier_profile_service.py`

**Interfaces:**
- Produces: `_ensure_industry(master: dict) -> dict`，为缺失行业的供应商返回回填后的主数据。
- Consumes: `baseinfo` 缓存、现有工商查询能力、`update_supplier(supplier_id, data)`。

- [ ] **Step 1: 写失败测试**

```python
def test_profile_backfills_industry_from_cached_baseinfo(monkeypatch):
    master = {"_id": "supplier-1", "name": "测试供应商", "categories": ["电子制造"]}
    monkeypatch.setattr("app.domains.supplier.repo.get_supplier", lambda _: master.copy())
    monkeypatch.setattr(
        "app.domains.supplier.service._load_cached_industry",
        lambda _: "计算机通信和其他电子设备制造业",
    )
    written = {}
    monkeypatch.setattr(
        "app.domains.supplier.repo.update_supplier",
        lambda supplier_id, data: written.update({"id": supplier_id, **data}),
    )

    profile = build_supplier_profile("supplier-1")

    assert profile["basic_info"]["industry"] == "计算机通信和其他电子设备制造业"
    assert written == {"id": "supplier-1", "industry": "计算机通信和其他电子设备制造业"}
```

- [ ] **Step 2: 验证 RED**

Run: `cd backend && pytest tests/test_supplier_profile_service.py::test_profile_backfills_industry_from_cached_baseinfo -v`

Expected: FAIL，因为行业回填函数不存在。

- [ ] **Step 3: 实现最小逻辑**

```python
def _ensure_industry(master: dict) -> dict:
    if master.get("industry"):
        return master
    industry = _load_cached_industry(master["name"]) or _fetch_industry(master["name"])
    if not industry:
        return master
    update_supplier(str(master["_id"]), {"industry": industry})
    return {**master, "industry": industry}
```

在 `build_supplier_profile` 中调用该函数；从缓存复用既有 `_resolve_category` 解析规则；远程查询或回写异常记录日志并保留原主数据。

- [ ] **Step 4: 补充边界测试**

```python
def test_profile_keeps_existing_industry_without_querying(monkeypatch):
    master = {"_id": "supplier-1", "name": "测试供应商", "industry": "软件和信息技术服务业"}
    monkeypatch.setattr("app.domains.supplier.service._load_cached_industry", lambda _: pytest.fail("must not load"))
    assert _ensure_industry(master)["industry"] == "软件和信息技术服务业"

def test_profile_preserves_categories_when_industry_is_unavailable(monkeypatch):
    master = {"_id": "supplier-1", "name": "测试供应商", "categories": ["电子制造"]}
    monkeypatch.setattr("app.domains.supplier.service._load_cached_industry", lambda _: None)
    monkeypatch.setattr("app.domains.supplier.service._fetch_industry", lambda _: None)
    assert _ensure_industry(master) == master
```

断言已有行业不会被覆盖；没有行业数据时 `industry is None` 且 `categories` 原样返回。

- [ ] **Step 5: 验证 GREEN**

Run: `cd backend && pytest tests/test_supplier_profile_service.py -v`

Expected: PASS。

- [ ] **Step 6: 提交**

```bash
git add backend/app/domains/supplier/service.py backend/app/domains/supplier/repo.py backend/tests/test_supplier_profile_service.py
git commit -m "feat: 画像按需回填供应商行业"
```

### Task 2: 聚合趋势、告警和可跳转关联数据

**Files:**
- Modify: `backend/app/domains/supplier/service.py`
- Modify: `backend/app/schemas/supplier.py`
- Modify: `backend/tests/test_supplier_profile_service.py`

**Interfaces:**
- Produces: `financial.history: list[dict]`、告警的完整变化字段、关联实体可选 `supplier_id`。
- Consumes: `financial_cache.history`、`alerts`、`suppliers` 主数据集合。

- [ ] **Step 1: 写失败测试**

```python
def test_financial_snapshot_exposes_cached_history(monkeypatch):
    cache = {"metrics": {}, "history": [{"period": "2025", "revenue_growth": 8.0}]}
    monkeypatch.setattr("app.domains.supplier.service.get_db", fake_db(cache))

    assert _build_financial_snapshot("测试供应商", {})["history"] == cache["history"]
```

- [ ] **Step 2: 验证 RED**

Run: `cd backend && pytest tests/test_supplier_profile_service.py::test_financial_snapshot_exposes_cached_history -v`

Expected: FAIL，因为 `history` 不在快照响应中。

- [ ] **Step 3: 实现最小扩展**

为财务结果增加默认 `history: []` 并从缓存读取。告警返回 `severity`、`created_at`、`changes` 和 `company_name`。关联实体按名称查询本地供应商，找到时设置 `supplier_id`，否则为 `None`；在 Pydantic 模型为新增字段提供默认值。

- [ ] **Step 4: 补充边界测试**

```python
def test_relationship_summary_adds_supplier_id_for_local_entity(monkeypatch):
    monkeypatch.setattr("app.domains.supplier.repo.get_supplier_by_name", lambda _: {"_id": "supplier-2"})
    summary = _attach_supplier_ids({"entities": [{"name": "关联企业"}]})
    assert summary["entities"][0]["supplier_id"] == "supplier-2"

def test_alert_list_returns_change_details(monkeypatch):
    monkeypatch.setattr("app.domains.supplier.service.get_db", fake_alert_db())
    alert = _build_alert_list("测试供应商")[0]
    assert alert["changes"] == [{"field": "risk_score", "old": 50, "new": 80}]
```

- [ ] **Step 5: 验证 GREEN**

Run: `cd backend && pytest tests/test_supplier_profile_service.py -v`

Expected: PASS。

- [ ] **Step 6: 提交**

```bash
git add backend/app/domains/supplier/service.py backend/app/schemas/supplier.py backend/tests/test_supplier_profile_service.py
git commit -m "feat: 丰富供应商画像分析数据"
```

### Task 3: 画像编辑、重新评估和监控切换

**Files:**
- Modify: `frontend/src/components/SupplierProfilePage.tsx`
- Modify: `frontend/src/types.ts`
- Create: `frontend/src/components/SupplierProfilePage.test.tsx`

**Interfaces:**
- Consumes: `PUT /suppliers/{id}`、`POST /risk/assess`、`POST /alert/watchlist`、`DELETE /alert/watchlist/{company_name}`。
- Produces: 画像页三项动作，并失效 `supplierProfile(id)`、`suppliers` 与 `watchlist`。

- [ ] **Step 1: 写失败测试**

```tsx
it('reassesses and invalidates profile queries', async () => {
  renderProfile({ risk: { in_watchlist: false } });
  await userEvent.click(screen.getByRole('button', { name: '重新评估' }));

  expect(api.post).toHaveBeenCalledWith('/risk/assess', { company_name: '测试供应商' });
  expect(invalidateQueries).toHaveBeenCalledWith({ queryKey: queryKeys.supplierProfile('supplier-1') });
});
```

- [ ] **Step 2: 验证 RED**

Run: `cd frontend && npm test -- SupplierProfilePage.test.tsx`

Expected: FAIL，因为页面没有重新评估按钮和 mutation。

- [ ] **Step 3: 实现最小动作和失效函数**

```tsx
const refreshProfileQueries = () => {
  queryClient.invalidateQueries({ queryKey: queryKeys.supplierProfile(id) });
  queryClient.invalidateQueries({ queryKey: queryKeys.suppliers });
  queryClient.invalidateQueries({ queryKey: queryKeys.watchlist });
};
```

为编辑、评估和监控切换各建一个 `useMutation`；成功时调用该函数，失败时显示中文错误。编辑仅提交现有 `SupplierUpdateInput` 已支持的字段。

- [ ] **Step 4: 补充动作测试**

```tsx
it('adds an unmonitored supplier to the watchlist', async () => {
  renderProfile({ risk: { in_watchlist: false } });
  await userEvent.click(screen.getByRole('button', { name: '加入监控' }));
  expect(api.post).toHaveBeenCalledWith('/alert/watchlist', { company_name: '测试供应商' });
});

it('saves an edited industry through the supplier update endpoint', async () => {
  renderProfile({ basic_info: { industry: '旧行业' } });
  await userEvent.click(screen.getByRole('button', { name: '编辑主数据' }));
  await userEvent.clear(screen.getByLabelText('行业'));
  await userEvent.type(screen.getByLabelText('行业'), '新行业');
  await userEvent.click(screen.getByRole('button', { name: '保存' }));
  expect(api.put).toHaveBeenCalledWith('/suppliers/supplier-1', expect.objectContaining({ industry: '新行业' }));
});
```

- [ ] **Step 5: 验证 GREEN**

Run: `cd frontend && npm test -- SupplierProfilePage.test.tsx`

Expected: PASS。

- [ ] **Step 6: 提交**

```bash
git add frontend/src/components/SupplierProfilePage.tsx frontend/src/components/SupplierProfilePage.test.tsx frontend/src/types.ts
git commit -m "feat: 供应商画像支持风险动作"
```

### Task 4: 财务趋势、关系图和审计展示

**Files:**
- Modify: `frontend/src/components/SupplierProfilePage.tsx`
- Modify: `frontend/src/types.ts`
- Modify: `frontend/src/components/SupplierProfilePage.test.tsx`

**Interfaces:**
- Consumes: `financial.history`、`relationships.entities`、`alerts`、`changelog`。
- Produces: Recharts 财务趋势、@xyflow/react 关系图、告警变化明细、懒加载变更日志。

- [ ] **Step 1: 写失败测试**

```tsx
it('uses embedded changelog without requesting it again', () => {
  renderProfile({ changelog: [{ changed_at: '2026-07-28T00:00:00Z', changed: {} }] });

  expect(api.get).not.toHaveBeenCalledWith(expect.stringContaining('/changelog'));
});
```

- [ ] **Step 2: 验证 RED**

Run: `cd frontend && npm test -- SupplierProfilePage.test.tsx`

Expected: FAIL，或观察到首屏重复请求。

- [ ] **Step 3: 实现最小展示**

财务 `history` 存在时以 recharts 展示营收增长、净利润增长、资产负债率；关联区用 @xyflow/react 渲染中心供应商到关联实体，`supplier_id` 存在时使用 `Link` 到 `/suppliers/{supplier_id}`；告警显示等级、时间和新旧字段值。变更日志仅在画像没有内嵌记录时查询；“加载更多”以更大 `limit` 请求。

- [ ] **Step 4: 补充展示测试**

```tsx
it('links related entities that have a supplier id', () => {
  renderProfile({ relationships: { entities: [{ name: '关联企业', supplier_id: 'supplier-2' }] } });
  expect(screen.getByRole('link', { name: '关联企业' })).toHaveAttribute('href', '/suppliers/supplier-2');
});

it('renders old and new values for an alert change', () => {
  renderProfile({ alerts: [{ severity: 'critical', created_at: '2026-07-28T00:00:00Z', changes: [{ field: 'risk_score', old: 50, new: 80 }] }] });
  expect(screen.getByText(/50/)).toBeInTheDocument();
  expect(screen.getByText(/80/)).toBeInTheDocument();
});
```

- [ ] **Step 5: 验证 GREEN**

Run: `cd frontend && npm test -- SupplierProfilePage.test.tsx && npm run build`

Expected: 新增测试 PASS；记录与本次无关的既有全局构建错误。

- [ ] **Step 6: 提交**

```bash
git add frontend/src/components/SupplierProfilePage.tsx frontend/src/components/SupplierProfilePage.test.tsx frontend/src/types.ts
git commit -m "feat: 增强供应商画像分析展示"
```

### Task 5: 回归与交付检查

**Files:**
- Modify: `docs/superpowers/plans/2026-07-28-supplier-profile-improvements.md`

- [ ] **Step 1: 后端定向回归**

Run: `cd backend && pytest tests/test_supplier_profile_service.py tests/test_risk.py -v`

Expected: PASS。

- [ ] **Step 2: 前端定向回归**

Run: `cd frontend && npm test -- SupplierProfilePage.test.tsx && npm run build`

Expected: 画像相关测试 PASS；全局既有错误单独记录。

- [ ] **Step 3: 变更检查**

Run: `git diff --check`

Expected: 无空白错误。

- [ ] **Step 4: 更新计划状态并提交**

```bash
git add docs/superpowers/plans/2026-07-28-supplier-profile-improvements.md
git commit -m "docs: 记录供应商画像实施验证"
```
