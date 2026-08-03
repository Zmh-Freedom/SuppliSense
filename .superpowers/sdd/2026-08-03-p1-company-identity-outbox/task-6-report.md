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
