# Task 9 Report: Evidence safety normalization

## Delivered

- Added `evidence_service.py` with an `EvidenceRecord` Pydantic contract and `normalize_evidence()`.
- Raw provider responses are stored only in MongoDB collection `agent_evidence_payloads`; the PostgreSQL evidence snapshot contains `raw_payload_ref`, not the raw response.
- Added fail-closed evidence validation: missing/unavailable sanctions data, sanctions hits, and conflicts require review; stale and unknown data cannot return `clear`.
- Added focused behavior tests for absent sanctions data, contradictory sanctions findings, stale clear evidence, and raw-payload separation.

## Review remediation

- `EvidenceRecord` now uses UUID-validated `run_id`, `company_id`, and `evidence_id` fields. The normalization entry point rejects non-UUID run and company IDs.
- `normalize_evidence()` accepts the frozen policy explicitly and derives freshness only from `policy["freshness_days"][dimension]`; provider payload freshness values are ignored.
- The legacy `agent_evidence` repository only accepts `candidate_id`. A dedicated adapter now persists `candidate_id=None` and keeps canonical `company_id` in the structured snapshot, so the public model does not repurpose company identity as a candidate ID.
- Evidence validation now reports `evidence_present`, `claim_status`, and `score_eligible`. Missing, unavailable, hit, stale, unknown, conflicting, and resolved/no-risk states remain distinct; sanctions unavailable and hit are both `needs_review` and ineligible for scoring.

## Scoped re-review remediation

- `agent_evidence` now owns `company_id UUID NOT NULL REFERENCES companies(id) ON DELETE RESTRICT` and an `idx_agent_evidence_company` index. The table retains nullable `candidate_id` for historical schema compatibility, but repository writes no longer use it.
- The idempotent schema upgrade adds `company_id` when absent, backfills it from `agent_run_candidates.company_id`, rejects any rows that cannot be safely backfilled, then sets `NOT NULL` and the foreign key constraint.
- `insert_evidence()` and the evidence service now write canonical `company_id` as a first-class column.
- Unknown provider conflict statuses normalize to explicit `unknown`; future `observed_at` values are never fresh and become `stale`.

## TDD evidence

- Initial focused run failed during collection because `evidence_service` did not exist.
- Review regression tests failed first against the previous implementation (missing outcome fields, no policy input, no UUID validation), then passed after the minimal remediation: `8 passed`.
- Scoped re-review regression tests failed first for unknown conflict normalization, future evidence freshness, first-class company persistence, and DDL contract; after the fix, 20 evidence/DDL tests and 3 repository contract tests passed.

## Verification

- `cd backend && pytest tests/test_sourcing_risk_evidence_service.py tests/test_agent_run_models.py -v` — 20 passed.
- `cd backend && pytest tests/test_agent_run_repo.py -v -k 'first_class_column or callers_cursor'` — 3 passed.
- `cd backend && python -m compileall app/domains/sourcing_risk/evidence_service.py` — passed.
- `cd backend && git diff --check` — passed.

## Concerns

- Full `test_agent_run_repo.py` requires a configured local PostgreSQL password. In this environment the service accepted the connection but returned `fe_sendauth: no password supplied`; the new repository contract test remains isolated and passing.
- The upgrade intentionally fails if historical evidence cannot be mapped from `candidate_id` to a canonical company. This prevents inventing company ownership to satisfy the new required constraint; such records require a controlled data repair before migration.
