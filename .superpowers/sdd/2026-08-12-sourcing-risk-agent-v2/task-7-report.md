# Task 7 Report: Local-first supplier discovery

## Delivered

- Added read-only `search_for_sourcing_v2()` to normalize active local supplier candidates and filter explicit category, specification, region, and qualification constraints.
- Added local-first discovery: sufficient local candidates avoid provider use; insufficient results retain local candidates and stage external candidates.
- Staged external candidates always have `status="staged_candidate"`, `supplier_id=None`, and `company_id=None`; this task never resolves or creates identities.

## Verification

- RED: `pytest tests/test_sourcing_risk_discovery_service.py -v` failed at collection because `discovery_service` did not exist.
- GREEN: focused discovery tests pass, including local short-circuit, fallback retention, staging, sufficiency, all local filters, and read-only guard.
- `python -m compileall -q app/domains/sourcing/supplier_repo.py app/domains/sourcing_risk/discovery_service.py` passes.
- `git diff --check` passes.

## Concerns

- `search_external_provider()` is a no-op adapter until Task 11 supplies the governed timeout/retry provider implementation. On provider exceptions, discovery keeps local candidates and records a failed external status.
