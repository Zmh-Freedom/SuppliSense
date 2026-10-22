# 质量与交付指标可信度 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** 修正质量/交付指标计算与解释，让可核验事实贯穿领域服务、工具、Harness 和聊天界面，未确认评级政策时不产生正式分数与等级。

**Architecture:** 保留当前领域服务与 Harness 边界，在 risk 领域增加纯指标标准化和汇总函数。内部分析结果保留来源与限制，旧服务返回经兼容映射，工具以现有 EvidenceRecord/Claim 字段传递事实；前端通过小型纯展示函数区分指标、安全分与历史运营分。

**Tech Stack:** Python / Pydantic v2 / pytest / LangGraph Harness；React 19 / TypeScript / Vitest / Testing Library；不新增依赖。

**Spec:** [已确认设计](../specs/2026-10-22-risk-conclusion-trust-design.md)，仅实施交付 A（017、018）。B、C 不在本计划内。

**Status:** 用户确认后按当前会话顺序实施（Native）；2026-10-23 完成五项任务及独立审查修复，实际结果见验收记录。

## Global Constraints

- “已有公开 API 返回结构和 SSE 事件格式保持兼容，历史评估和测试保留。”
- “业务规则保持框架无关。”
- “不修改主评估四维政策，不迁移历史分数，不改变既有 `/api/v2/assessments` 的请求维度。”
- “没有已确认的质量/交付政策时，`risk_score` 和 `risk_level` 使用现有允许的空值。”
- “不新建 services/store/features 目录”；前端类型仍集中在 `types.ts`。
- “交付 A 不要求修改飞书表结构、不写共享开发库、不批量补造历史字段。”
- “集成测试仅使用服务器独立测试库和独立 Redis 范围，禁止清理 `sra`、`tianyancha`。”
- 问题先记录再修复；017、018 已记录于 `docs/issue-log.md`。新增根因、阻塞与验证结果继续追加到对应条目。
- 当前工作区包含未提交评审材料、issue-log 修改、两份 10 月 13 日设计和隧道脚本。执行前记录差异，禁止 `git add .`，只提交本任务文件和自己新增的记录片段。
- 所有命令从标明的目录运行；先使用已有 `backend/.venv`、`frontend/node_modules`，不自行重装依赖。离线测试必须替换数据库、外部源、LLM 和持久化访问，不能只依赖 pytest marker 保证隔离。

## Review Focus

- 最新月夹杂非法值或重复冲突：不能丢弃坏行后得到偏好的汇总；由 Task 1、2 的混合输入测试约束。
- 指标与单位、分母或范围来自不同来源：不能拼接推算；由 Task 2 的混合口径与来源测试约束。
- 有数据但来源时间未知：工具执行时间不能伪装成数据时间；由 Task 3 的 evidence unavailable 测试约束。
- 同名不同主体、两个企业同时返回相同指标路径：Claim、限制和颜色不能串到另一主体；由 Task 3、4、5 约束。
- 旧聊天含高风险分或缺少证据/政策：回放不能重新标成安全，也不能改写原正文；由 Task 4 的历史结果组件测试约束。

## 文件与内部接口

| 文件 | 操作与职责 |
| --- | --- |
| `backend/app/domains/risk/risk_contract.py` | 增加内部指标契约类型，不改变现有能力/请求模型 |
| `backend/app/domains/risk/operational_metrics.py` | 新增纯解析、标准化、去重与汇总 |
| `backend/app/domains/risk/operational_risk_service.py` | 保持公开函数签名，新增内部详细分析入口与来源契约解析 |
| `backend/app/domains/risk/tools_risk.py` | 将内部结果映射为原工具结果和现有证据/Claim |
| `backend/app/tools/evidence.py` | 补充质量/交付指标单位及文案，多证据时逐条绑定，不改变其他工具默认行为 |
| `backend/app/graphs/harness/graph.py` | 仅调整质量/交付相关摘要与限制传递，不增加执行内核 |
| `frontend/src/claimPresentation.ts` | 新增与维度、证据绑定的纯展示函数 |
| `frontend/src/types.ts` / `components/StructuredAgentResult.tsx` | 内部展示类型与组件接入；旧公共数据类型保持 |
| `backend/tests/test_operational_metrics.py` | 新增纯函数测试 |
| `backend/tests/test_operational_risk_service.py` | 新增服务与兼容测试 |
| `backend/tests/test_operational_risk_evidence.py` | 新增工具与证据链测试 |
| `backend/tests/test_operational_risk_harness.py` | 新增真实 Harness 离线 E2E |
| `frontend/src/__tests__/claimPresentation.test.ts` | 新增展示规则测试 |
| `frontend/src/__tests__/StructuredAgentResult.test.tsx` | 扩展真实组件测试 |
| `backend/tests/test_agent_harness_task15.py` | 保留并按纠正后的业务含义更新必要断言 |
| `docs/issue-log.md` / 本计划 | 每个任务更新状态、验证和提交；不提前关闭问题 |

内部对象采用冻结的 Pydantic 模型，放在 `risk_contract.py`，不直接序列化到旧 API：

- `OperationalDimension = Literal['quality', 'delivery']`，现有服务从此处导入，避免两个定义。
- `OperationalSourceContract`：`source: str`、`version: str`、`dimension: OperationalDimension`、`rate_fields: tuple[str, ...]`、`rate_unit: Literal['ratio', 'percent']`、`rate_kind: Literal['pass', 'on_time', 'reject', 'late', 'defect_occurrence']`、`numerator_field: str | None`、`denominator_field: str | None`、`denominator_basis: Literal['inspected_units', 'ordered_units', 'order_lines', 'batches'] | None`、`complement_allowed: bool`、`quantity_unit_field: str | None`、`scope_fields: tuple[str, ...]`、`record_id_field: str`、`partition_field: str | None`、`row_kind: Literal['detail', 'summary']`、`observed_at_field: str`、`valid_until_field: str`。
- `OperationalObservation`：`metric: str`、`raw_value: float | None`、`raw_unit: str`、`value: float | None`、`numerator: float | None`、`denominator: float | None`、`denominator_basis: str | None`、`quantity_unit: str | None`、`period: str`、`scope: dict[str, str]`、`source: str`、`source_record_id: str`、`partition: str | None`、`row_kind: str`、`source_version: str`、`observed_at: datetime | None`、`valid_until: datetime | None`、`status: Literal['available', 'unavailable', 'conflicting']`、`reasons: tuple[str, ...]`。
- `OperationalMetricResult`：`metric: str`、`period: str | None`、`value: float | None`、`numerator: float | None`、`denominator: float | None`、`observations: tuple[OperationalObservation, ...]`、`status: Literal['available', 'partial', 'unavailable', 'conflicting']`、`reasons: tuple[str, ...]`。

契约配置属于受控来源配置，不能直接信任业务数据自带的“已批准”标记。A 不配置任何真实质量/交付来源的正式映射；测试通过注入契约验证正向逻辑。未配置来源在实际应用中明确未覆盖，后续真实接入需要独立字段对照验收。

## Task 1：明确单位、来源与单条指标标准化

**Files:** 新建 `operational_metrics.py`、`tests/test_operational_metrics.py`；修改 `risk_contract.py`、issue-log。

**Interfaces:**
- Produces: `normalize_operational_row(row: dict, contract: OperationalSourceContract) -> OperationalObservation`。
- Produces: `normalize_rate(value: object, unit: Literal['ratio', 'percent']) -> float`；非法值抛 `ValueError`，行标准化捕获并保留原因。
- 使用上面的内部模型；业务服务仍返回 dict。

- [x] **1. 写失败测试。** `test_rate_units_normalize_equally` 断言 ratio 0.952 与 percent 95.2 都等于 `pytest.approx(0.952)`；`test_invalid_rates_are_rejected` 参数化 True、NaN、±Infinity、负数、ratio 95.2、percent 100.1、非法字符串；合法字符串数值与 0 可解析。
- [x] **2. 写单行契约测试。** `test_alias_conflict_is_not_first_value_wins` 两别名值不同得到 conflicting；`test_reject_ratio_is_not_divided_by_received_qty` 只有在 `rate_kind='reject'`、`complement_allowed=True` 且检验口径一致时 0.048 得到 0.952；`defect_occurrence` 不推导合格率。`test_counts_only_follow_declared_basis` 覆盖不合格 2/检验 100→0.98、按数量口径延期 30/订货 100→0.7；均要求契约允许互补，不能声称后者是按单准时率。负数量、分子大于分母、零分母分别不可计算；未声明收货量等于检验分母时禁止补权重。
- [x] **3. 运行 RED。** backend：`.venv/bin/python -m pytest -m 'not integration' tests/test_operational_metrics.py -q`；预期新接口缺失或上述业务断言失败，记录原因，不能以环境导入错误充当业务反例。
- [x] **4. 实现单行标准化。** 字段只按受控契约读取；内部数值不提前四舍五入。声明为直接合格率/准时率时使用原率，reject/late 仅在允许互补时取补数；源 numerator 按 rate_kind 解释，输出 numerator 统一为合格/准时数量。rate_fields 为空或全部缺失时，可用已声明分子分母计算；同时存在率与计数时检查一致性，不静默择一。月份仅接受合法 `YYYY-MM`；非法时保留 unavailable。同一契约的所有 rate_fields 共用声明单位，因此同组 0.952 与 95.2 是冲突；ratio/percent 等价测试使用各自明确单位的两份契约，不猜测单位。
- [x] **5. 重跑同一命令至 GREEN。** 追加输入对象未被修改的断言，保持纯函数，无数据库/LLM 调用。
- [x] **6. 提交。** 精确暂存本任务文件与日志新增片段；提交信息 `feat: define validated operational metric observations`，将提交号记入问题记录。

## Task 2：按分母汇总并拒绝范围与重复冲突

**Files:** 修改 `operational_metrics.py`、`tests/test_operational_metrics.py`、issue-log。

**Interfaces:**
- Consumes: Task 1 的 `OperationalObservation`。
- Produces: `aggregate_operational_observations(observations: list[OperationalObservation]) -> OperationalMetricResult`。
- 仅汇总同一指标、周期、来源契约版本、scope、数量单位与分母口径；首轮不开放跨 scope 汇总。

- [x] **1. 写失败算例。** `test_weighted_quality_rate_uses_inspected_denominator`：同范围两个互不重叠分区，分母 100/9900，合格率 0.5/1.0，断言 denominator=10000、numerator=9950、value≈0.995；不是数量均值 5000 或比率均值 0.75。
- [x] **2. 写拒绝汇总测试。** `test_unweighted_rates_remain_individual_observations` 保留两条事实，aggregate value=None；单条来源汇总率可展示；`test_distinct_record_ids_do_not_prove_disjointness` 无 partition 证明不汇总；同 partition 重叠、混合单位/来源/月份/scope、汇总与明细并存均不合并。
- [x] **3. 写去重与坏行测试。** 同 source/version/record_id 内容相同只计一次；内容不同 conflicting；一好一坏不能丢弃坏行得到“完整”汇总。明细缺 ID 不参与汇总，原因可见；空列表返回 unavailable。
- [x] **4. 运行 RED。** backend：`.venv/bin/python -m pytest -m 'not integration' tests/test_operational_metrics.py -q`，确认新断言失败。
- [x] **5. 实现聚合。** 用来源+版本+记录 ID 去重，以 contract 声明的 partition 证明明细互不重叠；计数可比时求和后相除，无计数的单条已发布率保持来源范围。混合输入返回 partial/conflicting 并保留 observations；不能扩大样本范围。浮点比较使用固定小容差 `1e-9`，显示精度另处理。
- [x] **6. 运行 GREEN 并提交。** 同一测试命令全部通过；提交信息 `fix: aggregate operational metrics by verified denominators`，附去重/冲突验证记录。

## Task 3：服务兼容与逐证据 Claim 绑定

**Files:** 修改 `operational_risk_service.py`、`tools_risk.py`、`tools/evidence.py`；新建 `test_operational_risk_service.py`、`test_operational_risk_evidence.py`；必要时调整 Task 15 断言；更新 issue-log。

**Interfaces:**
- Produces: `resolve_operational_source_contract(row: dict, dimension: OperationalDimension) -> OperationalSourceContract | None`；受控配置查找，未配置返回 None；测试 monkeypatch 注入。
- Produces: `analyze_operational_metrics(company_name: str, dimension: OperationalDimension) -> dict`，内部结果严格为 `{'payload': dict, 'metric_result': OperationalMetricResult | None, 'limitations': list[str]}`。
- Preserves: `assess_operational_risk(company_name: str, dimension: OperationalDimension) -> dict`，返回内部结果的 payload。
- 工具调用内部详细入口，构造现有 EvidenceRecord 与 Claim，再交给共享 `attach_tool_evidence`；不新增公共字段或模型类型，不在领域层导入 LangGraph。

- [x] **1. 记录并测试现有返回分支。** fixtures 固定 missing_supplier、无快照、最新月无指标、有指标四种 payload 的字段集合和类型；确认 nullable score/level。新逻辑有指标时 partial 且两个评分字段为空，无指标继续 missing_data；partial 原有字段集合不增加 reason，限制走既有 AgentAnswer.limitations。
- [x] **2. 写来源选择失败测试。** 未配置契约不猜单位；最新月份坏数据不回退旧月；非法月份可能影响最新判定时返回不可判定；非 real、非 current、非 eligible 行不进入计算。多个同名主数据候选或缺 supplier_code 时使用现有缺失分支说明主体/范围不足，不以空代码查出其他供应商数据。
- [x] **3. 写真实工具证据测试。** 使用内存数据库替身、受控测试来源契约，执行实际工具与 `ToolExecutor`。直接比率 Claim 的 fact_path 为 `quality_rate` 或 `on_time_rate`、value 为 ratio、unit 为 `ratio`，statement 显示 95.2%。所有 Claim 绑定相应 evidence；跨主体或跨维度引用应被 EvidenceLedger 拒绝。多条不可汇总事实分别生成记录与 Claim，不只取第一条。
- [x] **4. 写时间与缺失测试。** 来源观察时间不等于工具调用时间；契约声明的 valid_until 已过期→stale，来源时间或有效期不明→unavailable，不能产生 supported 的当前结论。合法指标仍作为来源观测保留在证据中；无评分不生成 risk_score/risk_level Claim。synthetic 不升格为 formal。
- [x] **5. 运行 RED。** backend：`.venv/bin/python -m pytest -m 'not integration' tests/test_operational_risk_service.py tests/test_operational_risk_evidence.py tests/test_agent_harness_task15.py -q`。
- [x] **6. 接通服务。** 先确定当前供应商和最新合法月，再按来源契约标准化并汇总。未配置来源保留明确缺口；已有 raw_fields 不自动视为批准契约。metrics 只包含可表达的规范数值，聚合失败时留空或保留无歧义项，逐条来源放原 evidence 列表。partial 的 data_coverage 代表有效指标覆盖，不代表正式评级就绪；所有质量/交付新结果 score/level 为空。
- [x] **7. 接通工具。** 使用内部 observations 构造现有 evidence 字段：稳定来源引用、范围 query、content_hash、来源时间/有效期、状态和原值可追溯内容；可用聚合另成证据记录，标明组成来源。来源缺失不伪造 now 为 observed_at；收集时间可以为 now，但必须保持 unavailable 状态。共享适配器只对本工具启用逐记录事实 Claim，其他工具默认行为保持。内部数值与 Claim 值一致，百分比仅在文案转换。
- [x] **8. 运行 GREEN 与兼容回归。** 重跑第 5 步，另跑 `tests/test_agent_evidence_ledger.py tests/test_assessment_scoring.py tests/test_assessment_evidence.py`；检查返回结构/SSE 合同无变化，既有企业安全评分测试通过。
- [x] **9. 提交。** 提交信息 `fix: bind operational metric facts to verified evidence`，记录来源尚未正式配置这一发布限制，不能关闭“真实接入待验收”事项。

## Task 4：统一 Harness 摘要、结果卡与历史回放

**Files:** 修改 `graphs/harness/graph.py`、`types.ts`、`StructuredAgentResult.tsx`；新建 `claimPresentation.ts`、`__tests__/claimPresentation.test.ts`；扩展 `__tests__/StructuredAgentResult.test.tsx` 和 `test_operational_risk_harness.py`。

**Interfaces:**
- Produces in types.ts: `ClaimPresentation = { metric: string; value: string; label: string; tone: 'neutral' | 'positive' | 'warning' | 'danger' }`。
- Produces: `presentRiskClaim(claim: AgentAnswer['claims'][number], evidence: AgentEvidenceRecord[], peerClaims: AgentAnswer['claims'], coverageLimited: boolean): ClaimPresentation | null`；只接管原始质量/交付率和 risk_score/risk_level；其余返回 null，沿用旧格式化。
- Consumes: Task 3 的现有 Claim/Evidence 输出；保留 Harness `_summary(answer, state)` 签名。

- [x] **1. 写展示失败测试。** ratio 0.952→“95.2%”“合格率”；旧 quality risk_score=4.8、delivery=90 都中性“历史规则待核验”，保留原数字；未知维度评分中性“评分含义待核验”。已识别企业安全分 90 保留既有方向；两个主体的 level/score 不能相互配对。
- [x] **2. 写证据优先级测试。** partial/unsupported/stale/conflicting/过期引用优先于绿色；同主体/维度但不同 evidence 的等级不能套用到分数。运营旧等级没有可验证政策时也中性，不仅处理 risk_score。没有引用的旧数据不能靠 dimension 自动获得确定性颜色。
- [x] **3. 写真实组件测试。** render StructuredAgentResult，断言指标名/95.2%/“已取得指标，风险等级待确认”可见，不出现低风险承诺；历史 statement 输入不被改写，原数字保留；覆盖同时展示两个主体及质量/交付两维，不以抽取函数探针代替组件。
- [x] **4. 写 Harness 摘要失败测试。** 实际工具只输出 rate Claim 时，单维和质量+交付联合请求均展示有效指标与政策限制，不得说“没有可验证数据”、产生空摘要或把率叫安全分；完全无源仍说明未覆盖。只修改展示所需编排结果，不新增业务评分。
- [x] **5. 运行 RED。** frontend：`npm test -- src/__tests__/claimPresentation.test.ts src/__tests__/StructuredAgentResult.test.tsx`；backend：`.venv/bin/python -m pytest -m 'not integration' tests/test_operational_risk_harness.py -q`。
- [x] **6. 实现展示与摘要。** helper 同时供指标列、数值列、徽标与结构化摘要使用，避免只改颜色；旧其他维度行为保留。前端固定 tone→已有 Tailwind 色值。Harness 从 supported 指标 Claim 格式化；来源范围限制与未配置政策追加到现有 limitations。多实体按 entity_id/维度/evidence 配对；旧运营分无新政策不重新评级。
- [x] **7. 运行 GREEN 与前端相邻回归。** 第 5 步通过后跑 RiskSummary、ChatView、answerCapabilities、riskColors 现有测试，确认企业安全分与已有聊天回放无回归。
- [x] **8. 提交。** 提交信息 `fix: align operational summaries and claim presentation`；更新 018 验证记录。

## Task 5：完整链路、兼容与发布验收

**Files:** 完善 `test_operational_risk_harness.py`；按需新增 `frontend/src/__tests__/fixtures/operationalMetricAnswer.json`；更新问题记录、本计划和 `docs/reviews/2026-10-22-operational-metric-trust-validation.md`（实际验收时创建）。

**Interfaces:** 使用生产 `run_harness`、真实注册工具和 Task 1–4 的接口；数据库、来源配置、运行持久化、LLM 使用固定替身。新测试标记 `@pytest.mark.agent_e2e`，不走历史 ReAct 图。

- [x] **1. 补完整用户动作回归。** “查看甲供应商质量和交付”→当前 execution_context→实际工具→EvidenceLedger→AgentAnswer，断言有分子/分母可重算的事实、无正式风险等级、限制可见；权限拒绝后不读取风险/交易数据、不外呼（允许权限判定所需的身份与责任查询）；同名不同主体不混合。控制面与调用账本使用现有内存替身，不省略 ToolExecutor。
- [x] **2. 绑定前后端契约样本。** 保存一次固定离线输入产生的实际 AgentAnswer/evidence 到 fixture，并由后端测试验证生成结构与 fixture 一致，前端组件测试消费它。验收样本明确为测试 fixture，不进入业务来源；不得手写一个不同形状的“看起来正确”前端响应。
- [x] **3. 完成定向回归。** backend：`.venv/bin/python -m pytest -m 'not integration' tests/test_operational_metrics.py tests/test_operational_risk_service.py tests/test_operational_risk_evidence.py tests/test_operational_risk_harness.py tests/test_agent_harness_task15.py tests/test_agent_evidence_ledger.py tests/test_assessment_scoring.py tests/test_assessment_evidence.py -q`。失败先记录根因，不能删除旧测试或仅放宽断言。
- [x] **4. 完成离线门禁。** 先确认收集项无真实访问后，在 backend 跑 `.venv/bin/python -m pytest -m 'agent_e2e and not integration and not agent_e2e_live' -q`，再跑 `.venv/bin/python -m pytest -m 'not integration and not agent_e2e_live' -q`。若某个旧测试仍访问真实服务，记录并采用正确隔离替身后重跑，不能以共享开发库配合离线门禁。
- [x] **5. 完成前端门禁。** frontend 依次执行 `npm test`、`npm run typecheck`、`npm run lint`、`npm run build`；项目根目录执行 `git diff --check`。每个命令记录退出码与结果，不将本计划中的预期当作实际通过。
- [x] **6. 完成浏览器验收。** 先检查既有隧道与认证再启动应用；只读访问真实未覆盖场景。正向有指标无政策、旧分回放通过隔离测试数据或浏览器级 fixture 验收，不往共享库写 fixture，不把 mock 验收称为真实源验收。截图与记录覆盖数字、单位、限制、无误导颜色及页面刷新后回放一致。
- [x] **7. 完成最终审查。** 按用户选定执行方式审查差异与验收证据；核对本设计全部 A 验收项、公共结构、未配置源、缺阈值、历史记录不回写。测试不足或浏览器受阻时明确未验收项，问题不得标记全部解决。
- [x] **8. 提交验收结果。** 提交信息 `test: verify operational metric trust end to end`；017、018 只在相应验收完成后关闭，真实来源接入仍列为未开放能力。B/C 保持待设计状态。

## 设计覆盖与发布门槛

| 设计要求 | 负责任务 |
| --- | --- |
| 单位、非法值、别名冲突、补数语义 | 1 |
| 权重、范围、去重、混合来源、不可汇总仍保留事实 | 2、3 |
| 最新月份、正式来源过滤、缺主体、无政策返回 | 3 |
| 原 API 兼容、来源时间、证据逐条绑定 | 3、5 |
| 摘要、卡片、历史回放、不同分数含义 | 4、5 |
| 当前权限、同名主体、端到端与真实界面 | 3、5 |

自审结论：A 的设计要求均已分配；B/C 明确排除。没有已确认的真实质量/交付来源契约，首包交付是可信的计算/展示与诚实缺失行为，不宣称正向生产数据已接入。没有业务评级政策，正式分数与等级保持空值；不增加临时阈值开关。

推荐当前会话顺序实施（Native）：任务共享指标与证据接口，顺序推进可减少接口交接成本；完成后执行一次独立整体审查。也可选择逐任务子代理实施与审查（Subagent-driven），审查粒度更细、上下文成本更高。用户已选择 Native，全部实现由当前会话完成，末尾执行一次独立整体审查。
