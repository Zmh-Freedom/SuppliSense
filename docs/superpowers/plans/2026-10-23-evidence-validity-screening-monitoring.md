# 名单筛查与监控证据有效性 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 名单缺源和模糊匹配不产生正式结论，监控按当前有效证据计算覆盖，并在聊天、画像和监控入口一致呈现。

**Architecture:** 保留现有架构，在风险领域建立纯有效性函数和名单读模型，在监控领域适配受权评估及现有来源。既有 API 成功返回和工具/SSE 结构不变；新增 schema_version=1 的名单与画像合规只读入口承载明确状态。

**Tech Stack:** 现有 FastAPI、Pydantic v2、PostgreSQL、同步 PyMongo、React、TanStack Query、pytest、Vitest；无新依赖。

**Spec:** `docs/superpowers/specs/2026-10-23-evidence-validity-screening-monitoring-design.md`（用户已确认）。

**Execution:** Native，沿用用户指定的当前会话顺序实施。基线 `52f926f6`；本计划待审阅后执行。所有命令从仓库根目录执行，除非显式 cd。

## Global Constraints

- 保留模块化单体、唯一只读 Harness、原审批边界、数据库职责和全部既有 API 成功返回结构及 SSE 事件。
- service 不引入 LangGraph；框架相关证据构造留在 tools/evidence.py；工具仍经唯一 Registry/Executor。
- 不采购/接入外部名单，不编造来源或期限，不自动刷新、不外发通知、不回写历史数据。
- B1 仅解决 016、023 及直接消费入口；不实施 B2、C、采购事项状态或经理审批策略。
- unknown 不能投影为 clean=false；旧 HTTP 无法准确表达时 503，既有画像 compliance 允许 null。
- 名单生产适配器默认未启用；交易无已验收时效契约时 pending；不得拿任意行 approved 标志作为信任依据。
- 单域覆盖只使用既有 available/missing/pending，顶层 complete/partial/insufficient；detail 解释真实原因。
- 前端保持扁平目录、Tailwind、TanStack Query；类型在 types.ts、缓存 key 在 query-keys.ts。
- 修改先记录 docs/issue-log.md，再补失败测试；相关修改逐批提交，原有未跟踪设计和隧道脚本不动。
- 离线门禁阻断 IPv4/IPv6 网络；若需集成只用服务器独立测试库/Redis 范围，不清理共享 sra、tianyancha。应用启动前核验既有隧道与认证。

## Review Focus

1. 刚刚采集但来源时间陈旧、未来时间或到期瞬间，不能变成 fresh；Task 1 固定时钟边界测试。
2. 已确认命中与其他来源失败并存，不能吞掉命中，也不能宣称完整筛查；Task 2 混合来源测试。
3. 已撤权/同名主体/PG 故障不能回退同名旧 Mongo 快照，亦不能泄露来源事实；Tasks 3/4 权限和故障测试。
4. 新画像请求失败且旧 compliance.clean=true，不能恢复绿色正常，国内已核验事实仍保留；Task 5 真实组件测试。
5. 过期的历史高风险与覆盖不足并存，仍需优先复核并明确历史边界；Tasks 3/5 动作及页面测试。

## 文件职责与接口约定

- 新增 `backend/app/domains/risk/evidence_validity.py`：纯有效性判断；不改变冻结评估/寻源原有政策。
- 修改 `sanctions_service.py`，新增 `sanctions_sources.py`：名单结果合并及来源契约；生产来源默认空，样本不进入活动路径。
- 新增 `backend/app/domains/supplier/compliance_service.py`：国内司法事实、画像新合规读模型；名单和国内事实分开。
- 新增 `backend/app/domains/alert/coverage_service.py`：读取和适配当前监控覆盖；`alert/service.py`、`intake_service.py` 消费它。
- 扩展 `assessment_service.py` 内部受权读取；不扩大公开 legacy_snapshot_history 的字段集合。
- 新增 `backend/app/schemas/screening.py`、`domains/risk/api_screening.py`：新版本化只读契约和路由；修改现有 supplier/api.py、api_scenario.py 及 main.py 注册。
- 修改 tools/evidence.py、tools_analysis.py、Harness 和保留 Supervisor 合规消费者；不增加执行内核分支。
- 新增 `frontend/src/evidencePresentation.ts`：共享状态文案及监控历史/当前展示；修改实际路由上的画像、聊天、监控、首页及接入组件。
- 新增 `backend/tests/evidence_validity_fixtures.py`：全虚构、固定时间、内存来源 fixture，供 Tasks 1–6 复用。禁止测试期向生产代码添加测试开关。

所有新 service 返回 dict。内部 SourceEvidence 字典字段：source、source_version、entity_id、scope（字符串）、source_mode（formal/sample）、status、observed_at、collected_at、expires_at、query_receipt（status/record_count）、coverage_partial、conflict。调用适配器负责提供已验收的来源契约，纯函数不接受 raw_fields 中的自我批准。

## Task 1: 有效性纯函数与时间边界

**Files:** Create `backend/app/domains/risk/evidence_validity.py`, `backend/tests/test_evidence_validity.py`, `backend/tests/evidence_validity_fixtures.py`；修改问题记录。

**Interfaces:** Produces `evaluate_evidence_validity(evidence: dict, *, expected_entity_id: str, expected_scope: str, now: datetime, max_age: timedelta | None = None, policy_version: str | None = None) -> dict`。返回保留来源元数据的 detached 字典，含 status、expires_at、reason_codes、detail。max_age 必须为正且有 policy_version；来源显式截止可独立使用 source_version。状态为设计中的七种内部状态。

- [ ] **1. 写失败测试。** 固定 `NOW=2026-10-23T00:00:00Z`、主体 `company-fixture-a`、范围 `fixture-scope`。fixture 函数 `source_evidence(**overrides) -> dict` 默认 formal、版本 fixture-v1、成功一条回执、观察 2026-10-22、截止 2026-10-24。测试断言：
  - 默认 available；成功零回执且 status=empty -> empty；无记录 -> missing。
  - now==expires_at -> stale；旧 observed_at + 新 collected_at 仍 stale。
  - observed_at 缺失/非法/未来、截止早于观察、无版本、无期限策略、sample、coverage_partial -> unknown。
  - 同一瞬间跨时区等价；naive 时间按 UTC；原输入不被修改。
  - 明确 failed/conflicting 不因缓存存在恢复可用；主体或范围不一致 -> conflicting。
  - 显式截止和 max_age 同时存在取较早值；无政策版本或非法 max_age -> unknown。
代表性断言（其余场景各自参数化）：
```python
def test_expiry_boundary_is_stale():
    evidence = source_evidence(expires_at=NOW.isoformat())
    result = evaluate_evidence_validity(evidence, expected_entity_id="company-fixture-a", expected_scope="fixture-scope", now=NOW)
    assert result["status"] == "stale"
```

- [ ] **2. 验证 RED。** `backend/.venv/bin/python -m pytest -m 'not integration' backend/tests/test_evidence_validity.py -q`；必须因缺失函数/行为失败，修正夹具错误后再判断。
- [ ] **3. 实现上述纯函数。** 明确缺失/失败/冲突优先级；available/empty 要求有效时间、主体范围、正式版本、充分回执。使用 detached 字典，不改其他领域有效期。
- [ ] **4. 验证 GREEN。** 重跑 Task 1 命令；全部通过。
- [ ] **5. 提交。** 仅暂存本任务文件和问题记录，提交 `feat: define shared evidence validity rules`。

## Task 2: 名单读模型、国内事实与旧投影

**Files:** Modify `backend/app/domains/risk/sanctions_service.py`；Create `sanctions_sources.py`、`backend/app/domains/supplier/compliance_service.py`、`backend/tests/test_sanctions_service.py`、`backend/tests/test_supplier_compliance_service.py`。

**Interfaces:** Consumes Task 1。Produces：
- `sanctions_sources.get_sanctions_source_contracts() -> list[dict]`，生产默认空；`load_sanctions_source(contract: dict, identity: dict) -> dict` 只读、返回 SourceEvidence 和匹配记录。
- `sanctions_service.analyze_sanctions(company_name: str, *, now: datetime | None = None) -> dict`；主体使用当前 assessment_scope，缺少受权主体不得直接按名称查源。
- `sanctions_service.project_legacy_sanctions(analysis: dict) -> dict`；既有 check_sanctions 调用该投影；未知或无已验收旧评分政策时 ValueError。
- `compliance_service.read_domestic_findings(company_name: str, *, entity_id: str, scope: str, now: datetime) -> list[dict]`；列表每项含 kind、count（可空）、validity、source。来源是已有 lawSuit/courtRegister/executedPerson/dishonesty/abnormal/punishmentInfo/taxArrears，回执验证复用 provider_receipt；期限仅来自已验收对应政策，不自行补税务期限。
- `compliance_service.build_supplier_compliance_status(supplier_id: str, *, now: datetime | None = None) -> dict`；输出 schema_version=1、screening、domestic_findings、limitations。analyze_sanctions 可调用 read_domestic_findings，画像 builder 复用该结果，不重复查询。

- [ ] **1. 写失败测试。** 测试全部使用虚构企业和名单，主体解析与 actor 范围在单元测试边界提供受权固定身份，不跳过产品权限检查的端到端测试：无正式来源 -> unavailable，无样本命中；已配置无回执 -> not_queried；完整有效零命中 -> no_match_in_scope；名称/别名线索 -> possible_match；稳定主体标识一致 -> confirmed_match；短子串不命中。
  混合来源确认命中+失败保留命中且 coverage 不完整；无命中+缺源 -> partial；只有过期来源 -> stale；冲突 -> conflicting。国内被执行不计 sanctions match_count，国内有有效事实时名单缺源不吞事实；缺回执不显示已核验零条。
  legacy 投影未知/无评分政策抛 ValueError；有明确 fixture 政策的完整结果保持旧字段及类型。fixture 策略显式携带 legacy_projection（按最终状态映射 sanctions_score/sanctions_level）和版本，只从受信配置读取；生产无此映射，不为兼容自行补分值。原样本不参与任何生产匹配，历史测试不删除。
代表性断言（analysis 是正常返回的新读模型最小数据，无真实数据库）：
```python
def test_unknown_cannot_be_projected_as_clean_false():
    with pytest.raises(ValueError):
        project_legacy_sanctions({"screening_status": "unavailable", "company_name": "虚构甲公司"})
```

- [ ] **2. 验证 RED。** `backend/.venv/bin/python -m pytest -m 'not integration' backend/tests/test_sanctions_service.py backend/tests/test_supplier_compliance_service.py -q`。
- [ ] **3. 实现所列接口。** 来源契约包含必需名单、范围、版本、允许的匹配标识与可选旧评分政策；只允许受信适配器提供。screening 读模型字段固定为 schema_version、company_name、entity_id、scope、screening_status、coverage、matches、domestic_findings、limitations、checked_at；coverage 含 status、sources、required_sources，matches 含来源记录、匹配依据及有效性。不能拿名称相等代替主体核验。生产无来源是业务状态。
- [ ] **4. 验证 GREEN。** 重跑本任务与 Task 1 测试，保持旧画像测试待 Task 4 完成后相邻验证，不删除旧测试。
- [ ] **5. 提交。** 提交 `fix: separate verified screening from unavailable sources`，附问题记录。

## Task 3: 监控当前覆盖与下一步建议

**Files:** Create `backend/app/domains/alert/coverage_service.py`、`backend/tests/test_monitor_coverage_validity.py`；Modify `backend/app/domains/risk/assessment_service.py`、`backend/app/domains/alert/service.py`、`intake_service.py`、`backend/tests/test_monitor_target_migration.py`、`test_monitor_intake.py`。

**Interfaces:** Consumes Task 1。Produces：
- `assessment_service.get_monitor_assessment_context(target: dict, snapshot: dict, *, actor_id: str, actor_role: str) -> dict | None`：按 snapshot.assessment_id 取完整行，先用既有 resolve_identity 重验当前主体/监控/责任范围，再校验行主体、scope、monitor_target_id；repo.get_assessment 沿用 actor 所有权，allow_all 仅既有管理员权限。无受权上下文不得读取 PG。无 assessment_id 返回 None；PG 异常向覆盖边界暴露，不回退同名缓存。
- `coverage_service.build_monitor_coverage(db: object, target: dict, snapshot: dict | None, *, now: datetime) -> dict`：返回既有七字段 status/available_count/total_count/coverage_ratio/summary/dimensions/missing_dimensions。
- `coverage_service.read_transaction_validity(db: object, identity: dict, *, now: datetime) -> dict`、`read_sentiment_validity(db: object, identity: dict, *, now: datetime) -> dict`，均返回 Task 1 结构及既有事实，供 summary 和 intake 共用。
- `coverage_service.build_intake_coverage(db: object, candidate: dict | None, query: str, enterprise_state: dict, *, now: datetime) -> tuple[list[dict], list[dict], list[dict]]`，与现有 intake 返回一致。

- [ ] **1. 写失败测试。** 2020 年五域都有记录仍不可 complete；更新采集时间不变 fresh；Task 1 固定时间 fixture 下冻结清单/政策全部当前有效才计入快照覆盖，quality=partial 或清单不充分不可算完整。只带 financial 数字/available_dimensions 不算当前可用。
  舆情六小时边界过期、按 analyzed_at 取最新、空新闻待复查；交易 sync_status=current 但旧月/合成/不可派生/未知期限均非 available，最新坏月不跳回旧月。历史零件关系只称历史合作。
  PG 超时、有 assessment_id 但无权/已撤权/主体不符：不查同名 Mongo 替代，不显示清单事实。单域读异常映射 pending/查询失败，不变成缺记录。
  下一步断言：过期高风险 -> review、高优先级且 reason 标历史待重核；旧低风险覆盖不足 -> supplement_data；无 next_action 不由后端编造完整覆盖；当前充分无异常才 continue_monitoring。序列比较继续沿用相同主体/范围/规则约束。
代表性断言（existing _next_action 的真实业务输出）：
```python
def test_historical_high_risk_stays_priority_review():
    action = _next_action({"identity_status": "verified"}, {"risk_level": "高风险"}, {"status": "stable"}, {"status": "partial", "dimensions": [{"key": "risk_snapshot", "status": "pending", "detail": "来源已过期"}]})
    assert action["code"] == "review"
    assert action["priority"] == "high"
    assert "历史" in action["reason"]
```

- [ ] **2. 验证 RED。** `backend/.venv/bin/python -m pytest -m 'not integration' backend/tests/test_monitor_coverage_validity.py -q`。
- [ ] **3. 实现接口及消费。** 当前请求统一 now；不修改既有 snapshot_history 输出，不以 checked_at 冒充每项来源时间。冻结 manifest 的 source_version 从冻结策略/内容哈希身份派生并注明 provenance，不能伪称外部名单版本；财务时间保留来源字段含义。舆情复用六小时规则与相关性检查，不触发 analyze_sentiment。交易无已验收策略保持 pending。旧 _data_coverage 转为薄适配、_next_action 消费明确边界；保留历史数值。
- [ ] **4. 验证 GREEN。** 本任务及既有 monitor_target_migration/monitor_intake 测试通过；旧夹具按真实来源边界补齐，不为保持 complete 而放松新门槛。
- [ ] **5. 提交。** 提交 `fix: evaluate monitoring coverage by current evidence validity`，附问题记录。

## Task 4: API、画像与 Harness 证据适配

**Files:** Create `backend/app/schemas/screening.py`、`backend/app/domains/risk/api_screening.py`、`backend/tests/test_screening_api.py`、`test_screening_evidence.py`；Modify `main.py`、`supplier/api.py`、`supplier/service.py`、`risk/api_scenario.py`、`risk/tools_analysis.py`、`tools/evidence.py`、`graphs/harness/graph.py`、`graphs/agent_supervisor/agents.py`、`backend/tests/test_supplier_profile_service.py`、`test_agent_supervisor_agents.py`。

**Interfaces:** Consumes Tasks 2/3。Produces 新 GET `/api/v1/sanctions-screenings/{company_name}`、`/api/v1/suppliers/{supplier_id}/compliance-status`；已有 GET `/api/v1/analysis/sanctions/{company_name}` 和 `/api/v1/analysis/sanctions` 不改地址或成功字段。schemas/screening.py 定义 `ScreeningReadResponse`、`SupplierComplianceReadResponse`，字段来自 Task 2，固定 schema_version=1。

Produces `tools.evidence.attach_screening_evidence(analysis: dict, *, entity_id: str) -> dict`，输出 SanctionsOutput 既有字段和共享 evidence/claims 信封。状态详情在 evidence.facts；不增加工具字段。

- [ ] **1. 写失败测试。** FastAPI TestClient 在无外部 lifespan 的最小 app 挂载真实 router、统一错误处理及内存鉴权边界。401/403/404/409 保持明确；新接口缺源 200+unavailable，旧接口缺源 503+既有 error 信封；新接口真实 body 经 schema 校验，已受权新入口不可按同名跨主体读。旧总览任一不可投影返回 503，不把 unknown 算 clean。
  画像旧 compliance=None 时其余模块仍返回，新增接口继续保留国内有效计数。工具未知时 clean=None、无 clean/零命中安全 Claim；来源时间与有效截止进入对应 EvidenceRecord，部分命中保持单条可验证事实和整体限制。Supervisor 不输出“已完成筛查/命中0条”。Harness 无 Claim 摘要不能称筛查已完成，possible_match 只能待核验。
代表性断言：Task 4 在 test_screening_api.py 定义 `screening_client` fixture，挂真实 router/错误处理，受权虚构主体，来源契约为空。
```python
def test_legacy_unavailable_is_503(screening_client):
    response = screening_client.get("/api/v1/analysis/sanctions/虚构甲公司")
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "HTTP_503"
```

- [ ] **2. 验证 RED。** `backend/.venv/bin/python -m pytest -m 'not integration' backend/tests/test_screening_api.py backend/tests/test_screening_evidence.py -q`。
- [ ] **3. 实现路由、受权 scope 和适配。** 新 screening router 单独挂载到 api_v1，避免误放到 /analysis；旧路由显式捕获 ValueError -> HTTPException(503)。profile 新路由严格复用 get_supplier/can_access_supplier；身份不存在 404，歧义/未核验 409。registry 保留工具名/契约/执行策略，适配器不绕过 Executor。限制从 evidence.facts 进入现有 AgentAnswer，沿用共享引用收集器。保留 Supervisor 写动作入口，不激活历史只读图。
- [ ] **4. 验证 GREEN。** 跑本任务、supplier_profile_service、agent_supervisor_agents、agent_evidence_ledger、agent_harness_task15 及 Tasks 1–3；全部通过。
- [ ] **5. 提交。** 提交 `fix: propagate screening validity through APIs and Harness`，附问题记录。

## Task 5: 实际前端入口统一展示

**Files:** Create `frontend/src/evidencePresentation.ts`、`src/__tests__/evidencePresentation.test.ts`；Modify `src/types.ts`、`query-keys.ts`、`components/SupplierProfilePage.tsx`、`Dashboard.tsx`、`MonitoringWorkbench.tsx`、`MonitoringView.tsx`、`MonitoringIntakePanel.tsx`、`StructuredAgentResult.tsx`、`ActionSummary.tsx`、对应现有组件测试；新增 `MonitoringIntakePanel.test.tsx`。

**Interfaces:** Consumes Task 4 新读模型和 Task 3 既有覆盖形状。Produces types.ts 中 `ScreeningReadResult`、`SupplierComplianceReadResult` 与后端契约一致；`queryKeys.supplierComplianceStatus(id: string)` 为 `['supplier','compliance-status',id]`；新增请求统一 api.get，由 TanStack Query 管理。

Produces `screeningPresentation(screening: ScreeningReadResult | null | undefined, state: 'loading' | 'error' | 'ready') -> {label: string; tone: 'neutral' | 'warning' | 'danger'; detail: string}`；即便 no_match_in_scope 也显示范围，不称整体“正常”。`monitorEvidencePresentation(target: MonitorTarget) -> {riskLabel: string; actionLabel: string; actionTone: 'neutral' | 'warning' | 'danger'; detail: string}`；当前不可确认时历史数值需标记“历史评估，当前待核验”，缺动作不回落绿色。

- [ ] **1. 写失败测试。** 新请求加载/失败且旧 profile sanctions_clean=true，概览与风险页均不显示制裁正常或绿色未命中；无名单仍显示国内已核验诉讼事实，缺国内来源不显示已核验0条。possible/confirmed/stale/partial/no_match 的范围文案正确；切换供应商不沿用前一家状态。
  Dashboard/Workbench/View 对过期旧低风险保留数字并显示历史边界；缺 next_action 显示核验状态未知；过期历史高风险仍为优先复核并说明需重核。Intake 历史合作不称当前充分。实际 StructuredAgentResult/ActionSummary 缺源与疑似不得默认放行或“已完成筛查”。
代表性断言（真实组件测试另按上述用户动作验证）：
```typescript
it('failed screening request is neutral', () => {
  const result = screeningPresentation(undefined, 'error');
  expect(result.label).toBe('核验状态未知');
  expect(result.tone).toBe('neutral');
});
```

- [ ] **2. 验证 RED。** `cd frontend && npm test -- src/__tests__/evidencePresentation.test.ts src/__tests__/SupplierProfilePage.test.tsx src/__tests__/Dashboard.test.tsx src/__tests__/MonitoringWorkbench.test.tsx src/__tests__/MonitoringView.test.tsx src/__tests__/MonitoringIntakePanel.test.tsx src/__tests__/StructuredAgentResult.test.tsx`。
- [ ] **3. 实现纯展示函数并接入真实组件。** 不改业务流程布局；画像两页消费新读模型，旧布尔仅作兼容数据不做当前判定；状态加载/失败有中性文案。所有缺动作 fallback 去掉绿色继续观察。source观察时间与采集时间分别显示。历史无活动路由 AssessView 不重新启用，旧 API 的 503 由其现有错误边界承接。
- [ ] **4. 验证 GREEN。** 跑本任务测试及 `cd frontend && npm run typecheck`；类型、字段、query key 与后端一致。
- [ ] **5. 提交。** 提交 `fix: align screening and monitoring validity presentation`，附问题记录。

## Task 6: 真实组件/链路、兼容与最终门禁

**Files:** Create `backend/tests/test_screening_monitoring_harness.py`、`frontend/src/__tests__/fixtures/screeningMonitoringAnswer.json`、`docs/reviews/2026-10-23-screening-monitoring-validity-validation.md`；Modify 必要的 Task 1–5 回归文件与问题记录。

**Interfaces:** Consumes 实际工具、Executor、Harness、新路由及生产组件；隔离仅放在数据库/鉴权/provider/state 边界。backend 生成 JSON fixture，frontend 读取同一份，不手造第二套响应。

- [ ] **1. 写 Agent E2E 与接缝失败测试。** `@pytest.mark.agent_e2e`，沿用 operational_risk_harness 的真实 registry/executor/run_harness 方法，完整 execution_context 与 current_task 快照只加载一次。覆盖用户问合规 -> 实际来源缺失 -> 无安全 Claim+明确限制；正式虚构来源疑似/确认/范围内零命中；用户问监控资料 -> 旧资料覆盖不足+下一步核验。未经授权的工具在读源前 denied。API 使用实际 service、内存来源核对 body；保存实际 Harness 输出为共享 fixture，规范化仅易变采集时间。
Task 6 在 test_screening_monitoring_harness.py 定义 `run_screening_harness(monkeypatch, *, scenario: str) -> dict`，使用上面约定的真实工具链与内存边界；代表性断言：
```python
def test_no_source_harness_does_not_claim_screening_complete(monkeypatch):
    result = run_screening_harness(monkeypatch, scenario="unavailable")
    assert result["answer"]["status"] != "completed"
    assert not any(c["fact_path"] == "clean" for c in result["answer"]["claims"])
    assert "已完成" not in result["answer"]["summary"]
```

- [ ] **2. 验证 RED，再闭合 GREEN。** `backend/.venv/bin/python -m pytest -m 'not integration' backend/tests/test_screening_monitoring_harness.py -q`；失败先记问题根因再修复，不只改摘要快照掩盖错误。前端实际组件测试读取共享 fixture，拒绝 clean/绿色放行误读。
- [ ] **3. 全后端离线门禁。** `cd backend && PYTHONPATH=../docs/reviews:. .venv/bin/python -m pytest -p operational_metric_offline_guard -m 'not integration and not agent_e2e_live' -q`。记录 pass/fail/deselected 和警告，任何失败先记录、定位，不启用共享库逃避网络保护。
- [ ] **4. 全前端门禁。** `cd frontend && npm test`、`npm run typecheck`、`npm run lint`、`npm run build` 均通过；类型构建真实运行，不只静态审查。
- [ ] **5. 浏览器验收。** 启动应用前确认服务器隧道与认证。真实画像/监控/聊天组件覆盖缺源、过期、疑似、确认与范围内零命中、旧低/高风险、刷新和窄屏；正向使用标记的隔离来源，真实来源未配置只证明缺源行为。控制台无新增错误。可使用临时验收入口，但验收后删除；如未跑完整登录链路须明确报告范围。
- [ ] **6. 整体独立审查与单轮修复。** 按 executing-plans 进行一次新上下文整体审查；Critical/Important 先记录并补失败测试再修复，Minor 明确留档。若代码变化，重跑受影响专项及对应最终门禁，记录最终结果，不用修复前数字代替。
- [ ] **7. 提交及清理。** 验收文档列出覆盖矩阵、未启用来源/策略、实施取舍和遗留问题，问题 016/023 补提交号。提交 `test: verify screening and monitoring validity end to end`，再按需提交审查修复。清理仅本计划临时入口/进程/工作记录，保留原有未跟踪文件；未获请求不推送/部署。

## 计划自检与交接

设计第 3 节 -> Task 1；第 4 节 -> Tasks 2/4/5；第 5 节 -> Task 3；第 6 节 -> Tasks 3–5；第 7 节全部验收场景 -> 各专项和 Task 6；第 8 节 -> 分步提交和最终留档。五项 Review Focus 均有明确归属。新增接口保持 /api/v1 前缀；既有制裁路径已按 main.py 的 /analysis 挂载校正。无未定义的跨任务接口；测试夹具函数在 Task 1 建立，后续补其场景数据，不在产品代码添加测试模式。

计划审阅确认后，继续按用户已选择的 Native 方法在当前会话实施，不再重复选择执行方式。正式名单和交易有效期契约尚未开放时，正确输出不可确认即为本轮预期边界，不以演示通过宣称上线。
