# Task 14 Report

## 交付

- 新增 `backend/app/evals/sourcing_risk.py` 离线评估 runner 与固定 12 场景 JSON 集。
- 覆盖 requirement parsing、local-first discovery、identity/evidence safety、decision/action approval boundary、recovery fail-closed。
- 输出 task quality、candidate precision/recall、citation/evidence completeness、unsafe action rate、clarification rate 与 latency p50/p95/max。
- 新增 V2 低基数 Prometheus metrics，拒绝 company/run/user/provider 任意 ID 作为 label。
- 新增安全默认 feature flags、role-gated internal、deterministic canary routing。
- README 增加 Shadow/Internal/Canary/Default 发布顺序、人工审批、观测门槛与回滚步骤。

## 验证

```text
cd backend && pytest -q tests/test_sourcing_risk_evals.py
12 cases, all quality and rollout contracts passed

cd backend && python -m compileall -q app
passed

git diff --check
passed
```

前端未受影响，未修改 service 层或既有 API 返回结构。既存的 `task-9-review.md` 至 `task-13-review.md` 为用户工作区文件，未纳入提交。
