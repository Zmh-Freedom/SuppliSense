# Task 12 Report — approved Sourcing Risk V2 actions

## Delivered

- Added `action_service.py` as the only V2 business-write gateway. Proposals
  are persisted without dispatching external imports or supplier-master writes.
- Approval requires a creator-scoped (or admin) Run, `expected_version`, an
  `ACTION_PENDING` Run, an authorized reviewer, a pending same-Run proposal,
  and a maker-checker restriction for high-risk imports. Approval decision,
  Run/status event, proposal update, and `agent.action.approved` Outbox event
  use one PostgreSQL transaction.
- Proposal creation locks and validates Run/candidate/company ownership and
  idempotency keys. Repeated matching keys return the original proposal; a
  conflicting key is rejected deterministically.
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
- GREEN: focused tests cover unapproved imports, duplicated approvals,
  unauthorized/stale/cross-Run approvals, high-risk self-approval, proposal
  ownership, worker replay idempotency, V2 five-attempt dead letter, and durable
  Mongo idempotency indexes.

## Verification

- `cd backend && pytest tests/test_sourcing_risk_actions.py -q` — 9 passed.
- `cd backend && pytest tests/test_agent_run_service.py tests/test_outbox_worker.py -q` — 19 passed.
- `cd backend && python -m compileall -q app/db/mongo.py app/domains/sourcing_risk/action_service.py app/domains/agent_run/repo.py app/domains/agent_run/service.py app/domains/outbox/service.py` — passed.
- `git diff --check` — passed.

## Concerns

- Existing real-PostgreSQL Outbox integration tests cannot run in this sandbox:
  connections to `localhost:5432` are blocked (`Operation not permitted`) before
  test assertions. Run `cd backend && pytest tests/test_outbox_service.py -q`
  in CI or a database-enabled environment to validate the shared transaction and
  lease paths end to end.
- Mongo unique indexes are created by the existing index bootstrap. Deployment
  must run that bootstrap before processing the first approved V2 action.
