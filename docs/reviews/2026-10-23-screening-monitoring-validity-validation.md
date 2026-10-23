# 名单筛查与监控证据有效性验收记录

日期：2026-10-23。对应设计：`docs/superpowers/specs/2026-10-23-evidence-validity-screening-monitoring-design.md`；实施计划：`docs/superpowers/plans/2026-10-23-evidence-validity-screening-monitoring.md`；范围：B1，问题 ISS-20261022-016、ISS-20261022-023。

## 本轮行为与决策

- 名单缺源、过期、疑似、确认和范围内零命中分别展示；未命中只对声明来源和筛查范围成立，不表示供应商整体合规正常。
- Harness 只把当前有效且由 Claim 的 `evidence_refs` 直接绑定的结构化名单/国内来源事实显示为当前记录。过期记录、旧 `clean` 布尔投影和无来源支撑的 Claim 继续标为历史参考。
- 监控覆盖按当前证据有效性判断。历史交易记录因缺少已验收时效契约仍显示为待核验；历史低分标记为当前待核验，历史高风险在覆盖不足时仍保留优先采购复核提示。
- 名单生产来源契约保持未配置；虚构来源只用于隔离验收。未引入名单供应商、刷新任务、通知或自动采购动作，也未扩展既有成功 API 返回结构。

## 覆盖矩阵

| 场景 | 验证边界 | 结果 |
| --- | --- | --- |
| 名单来源不可用 | 真实 `check_sanctions` 工具、Registry、Executor、Harness；隔离来源契约 | 无 clean Claim，答复明确无法判断，Harness 会话及轮次只加载一次 |
| 疑似、确认、范围内零命中 | 同一生产工具链与固定虚构正式来源 | 分别产生待核验线索、人工复核命中、声明范围内零命中；零命中未扩展为整体正常 |
| 工具未授权 | 真实 ToolExecutor；隔离来源读取计数 | 执行被拒绝，来源契约读取次数为 0 |
| 监控资料过期/缺失 | 真实监控调查工具与 Harness；内存数据库/来源有效性边界 | 过期交易维度显示 pending，缺失维度保留，建议下一步重新核验；不以历史合作声称当前供货 |
| Screening API | 真实路由和 service；内存来源及测试用户依赖 | 返回 schema 校验通过的范围内零命中响应，来源覆盖和来源名一致 |
| 前后端接缝 | 后端 Harness 实际输出生成 JSON，前端读取同一文件 | 疑似 Claim 显示主体名称及人工复核建议，不显示 clean/绿色放行；过期及 legacy bool 回归仍显示历史参考 |
| 浏览器组件 | 真实画像、监控工作台和聊天结果组件；明确标记的隔离 API/来源夹具 | 未知、过期、疑似、确认、范围内零命中五种文案一致；历史低/高分分别显示待核验和优先复核；重新加载后状态保留，390px 窄屏卡片纵向堆叠，控制台 error/warn 为空 |

浏览器验收确认既有 `scripts/db-tunnel.sh status` 未识别正在转发的 SSH 进程。没有启动第二条隧道；用 `lsof` 确认 15432、16379、27018 三个约定端口均在监听，并以现有配置成功探测 PostgreSQL `sra`、MongoDB ping 和 Redis ping。该脚本状态误报另记为 ISS-20261023-010，未在本任务修改未跟踪脚本。

## 门禁结果

- `backend/.venv/bin/python -m pytest -m 'not integration' backend/tests/test_screening_monitoring_harness.py -q`：8 passed。
- `cd backend && PYTHONPATH=../docs/reviews:. .venv/bin/python -m pytest -p operational_metric_offline_guard -m 'not integration and not agent_e2e_live' -q`：1360 passed，103 deselected，3 warnings，139.94s。警告是 Starlette `BlockingPortal` 别名和两处 Pydantic class-based Config 弃用提示。
- `cd frontend && npm test`：23 个测试文件、191 项通过。
- `cd frontend && npm run typecheck`、`npm run lint`、`npm run build`：均通过。Node 的 localStorage 实验性提示和 `module.register()` 弃用提示不影响门禁结果。
- `git diff --check`：通过。

## 最终审查与遗留

最终复核覆盖来源和有效性判断、API 兼容边界、Harness 的工具授权/证据绑定、前端状态映射以及共享夹具生成路径。审查中发现当前有效的疑似 Claim 被笼统标为历史参考，先补问题记录 ISS-20261023-009，再按 Claim 引用、主体、来源类型、有效状态和事实值判断；增加当前疑似、过期来源与 legacy bool 回归，浏览器确认主体名称可见且复核状态不变。另将 Task 14 的过期摘要断言改为实际范围内摘要；浏览器状态矩阵夹具说明已修正并复验，分别记录为 ISS-20261023-007、008。

本轮没有启用真实名单生产适配器，也没有运行使用真实账号完成登录的浏览器全链路；浏览器用例以固定虚构响应隔离外部服务，数据库认证只做只读连通检查。后续仍需为生产名单来源验收来源权威、范围、回执、版本及有效时间；交易快照仍需由业务确认时效政策。采购事项的接手、搁置、回执和关闭路径、经理审批政策以及 B2/C 扩展不属于本次 B1 验收范围。开发隧道状态脚本误报记录在 ISS-20261023-010，留待后续修复。

Task 6 实现提交：`0d467f12`（`test: verify screening and monitoring validity end to end`）。
