# 名单筛查与监控证据有效性设计（交付 B1）

日期：2026-10-23；基线：`2ad9b0df`。
状态：用户已确认本文具体设计（2026-10-23）；实施计划待审阅，尚未进入产品代码实施。
关联：ISS-20261022-016、023；沿用 2026-10-22 风险结论可信度路线。执行方式沿用用户指定的当前会话顺序实施。

## 1. 用户目标与完成边界

采购分析师查看画像、聊天合规结果或监控对象时，能够分辨已核验事实、待核验线索、来源缺口和已过期记录；不会把“未查到/没有正式来源/旧数据还在”解释为当前安全或完整覆盖。

保留模块化单体、唯一只读 Harness、原审批边界、数据库职责和全部既有 API 成功返回结构及 SSE 事件。service 不引入 LangGraph。仅新增必要的版本化读模型；不采购/接入外部名单，不编造来源或期限，不自动刷新、不外发通知、不回写历史数据。

B1 只解决 016、023，以及它们的直接消费入口。预测/ESG 属于 B2；报告冻结属于 C；采购事项状态、经理审批权限、首页工作台重排不纳入本批。首页现有卡片的证据有效性和默认建议属于本批。

## 2. 代码核查与选择

- `risk/sanctions_service.py` 把内置名称样本、国家词典和国内执行记录合并打分；无命中即 `clean=true`。这些样本没有可核验名单版本、有效期或主体依据，不应产生正式结论。
- `risk/tools_analysis.py` 直接把 clean/match_count 作为 Claim；Supervisor 合规摘要也将非 clean 全部解释为命中。
- `supplier/service.py` 的合规模块无法在布尔字段中表达未知；`SupplierProfilePage` 用真假分别显示正常/红色匹配。整个画像已有独立模块降级能力。
- `alert/service.py` 按记录存在计算五域覆盖；交易仅检查 sync_status，舆情未按最新时间查询；下一步可能给出继续观察。
- `alert/intake_service.py` 也有独立覆盖判断，须同步消费共享来源判断，保留“历史合作存在”与“当前证据可用”的区别。
- 企业冻结评估已有 evidence_manifest、policy_snapshot、主体和责任范围；监控兼容投影未直接带完整清单。应在受权领域读模型内读取对应 PG 评估，不改变公开历史快照结构。
- 舆情已有六小时复用规则；企业评分已有版本化维度有效期。内部交易和正式名单尚无已验收的当前覆盖来源契约。

选择：领域内共享有效性判断 + 小型读模型适配。仅改 UI 不能修复工具事实；另建证据平台涉及迁移和双重事实源，均不采用。

## 3. 内部有效性模型

新增框架无关的纯函数模块 `app/domains/risk/evidence_validity.py`。返回以下内部字典，不直接扩大已有 API：

- source/source_version、policy_version、entity_id、scope；observed_at、collected_at、expires_at。
- status：available、empty、missing、stale、failed、conflicting、unknown。
- reason_codes 和说明；原始来源状态保留，未知不能被布尔取值覆盖。

确定性判断必须传入 now，统一为 UTC。无时区旧时间沿用现有项目 UTC 解释。非法时间、未来观察时间、截止早于观察时间，归为 unknown；不能使用采集时间替代来源观察时间。存在冲突、明确查询失败时保留对应状态，不因缓存存在变回 available。

截止规则：now >= expires_at 即过期。使用来源明确有效截止与已验收策略推导截止的较早值；没有可验收的时间/策略则 unknown。available/empty 均要求主体与范围一致、来源正式、查询回执有效、版本明确、覆盖充分、时间合法。

empty 仅代表在声明范围内完成查询且零记录；不是缺失，不表示供应商整体无风险。对无命中结论，部分来源成功、部分缺失不能以成功子集代表整体。

## 4. 名单筛查读模型

### 4.1 状态与正式结论门槛

新增内部 `analyze_sanctions(company_name, *, now=None) -> dict`，输出带 `schema_version=1` 的读模型：主体信息、screening_status、coverage、matches、domestic_findings、limitations、checked_at。checked_at 表示本次检查时间。

screening_status：

| 状态 | 含义 |
| --- | --- |
| not_queried | 没有查询回执 |
| unavailable | 正式来源未接入、来源失败或没有可用覆盖 |
| partial | 仅部分声明名单完成当前查询 |
| stale | 只有已过期的筛查依据 |
| possible_match | 正式来源存在名称线索，主体尚未核实 |
| confirmed_match | 正式名单记录与稳定主体标识核实一致；只确认该条记录 |
| no_match_in_scope | 所有声明必需来源当前有效、完整查询且无命中，仅限声明范围 |
| conflicting | 来源或主体证据互相冲突 |

未配置正式来源为 unavailable；正式来源已配置但尚无查询回执为 not_queried。

确定命中不因其他来源缺失被抹掉，但 coverage 继续保留缺口；不得声称完整筛查。possible_match、stale、partial、unavailable 等均不能被映射成 clean 或 confirmed_match。历史命中单独标记历史依据，不作为当前 confirmed_match。

正式来源适配器须提供名单名称/机构/清单类别、来源地址或引用、版本、覆盖范围、查询回执、时间、记录标识及主体匹配依据。源映射默认未启用；不能相信任意 Mongo 行的 approved 标志。内置样本不进入生产筛查，也不把其名称返回为企业真实线索；保留测试文件，测试中改用明确标记的虚构 fixture。

名称标准化只处理大小写、空白等表现差异，不剥离公司主体词冒充同一企业。精确名称或别名仍需主体核验；短字符串包含关系不得形成命中。国家词典不能证明企业在名单中，亦不产生正式制裁等级。国内失信/被执行信息独立归入 domestic_findings，按实际来源、回执和时间展示，不能计入制裁命中。

### 4.2 旧 API 和工具兼容

- `check_sanctions(company_name)` 保留旧成功字段集合。只有能精确投影为旧契约时才成功：no_match_in_scope 对应 clean=true；confirmed_match 对应 clean=false；旧 HTTP 成功分支的分数/等级类型也必须保持。没有已验收评分规则而无法满足旧分支类型时同样返回不可用，不能借工具字段可空偷偷改变旧 HTTP 类型。不再按混合计数凑制裁风险分。
- 无法投影时 service 抛业务 ValueError；旧 `/api/v1/analysis/sanctions/{company_name}` 返回统一错误信封的 HTTP 503。不能用 clean=false 表达未知。
- 旧总览不统计 unknown 为 clean；任一对象不能完整投影则返回 503，避免静默跳过。保留原成功结构，不在本轮扩展总览能力。
- 新增 `/api/v1/sanctions-screenings/{company_name}` 返回显式状态读模型，使用现有主体解析和责任范围校验；不接受仅登录就扩大到无权限主体。若主体不唯一，返回 409；无权 403；未找到 404。来源不可用属于正常业务状态返回 200，不与接口执行失败混淆。
- 保留工具名 `check_sanctions`、注册及执行策略，内部改用 analyze_sanctions；仍输出既有 SanctionsOutput 和 evidence/claims 结构。其 clean/score/level 已允许 None，未知时不得生成这类事实 Claim。详细状态放入 evidence.facts 并投影为现有 AgentAnswer limitations。
- 已验证正式命中和范围内无命中才产生相应 Claim；疑似线索只能为待核验说明。Harness 摘要及保留的 Supervisor 写动作合规入口一起消费状态，不能因工具成功就称筛查完成。普通聊天仍唯一进入 Harness。

### 4.3 画像适配

旧 profile 响应结构不变；未知筛查无法满足 sanctions_clean: bool 时，旧 compliance 模块按现有可空契约降级为 null，不能伪造布尔值；其余画像模块照常返回。

新增 `/api/v1/suppliers/{supplier_id}/compliance-status`，响应 schema_version=1、screening（同一读模型）、domestic_findings、limitations。沿用现有 profile 的 supplier_id 解析、can_access_supplier 及 assessment_scope。国内司法计数独立获取并带有效性；正式名单缺失不能阻止这些事实显示。缺来源不能被默认计为已核验零条。

前端画像用 TanStack Query 获取新读模型。旧字段不再用于判断当前正常/命中；新请求加载、失败或旧历史响应没有来源状态时中性显示“核验状态未知”。概览与风险页都显示疑似/确认/缺源/过期的准确文案，不让未知变为红色“0 条匹配”。独立来源失败不阻断整页。

## 5. 监控覆盖读模型与策略

在 `app/domains/alert/coverage_service.py` 建立当前覆盖读模型，使用共享有效性函数，输入已受权 target、对应评估及来源记录。一次请求统一 now；只读，不触发重采集。向既有 data_coverage 的原字段映射，不新增成功字段或新枚举：

- 单域 available 仅限当前有效且充分；missing 为确实未覆盖；pending 为存在但过期、未知、失败、冲突或部分覆盖。原 status/detail 结构保持；detail 清楚说明真实状态、来源观察时间和原因。
- 顶层 complete/partial/insufficient、数量和分母不变；只有 available 计入可用数量。empty 的有效查询也可计入来源覆盖，但不能暗示完整风险安全。
- missing_dimensions 沿用旧字段含义为“当前不可用的数据域”，detail 说明过期/失败等具体原因，不统一说未采集。

五域规则：

| 数据域 | 判定依据 |
| --- | --- |
| 主体身份 | 受权稳定主体与当前监控绑定一致且身份已核验；已撤销/歧义不能复用旧 verified |
| 风险快照 | 对应 PG 评估的冻结清单、冻结策略、质量、主体及范围；按现在重验各必需来源的有效性。原 complete 只是评估当时状态，不能直接复用为当前完整 |
| 财务/公开信息 | 对应评估财务来源及冻结有效期，保留来源原始时间含义；只拿到 risk_detail.available_dimensions 或 financial 数字不够 |
| 内部交易 | 正式供应商标识、合法最新月份、real/current/eligible 与已验收的来源/有效期；sync_status=current 不等于近期。无已验收时效契约则 pending，不能自行添加任意天数。最新月异常不回退旧月冒充当前 |
| 舆情 | 精确受权主体、最新 analyzed_at、现有六小时规则和来源相关性；有文章/有效查询回执才有相应覆盖。空新闻缓存沿用现有实时复查要求，此只读入口不触发刷新，显示待复查 |

PG 评估元数据经新的内部读取函数取得，按访问时责任范围重验，不在 legacy_snapshot_history 公共返回中添加字段。有 PG assessment_id 时 PG 不可用就明确 pending/failed，不降级到同名 Mongo 旧快照冒充当前。无可靠评估身份的历史记录可展示历史值，当前可用性为待核验；不删历史，不覆盖原分数。

监控接入调查也使用同一来源适配器；历史零件关系只称历史合作事实，不承诺当前供货或计入当前风险覆盖。企业资料获取时间与来源回执不足时不以对象存在表示有效。

## 6. 摘要、下一步与前端

现有 next_action 结构与 code 集合保持。排序原则：主体待核验 -> 无快照首次评估 -> 已知高风险或明确恶化需要复核 -> 证据过期/失败/冲突/未知需要覆盖核验 -> 当前充分且无异常才继续观察。

已知高风险依据过期时仍保留历史警示并注明待重核，不伪称当前已确认高风险；不能因缺源把已知危险隐藏为无需处理。reason 准确区分缺失、过期与失败。历史风险值保留，但标题及颜色不能把历史低风险包装为当前安全。

Dashboard、MonitoringWorkbench、MonitoringView、MonitoringIntakePanel 和 Harness 监控调查摘要同步消费状态；缺少 next_action 不再默认绿色继续观察，改“核验状态未知”。动作进入既有覆盖核验/审批链路，不新增自动采集或让采购员被迫补外部资料。

前端保持扁平目录及现有 Tailwind 风格，用共享小型展示函数，类型集中 types.ts、query key 集中 query-keys.ts。实际用户界面显示业务时间、边界和下一步，不暴露内部状态机术语。

## 7. 验收矩阵

1. 仅内置样本、无正式来源：旧 API 503；新读模型 unavailable；聊天不产生 clean Claim；画像显示未完成核验而非正常。
2. 短别名碰撞、空名称、同名不同主体：无确认命中；空名拒绝，同名进入澄清或冲突，不能跨主体取数。
3. 正式 fixture：有效完整零命中 -> no_match_in_scope；名称线索 -> possible_match；稳定标识一致 -> confirmed_match。声明范围明确，不承诺所有名单。
4. 正式来源部分缺失、失败、过期、冲突、无版本/时间/回执：保留原因，不形成整体清白；真实命中单条事实不被其他来源缺口吞掉。
5. 无名单但有国内司法来源：国内事实仍展示，不计为国际制裁命中；其他画像模块不受影响。
6. 2020 年记录全部存在：覆盖不可 complete，不能继续观察；重读或改采集时间不改变来源时效。
7. 有效期边界、未来时间、非法时间、未知时间、跨时区：纯函数固定 now，结果确定；截止瞬间过期。
8. PG 冻结清单当前有效/已过期/部分覆盖，财务周期不同、空新闻缓存、旧交易、最新坏月：领域覆盖和对应动作准确，不回退掩盖。
9. 名单或舆情来源挂起不阻断其他有效事实；数据库读取失败与确实零结果可区分。
10. 普通分析师/科室范围/无权限/撤权：新读入口沿用现有权限；工具拒绝不产生证据；不能通过同名或旧快照跨范围读取。
11. 实际 tool -> Executor -> EvidenceLedger -> Harness -> React；实际监控 service -> API -> React；覆盖缺源、过期和正向 fixture。保留 Agent E2E 标记及旧 API/SSE 兼容检查。
12. 历史数据保持原值；当前解释明确待核验；刷新后不恢复默认绿色。

先补失败测试，再修领域判断、来源适配、工具/路由和前端。专项后执行全部后端离线门禁（IPv4/IPv6 网络阻断）、前端测试、类型、lint、构建；浏览器使用真实组件核对画像、监控列表/详情和聊天的对应状态。正式名单未接入时正向只用明确隔离样本，真实路径只能证明准确未覆盖。

如需数据库集成，仅用服务器独立测试库/Redis 隔离范围；不写/清共享 sra、tianyancha，不调用 LLM/付费来源或外发通知。本设计阶段不启动应用或运行产品测试。

## 8. 实施顺序及风险

按六段形成后续实施计划：有效性纯函数 -> 名单读模型与旧投影 -> 监控来源读模型 -> 工具/Harness/画像/API 适配 -> 前端统一展示 -> 端到端、兼容及全量门禁。每段相关改动提交在一起，问题记录带根因、验证与提交。

主要代价：旧制裁接口将比之前更常返回 503，这是把无依据的成功改为诚实不可用；新版画像需额外只读请求。缺少可靠时效的旧数据将增加待核验数量，不能以降低门槛减少告警。正式名单与内部交易有效期契约未启用不阻止安全收口，但这两个来源不能宣称已具备正向业务能力。

回退只可退到“无法确认/待核验”，不得恢复样本清白、布尔未知或历史低风险默认放行。完成 B1 不代表 B2/C 或整体重构完成。

实施计划：`docs/superpowers/plans/2026-10-23-evidence-validity-screening-monitoring.md`。路径校正：既有 sanctions router 在 main.py 挂于 /api/v1/analysis，本次保持原地址；新 screening router 单独挂于 /api/v1，设计行为不变。
