# Task 8 Report: Sourcing candidate identity adapter

## Delivered

- Added read-only `resolve_candidate_identity(candidate)` adapter over P1 `search_identity()`.
- Exact P1 resolution binds only the returned canonical `company_id`.
- Candidate and pending-verification resolutions carry candidate/source snapshots, require identity review, have no `company_id`, and are not score eligible.
- The adapter creates and merges no company records; supplier names are lookup inputs only.
- P1 follow-up: exact results require a non-empty canonical `company_id` and explicitly set `score_eligible=True`; malformed exact results downgrade to pending verification with identity review and `score_eligible=False`.

## Verification

- RED: `pytest tests/test_sourcing_risk_identity_service.py -v` failed during collection because `identity_service` did not exist.
- GREEN: focused identity tests pass (3 passed).
- `python -m compileall -q app/domains/sourcing_risk/identity_service.py` passes.
- `git diff --check` passes.
- P1 follow-up: focused identity tests pass (4 passed), including malformed exact and exact score eligibility coverage; compile and diff checks pass.

## Concerns

- This task exposes the resolution payload only; a later orchestration task must persist `identity_review` and enforce `score_eligible` before any scoring workflow.
