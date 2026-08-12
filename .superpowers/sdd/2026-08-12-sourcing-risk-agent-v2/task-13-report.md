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

---

## Final P1 transaction and staged-external repair

### Delivered

- `normalize_evidence()` now persists only the raw MongoDB payload reference and returns normalized evidence to the graph. The graph passes that evidence to `persist_orchestration_snapshot()`, whose existing caller-owned PostgreSQL cursor writes evidence snapshots, candidates/decisions, Run status, and SSE event as one transaction. Thus an event-write failure rolls the full PostgreSQL snapshot back instead of leaving a separately committed evidence row.
- Evidence upserts now use a stable provider/source key (with dimension/index fallback), not the newly generated normalized `evidence_id`; retrying the same graph snapshot updates one durable evidence row.
- Approval-gated external import recognizes the graph's canonical `source=staged_external,status=staged_candidate` pair while retaining the explicit legacy `source=external` alias. It still requires the candidate to belong to the current Run and uses only its persisted snapshot, so no supplier master write occurs before approval.

### TDD evidence

- RED: event-failure graph coverage showed `normalize_evidence()` independently invoked PostgreSQL evidence insertion; staged graph-persisted external candidates were rejected; stable evidence retry IDs differed.
- GREEN: coverage now verifies normalized evidence is handed to the snapshot command, the service passes evidence through the rollback transaction cursor, snapshot evidence shares the caller cursor, retry IDs match, and an actual `staged_external` candidate can create only a pending proposal from its persisted snapshot.

### Verification

Passed:

```text
cd backend && pytest -q tests/test_sourcing_risk_evidence_service.py tests/test_sourcing_risk_graph.py tests/test_agent_run_service.py tests/test_agent_run_repo.py::test_snapshot_writes_share_the_caller_transaction tests/test_agent_run_repo.py::test_evidence_snapshot_retries_reuse_a_stable_provider_key tests/test_sourcing_risk_actions.py -k 'not real_postgres_action_transaction'  # 66 passed, 1 deselected
cd backend && python -m compileall -q app
git diff --check
```

### Remaining environment constraint

The existing PostgreSQL integration test needs `localhost:5432`, which this sandbox denies (`Operation not permitted`). It remains excluded from the focused command above; rerun it where PostgreSQL is reachable.

---

## Final P1 evidence idempotency and raw-payload compensation repair

### Delivered

- Evidence snapshot IDs now derive solely from `run_id`, `company_id`, `dimension`, `claim_code`, source type, and a stable provider/source/raw-payload identity. They never include a mutable evidence-list index; when a provider does not provide an identifier, a canonical snapshot digest is the deterministic fallback.
- `normalize_evidence()` is now a pure normalization step: it deterministically derives `raw_payload_ref` but does not write MongoDB. The investigation node passes bounded raw-payload documents to `persist_orchestration_snapshot()` with the structured evidence snapshot.
- Raw MongoDB documents use the stable `raw_payload_ref` as an upsert key. The orchestration command stages them as `pending`, commits their visibility only after the PostgreSQL snapshot/status/event transaction commits, and deletes newly-created staged documents on any PostgreSQL failure. If cleanup itself cannot delete, it marks the document `orphan` for safe remediation. This is an explicit cross-store compensation seam, not a claim of Mongo/PostgreSQL ACID atomicity.
- The change preserves `company_id` on both structured and raw evidence, keeps the existing audit-bearing PostgreSQL snapshot/event transaction, and leaves legacy Mongo collections untouched.

### TDD evidence

- RED: a retry with a provider record moved from evidence index 1 to index 2 generated a second durable ID; normalization attempted a direct Mongo write; and the snapshot command had no raw payload boundary.
- GREEN: focused tests prove reordered provider evidence maps to one upsert row, a PostgreSQL event failure removes the newly staged raw payload, and a successful retry upserts/commits exactly one raw document under its stable reference.

### Verification

Passed:

```text
cd backend && pytest -q tests/test_sourcing_risk_evidence_service.py tests/test_sourcing_risk_graph.py tests/test_agent_run_service.py tests/test_agent_run_repo.py::test_snapshot_writes_share_the_caller_transaction tests/test_agent_run_repo.py::test_evidence_snapshot_retry_reuses_provider_evidence_after_reordering tests/test_sourcing_risk_actions.py -k 'not real_postgres_action_transaction'  # 68 passed, 1 deselected
cd backend && python -m compileall -q app
git diff --check
```

### Remaining environment constraint

The repository's PostgreSQL integration cases still require a reachable `localhost:5432`; this sandbox denies that connection. The compensation and idempotency seams are covered by stateful unit fakes and should be run against the real PostgreSQL/MongoDB deployment environment as part of integration validation.

---

## Final P1 raw-evidence compensation recovery repair

### Delivered

- MongoDB raw evidence remains explicitly outside the PostgreSQL transaction; this change does not claim cross-store ACID. Each document is keyed by the existing deterministic `raw_payload_ref` and can only be `pending`, `committed`, or `pending_compensation` during this workflow. Cleanup failure is never relabeled as committed or a terminal orphan.
- Partial Mongo staging now raises a `RawPayloadStagingError` carrying every already-created ref. The orchestration command compensates that exact set before re-raising, so an interruption halfway through a batch cannot silently strand an ordinary `pending` payload.
- PostgreSQL snapshot/status/event failure first changes each newly staged document to `pending_compensation` with `compensation_reason=postgres_snapshot_failed`, then attempts a conditional delete. A Mongo delete exception or `deleted_count == 0` leaves that stable record as explicitly retryable state.
- `retry_raw_payload_compensations()` is idempotent: duplicate refs collapse to one cleanup attempt; a missing ref reports `already_compensated`; repeated delete failure stays `pending_compensation` without creating or committing a duplicate. Re-staging never downgrades a `committed` payload.
- Detail responses include status-only `raw_payload_statuses` for refs already present in the authorized Run's PostgreSQL evidence snapshot. `POST /api/v1/agent-runs/{run_id}/raw-payload-compensations/retry` is the deterministic recovery entrance: it authorizes the Run first, derives refs server-side from that Run, and never accepts arbitrary client-supplied Mongo keys. Candidate and approval/master-data boundaries are unchanged.
- Added the unique Mongo index for `agent_evidence_payloads.raw_payload_ref` to enforce the stable idempotency key in deployment.

### TDD evidence

- RED: seven new stateful failure-injection tests failed against the previous implementation: mid-batch Mongo staging left `pending`, PG event/snapshot failures with delete errors or zero-delete became `orphan`, no retry API existed, and detail could not expose lifecycle status.
- GREEN: the tests cover mid-stage cleanup, PG event failure/delete exception, PG snapshot failure/zero delete, recovery success, repeated recovery failure, committed-state preservation, authorized Run-scoped retry, and status-only detail audit metadata.

### Verification

Passed:

```text
cd backend && pytest -q tests/test_sourcing_risk_evidence_service.py tests/test_agent_run_service.py tests/test_sourcing_risk_graph.py tests/test_agent_run_api.py  # 66 passed
cd backend && python -m compileall -q app
cd frontend && npm run typecheck
git diff --check
```

### Remaining environment constraint

The existing PostgreSQL integration tests still require a reachable `localhost:5432`, which this sandbox denies. The compensation paths use stateful Mongo fakes to verify all failure-injection contracts; real Mongo/PostgreSQL integration should be rerun in an environment where both services are available.

## Final P1 repair — durable compensation discovery and Mongo ownership

### Delivered

- Added the PostgreSQL `agent_raw_payload_compensations` recovery record keyed by `(run_id, raw_payload_ref)`, with status, attempt count, company ownership and last error. It is written outside the failed snapshot transaction, so a rolled-back evidence snapshot cannot hide a pending cleanup.
- Run detail merges the authorized Run's durable compensation records into `raw_payload_statuses`; the existing protected retry endpoint also derives and retries those records server-side, idempotently, without accepting arbitrary raw references.
- Mongo staging now carries a per-attempt `staging_owner` and checks stable `raw_payload_ref`, `run_id`, and `company_id` ownership. Commit and compensation use conditional lifecycle transitions, so a concurrent replay cannot commit or delete another Run's pending payload.
- An ambiguous `update_one` acknowledgement is reconciled by reading the stable reference and retaining an owned pending document for this attempt; staging exceptions expose all known owned payloads for compensation. Pending and compensation states never become committed evidence or decision input.
- This remains an explicit retryable cross-store compensation design; it does not claim Mongo/PostgreSQL ACID atomicity.

### Focused verification

Passed:

```text
cd backend && pytest -q <nine Task 13 compensation/detail/ownership tests>  # 9 passed
cd backend && python -m compileall -q app
git diff --check
```

The broader PostgreSQL-backed integration suite remains environment-dependent on `localhost:5432`.
