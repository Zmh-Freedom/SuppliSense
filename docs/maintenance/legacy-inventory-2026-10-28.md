# 历史代码与脚本盘点

日期：2026-10-28；基线：PR #3 合并提交 cbcaba8c。此清单来自静态引用检查；仓库外的脚本调用方和运行环境尚未核实。

## 图与前端

| 范围 | 当前用途与证据 | 处置 |
| --- | --- | --- |
| `backend/app/graphs/react_graph.py`、`plan_execute_graph.py`、`parallel_graph.py`、`reflection.py` | 不由 Chat API 选为活动只读内核；ReAct、Reflection 等仍有历史定向测试 | 保留历史回归；若决定退役，先逐项迁移仍有价值的断言并核对调用方，不删除测试代码 |
| `backend/app/graphs/agents/` 和 `router.py` | 历史图与定向测试使用；静态搜索不能排除动态或外部调用 | 保留，退役前核对导入、测试与外部调用 |
| `backend/app/graphs/streaming.py` | 同时承载活动 Harness/Supervisor 流和历史 ReAct 流 | 不整文件删除；仅在历史回归迁移后单独评估旧流函数 |
| `backend/app/graphs/agent_supervisor/`、`sourcing_risk_v2/`、`harness/` | 当前审批、寻源运行、checkpointer 或 Harness 路径有调用 | 活动代码，保留 |
| `frontend/src/__tests__/legacy/` | 旧 AssessView 和 WatchlistPanel 仅作为历史回归夹具；活动 /assess 为 MonitoringView | 维持测试专用，不挂回产品路由 |

## 脚本

| 脚本 | 观察到的作用 | 处置 |
| --- | --- | --- |
| `backend/scripts/migrate_to_pg.py` | 一次性 Mongo 用户到 PostgreSQL 迁移，会初始化 schema 并写用户 | 外部调用和运维归属待确认；保留历史脚本，不在共享库试运行 |
| `backend/scripts/seed_suppliers.py` | 向 Mongo suppliers 插入或更新内置供应商 | 外部调用待确认；保留但不作为日常启动或演示步骤 |
| `backend/scripts/enrich_suppliers.py` | 调用天眼查并更新 Mongo suppliers，模块顶层有执行逻辑 | 外部调用待确认；保留，不导入或执行做盘点 |
| `backend/scripts/seed_feishu_transaction_snapshot.py` | 合成交易快照脚本，明确要求测试写入确认；测试引用其数据生成函数 | 保留；不把合成数据当作真实来源验证 |
| `backend/scripts/convert_supplier_workbook.py` | 飞书导入转换文档和测试引用 | 保留 |
| `backend/scripts/import_gaishi_data.py`、`import_internal_supplier_materials.py` | 业务导入命令入口；仅凭静态引用不能判定外部使用情况 | 保留；由数据负责人确认是否退役 |
| `backend/scripts/release_verify.py` | 发布文档使用 | 保留 |

独立 scheduler 的旧 API cron 仍承担未迁移任务，不能因“legacy”名称删除；预生产仅交接 financial_check，付费 full_refresh 保持关闭。详见 [3D 预生产记录](../reviews/2026-10-28-3d-preproduction-readiness.md)。

## 退役前检查

确认仓库及外部调用、部署命令和历史兼容窗口；把仍需要的测试迁至现行入口；在隔离环境验证；保留原问题和验收证据。历史证据索引见 [evidence/README](../evidence/README.md)。
