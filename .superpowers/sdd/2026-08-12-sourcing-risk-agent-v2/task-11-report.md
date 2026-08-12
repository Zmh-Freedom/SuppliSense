# Task 11 Report: Sourcing Risk V2 Graph Orchestration

## Delivered

- Added a persistent-checkpoint LangGraph orchestration for sourcing-risk runs with the route `load_run → parse_requirement → lock_policy → local_discovery → external_discovery? → identity_resolution → investigate_parallel → validate_evidence → score_candidates → ready_for_review`.
- Added durable LangGraph interrupt/resume handling for ambiguous identities. The graph records `IDENTITY_REVIEW` before issuing an interrupt and resumes from the configured `thread_id`; it does not use the legacy process-local interrupt store.
- Provider work is bounded with independent `asyncio.gather` tasks, a 20-second `asyncio.wait_for` per attempt, and one exponential-backoff retry. Failed sanctions produce unavailable evidence and mark the candidate `needs_review`; other provider failures preserve successful evidence and yield `PARTIAL`.
- Graph nodes delegate requirement, policy, discovery, identity, evidence, and decision behavior to existing services. Typed event emission and run loading use the agent-run service boundary; nodes issue no direct SQL or Mongo writes and no path writes supplier master data.

## TDD Evidence

- RED: `pytest tests/test_sourcing_risk_graph.py -v` initially failed at collection because the V2 graph modules did not exist.
- GREEN: graph tests now cover clarification routing, complete local flow, identity pause/resume, sanctions timeout retry/fail-closed behavior, conflicting evidence review, noncritical provider failure, and external fallback retention.

## Verification

- `cd backend && pytest tests/test_sourcing_risk_graph.py tests/test_sourcing_risk_requirement_service.py tests/test_sourcing_risk_policy_service.py tests/test_sourcing_risk_discovery_service.py tests/test_sourcing_risk_identity_service.py tests/test_sourcing_risk_evidence_service.py tests/test_sourcing_risk_decision_service.py -q` — 66 passed (one pre-existing Starlette deprecation warning).
- `cd backend && python -m compileall -q app/graphs/sourcing_risk_v2 app/domains/agent_run/service.py` — passed.
- `git diff --check` and `git diff --cached --check` — passed.

## Concerns

- The six provider adapters are deliberately neutral seams pending governed provider integrations; production enabling needs integration tests against those configured providers and the persistent PostgreSQL saver.
- Event append uses the existing run version as its event version. A dedicated atomic status-plus-event service primitive would further strengthen concurrent graph-run bookkeeping.
