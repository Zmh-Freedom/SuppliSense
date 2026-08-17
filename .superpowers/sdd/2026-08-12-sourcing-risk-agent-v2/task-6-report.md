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

## Review Fixes

- RED: `pytest tests/test_sourcing_risk_policy_service.py -v` — 3 failures confirmed the original implementation allowed nested snapshot mutation, lacked checksum verification, and accepted a tolerance-based weight total.
- Weight validation now explicitly rejects `bool`, `NaN`, and positive/negative infinity. It converts finite numeric weights to `Decimal(str(value))` and requires the sum to equal exactly `Decimal("1")`.
- Frozen snapshots now recursively convert mappings to read-only mappings and lists to tuples. `verify_policy_snapshot_checksum()` recomputes the deterministic SHA-256 payload checksum, while `validate_snapshot_checksum()` raises for tampering.
- GREEN: `pytest tests/test_sourcing_risk_policy_service.py -v` — 15 passed.
