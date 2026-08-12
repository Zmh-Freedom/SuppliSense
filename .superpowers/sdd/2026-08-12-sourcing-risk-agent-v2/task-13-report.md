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

---

## Final P1 durability repair

### Delivered

- Real `SourcingRiskGraph` nodes now persist discovery candidates, resolved identity state, normalized evidence snapshots, evidence-review conclusions, and decisions through the `agent_run` service boundary. The graph never writes company or supplier masters; approval-gated business-master writes remain unchanged.
- The new `persist_orchestration_snapshot()` service command locks the Run and writes graph collections, Run status, and its SSE event in one PostgreSQL transaction. An event failure rolls the collection snapshot back with the status transition.
- Repository upserts use deterministic UUIDs per `(run_id, candidate/decision/evidence key)` so LangGraph replay/retry updates the same Run collection records rather than duplicating them.
- Added `agent_evidence_reviews` durable storage and included its per-company review snapshots in `get_run_detail_collections()`. GET detail and SSE detail snapshots now recover reviews from PostgreSQL, not LangGraph memory.

### TDD evidence

- RED: the new graph, repository, and service transaction tests initially failed because `persist_orchestration_snapshot`, snapshot upserts, and the locked Run seam did not exist.
- GREEN: tests now cover a real graph flow invoking candidate/review/decision persistence; caller-cursor sharing in the repository; event-write failure rolling the whole snapshot transaction back; and post-interrupt identity resolution persisting its confirmed candidate.

### Verification

Passed:

```text
cd backend && pytest -q tests/test_agent_run_api.py tests/test_agent_run_service.py tests/test_sourcing_risk_graph.py::test_real_graph_persists_candidates_reviews_and_decisions_for_detail tests/test_agent_run_repo.py::test_snapshot_writes_share_the_caller_transaction  # 27 passed
cd backend && pytest -q <all 18 focused graph tests, split because the environment intermittently truncated the combined command>  # 18 passed
cd backend && python -m compileall -q app
git diff --check
```

### Remaining environment constraint

The existing PostgreSQL integration tests in `tests/test_agent_run_repo.py` cannot connect to `localhost:5432` in this sandbox (`Operation not permitted`). The new transaction rollback seam test passes without a database; rerun the database-backed repository integration cases in an environment with PostgreSQL access.
