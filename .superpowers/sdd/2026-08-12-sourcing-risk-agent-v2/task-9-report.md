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

## TDD evidence

- Initial focused run failed during collection because `evidence_service` did not exist.
- Review regression tests failed first against the previous implementation (missing outcome fields, no policy input, no UUID validation), then passed after the minimal remediation: `8 passed`.

## Verification

- `cd backend && pytest tests/test_sourcing_risk_evidence_service.py -v` — 8 passed.
- `cd backend && python -m compileall app/domains/sourcing_risk/evidence_service.py` — passed.
- `cd backend && git diff --check` — passed.

## Concerns

- The persisted `agent_evidence` schema still lacks a first-class `company_id` column. The explicit adapter prevents semantic misuse now, but a future migration should add `company_id` to avoid relying on the structured snapshot for relational queries.
