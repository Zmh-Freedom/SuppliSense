# Task 6 Report: Auditable Company Merging

## Delivered

- Added `merge_company(source_company_id, data, actor_id, actor_role)` with admin-only access and explicit confirmation.
- Validates source UUIDs, schema-validates target UUIDs, rejects self-merges, missing companies, merged subjects, stale source/target versions, conflicting non-null credit codes, and malformed redirect chains that would reach the source.
- Added `lock_companies_for_merge(cur, ids)`, which issues one UUID-sorted `SELECT ... ORDER BY id FOR UPDATE` for the two merge subjects.
- Writes a pre-merge compensation snapshot to `company_merge_log`, redirects only the source, increments both identity versions, and writes `company.merge` audit plus `company.merged` Outbox event in the same PostgreSQL transaction.
- Preserves source aliases as required by the logical-redirect design; existing canonical resolution makes a source alias resolve to the merge target.

## TDD evidence

- RED: `cd backend && pytest tests/test_company_merge.py -v` initially failed during collection because `merge_company` did not exist.
- GREEN: `cd backend && pytest tests/test_company_merge.py -v` passed: 16 tests.
- Required regression: `cd backend && pytest tests/test_company_merge.py tests/test_company_transactions.py -v` passed: 21 tests.
- Full backend suite: `cd backend && pytest -q` passed: 154 tests, 8 pre-existing deprecation warnings.

## Transaction and concurrency evidence

- The event-failure test adds a real PostgreSQL `CHECK` constraint rejecting `company.merged`. It proves the merge log, source redirect, both versions, and merge audit are all rolled back when `enqueue_event` fails.
- The opposite-lock-order test uses two real PostgreSQL connections. The first requests `[target, source]`, holds the transaction after acquiring locks, and the second requests `[source, target]`. The second waits until the first commits, then completes; both returned lock lists are UUID-sorted and no deadlock error occurs. This verifies the repository's deterministic lock behavior without timing-sensitive competing merge writes.

## Test isolation and cleanup

Every database test creates a UUID-named isolated PostgreSQL schema, switches each test connection's `search_path` to that schema, and drops that exact schema with `CASCADE` in `finally`. No shared rows or broad public-table deletes are used.

## Fix round 1/5

### TDD evidence

- RED: expanded `tests/test_company_merge.py` first, then ran it before production changes. It collected 27 tests and failed exactly three new regressions: whitespace-only reason was accepted, a surrounding-space reason was persisted unchanged, and the compensation snapshot lacked `updated_at`.
- GREEN: added the smallest service changes: normalize/reject the merge reason before opening the transaction and serialize the pre-merge `updated_at` into the snapshot. The expanded merge suite then passed: 27 tests.
- Focused regression: `pytest tests/test_company_merge.py tests/test_company_transactions.py tests/test_outbox_service.py -v` passed: 44 tests.
- Final full backend regression: `pytest -q --disable-warnings` passed: 165 tests (the command reports 8 existing warnings).

### Invariants and concurrency

- The compensation snapshot now retains the original `merged_into_id`, `identity_version`, and JSON-safe ISO-8601 `updated_at` for both rows, the complete set of fields mutated by `apply_company_merge`.
- Added coverage for an already-merged target; a target-only redirect cycle; a broken target redirect; exactly 20 and more than 20 redirect links; both one-sided-null credit-code directions and both-null; exact audit details; and Outbox `schema_version=1`.
- Replaced the former fixed-delay lock check. A direct repository contract test checks the one UUID-sorted `ORDER BY id FOR UPDATE` statement. A second real PostgreSQL two-connection test starts opposite-order lock requests, observes the second backend in `pg_stat_activity.wait_event_type = 'Lock'`, releases the first transaction, and confirms both complete with no `DeadlockDetected`.

### Outbox timing investigation

- The reviewer-noted retry timing assertion compares application UTC timestamps with PostgreSQL `NOW()` across a narrow two-second window. It is independent of the merge code and was not changed.
- Re-ran `test_process_outbox_batch_retries_dead_letters_and_replays_with_real_handler` 20 consecutive times against local PostgreSQL: 20/20 passed. The focused Outbox suite also passed, so there is no new reproducible failure to justify changing unrelated production semantics in this round.
