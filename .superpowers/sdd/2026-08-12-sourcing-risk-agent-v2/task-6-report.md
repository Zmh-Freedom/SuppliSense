# Task 6 Report: Sourcing-Risk Policy Snapshots

## Delivered

- Added deterministic default and camera category policy templates with required scoring weights, candidate minimum, evidence, penalties, freshness windows, and hard gates.
- Added recursive default-plus-category merging that returns independent policy copies.
- Added immutable-at-creation JSON-safe run snapshots containing template metadata, scoring version, freeze timestamp, and a SHA-256 checksum.
- Added deterministic contract validation for required metadata, complete dimensions, normalized weights, sanctions evidence, and hard-gate actions.

## TDD Evidence

- RED: `pytest tests/test_sourcing_risk_policy_service.py -v` failed during collection because `policy_service` did not exist.
- GREEN: `pytest tests/test_sourcing_risk_policy_service.py -v` — 8 passed.

## Verification

- `python -m compileall -q app/domains/sourcing_risk` — passed.
- `git diff --check` — passed.

## Concerns

- Snapshot persistence belongs to the later run repository task. This pure service creates an auditable detached record, and callers must persist that returned record once per run rather than re-resolving policy later.
