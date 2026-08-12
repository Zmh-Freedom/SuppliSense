# Task 13 report — V2 sourcing and risk workbench P1 fixes

## Delivered

- `GET /api/v1/agent-runs/{run_id}` now validates an `AgentRunResponse` and returns the authorized durable Run plus `candidates`, evidence grouped by `company_id`, evidence reviews, decisions, action proposals, approvals, and the legacy `proposals` alias. The response retains `id` and adds `run_id` for compatibility.
- Detail collection reads happen only after creator/admin Run authorization. SSE replay receives the same authorized detail snapshot, so replayed stage, identity, evidence, decision, and approval events contain renderable status/version/candidates/evidence/decisions/proposals/approvals data.
- The client keeps the existing `Last-Event-ID` and session cursor behavior and now treats non-aborted normal SSE EOF as a reconnectable disconnect. It applies evidence, action proposal, and approval deltas to the query cache and does not reconnect after a terminal status.
- The workbench maps every public `AgentRunStatus` to an explicit visible timeline phase; unknown statuses display an unknown-phase notice instead of looking like `CREATED`.
- Approval uses `POST /agent-runs/{run_id}/approvals/{approval_id}/decisions` with `expected_version`, `decision`, and `comment`. The former short path remains as a deprecated compatibility route. The UI captures an opinion and requires it for rejection.

## TDD evidence

- Added failing backend tests for durable detail assembly, renderable event replay, HTTP detail collections, and the canonical decision URL.
- Added failing frontend tests for approval URL/payload/comment, all public status mappings, and reconnectable EOF. They failed before implementation because the comment control, phase behavior, and EOF error signal did not exist.

## Verification

Passed:

```text
cd frontend && npm test -- SourcingRiskWorkbench.test.tsx --run  # 23 passed
cd frontend && npm run lint
cd frontend && npm run typecheck
cd frontend && npm run build
cd backend && pytest tests/test_agent_run_service.py tests/test_agent_run_api.py tests/test_sourcing_risk_graph.py -q  # 41 passed
cd backend && python -m compileall -q app
git diff --check
```

## Concern

The repository/action integration tests that require PostgreSQL could not run in this sandbox because connections to `localhost:5432` are denied (`psycopg2.OperationalError: Operation not permitted`). Their non-database unit coverage passes; rerun those integration tests in an environment with PostgreSQL access.
