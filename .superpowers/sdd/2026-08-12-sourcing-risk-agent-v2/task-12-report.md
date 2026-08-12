# Task 12 Report — approved Sourcing Risk V2 actions

## Delivered

- Added `action_service.py` as the only V2 business-write gateway. Proposals
  are persisted without dispatching external imports or supplier-master writes.
- Approval requires a creator-scoped (or admin) Run, `expected_version`, an
  `ACTION_PENDING` Run, an authorized reviewer, a pending same-Run proposal,
  and a maker-checker restriction for high-risk imports. Approval decision,
  Run/status event, proposal update, and `agent.action.approved` Outbox event
  use one PostgreSQL transaction.
- Proposal creation now requires creator identity/role and `expected_version`.
  It locks and validates Run/candidate/company ownership: imports require an
  `external` + `staged_candidate` candidate from the current Run and take their
  payload only from its persisted snapshot; existing-company actions require a
  current-Run target and reject candidate/company mismatches. Repeated matching
  idempotency keys return the original proposal; canonical payload, candidate,
  or target conflicts are rejected deterministically. The repository handles a
  concurrent unique-key insert by returning the durable replay result rather
  than leaking a PostgreSQL uniqueness exception.
- Registered an Outbox consumer for approved V2 actions. It checks approval
  state before dispatch, tracks success/retry/dead-letter `action_status`, and
  does not mark `ACTION_FAILED` until dead letter.
- V2 action events use a fixed five-attempt retry ceiling independently of the
  existing global Outbox setting. Existing Outbox behavior remains unchanged.
- Added explicit supplier-master import, watchlist, access-application, and
  report-export adapters. Supplier/access/export writes have durable Mongo
  idempotency keys; the import path does not call `resolve_supplier_id`.

## TDD record

- RED: `cd backend && pytest tests/test_sourcing_risk_actions.py -v` failed
  during collection because `action_service` did not exist.
- RED/GREEN: added and verified focused failures for creator authorization and
  stale versions, missing/local/non-staged/cross-Run import candidates,
  snapshot substitution, current-Run targets, candidate/company mismatch,
  canonical idempotency conflicts, and the concurrent unique-key replay seam.
- Added real PostgreSQL seam tests for create → approve → Outbox persistence,
  duplicate consumption unique keys, and V2 five-attempt dead letters. These
  use real repository/database state assertions rather than mock call checks.

## Verification

- `cd backend && pytest tests/test_sourcing_risk_actions.py -q -k 'not real_postgres'` — 20 passed, 1 deselected.
- `cd backend && pytest tests/test_agent_run_service.py tests/test_outbox_worker.py -q` — 19 passed.
- `cd backend && python -m compileall -q app/domains/sourcing_risk/action_service.py app/domains/agent_run/repo.py app/domains/outbox/repo.py app/domains/outbox/service.py app/domains/outbox/worker.py` — passed.
- `git diff --check` — passed.

## Concerns

- The three new real-PostgreSQL seam tests cannot run in this task environment:
  sandbox execution blocks `localhost:5432`; elevated execution reaches the
  server but has no `PG_PASSWORD` and fails with `fe_sendauth: no password
  supplied`. Run `cd backend && pytest tests/test_sourcing_risk_actions.py -q`
  and `pytest tests/test_outbox_service.py -q` with the project PostgreSQL
  credentials in CI or a database-enabled shell to verify the real transaction,
  duplicate-consumption, and five-attempt dead-letter paths.
- Mongo unique indexes are created by the existing index bootstrap. Deployment
  must run that bootstrap before processing the first approved V2 action.
