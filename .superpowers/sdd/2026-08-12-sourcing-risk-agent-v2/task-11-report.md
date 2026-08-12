# Task 11 Report: Sourcing Risk V2 Graph Orchestration

## Delivered

- Added a persistent-checkpoint LangGraph orchestration for sourcing-risk runs with the route `load_run → parse_requirement → lock_policy → local_discovery → external_discovery? → identity_resolution → investigate_parallel → validate_evidence → score_candidates → ready_for_review`.
- Connected the production create and clarification endpoints to non-blocking V2 runner tasks. The runner acquires Task 4's `AsyncPostgresSaver` and compiles the graph with that persistent checkpointer for each start/resume.
- Added `POST /agent-runs/{run_id}/identity-resolution`. It applies version and authorization checks, records an `identity_resolution` event in the same transaction as the run version update, then resumes the persistent checkpoint with `Command(resume={"identity_resolutions": ...})`.
- Added the service-level `record_orchestration_state` transaction. Graph nodes use it to transition the durable `AgentRunStatus` and append the corresponding typed SSE event with the new version; terminal `PARTIAL` and `NEEDS_REVIEW` states now let SSE replay and close consistently.
- Provider work is bounded by a per-event-loop shared semaphore (default maximum: 6) across all candidates and dimensions, in addition to the existing 20-second per-attempt timeout and one retry. Failed sanctions still produce unavailable evidence and mark the candidate `needs_review`; other provider failures preserve successful evidence and yield `PARTIAL`.
- Graph nodes continue to delegate requirement, policy, discovery, identity, evidence, and decision behavior to existing services. Nodes issue no direct SQL/Mongo calls or supplier-master writes.

## TDD Evidence

- RED: `pytest tests/test_agent_run_api.py tests/test_agent_run_service.py tests/test_agent_run_checkpointer.py tests/test_sourcing_risk_graph.py -q` failed at collection because `IdentityResolutionRequest` did not exist. The additional boundary tests specified the missing API-to-runner dispatch, persistent checkpointer resume payload, durable state/event transition, SSE terminal closure, and provider concurrency cap.
- GREEN: API tests now cover create, clarification, and identity-review runner dispatch; service tests cover durable reviewer input and atomic status/event persistence; checkpointer tests cover persistent-saver resume payload; graph tests measure the global provider maximum concurrency.

## Verification

- `cd backend && pytest tests/test_agent_run_api.py tests/test_agent_run_service.py tests/test_agent_run_checkpointer.py tests/test_sourcing_risk_graph.py tests/test_sourcing_risk_requirement_service.py tests/test_sourcing_risk_policy_service.py tests/test_sourcing_risk_discovery_service.py tests/test_sourcing_risk_identity_service.py tests/test_sourcing_risk_evidence_service.py tests/test_sourcing_risk_decision_service.py -q` — 92 passed (existing dependency deprecation warnings only).
- `cd backend && python -m compileall -q app/graphs/sourcing_risk_v2 app/domains/agent_run` — passed.
- `git diff --check` — passed.

## Concerns

- The six provider adapters are deliberately neutral seams pending governed provider integrations; production enabling needs integration tests against those configured providers and the persistent PostgreSQL saver.
- The runner is intentionally detached from the HTTP request, so deployment monitoring should surface exceptions from these background tasks; that operational concern is outside the requested P0/P1 scope.

## Final P0 Closure (2026-08-12)

- Clarification is now a legal `CLARIFYING → CREATED` version-protected transition. In the same database transaction it merges reviewer answers into the durable `agent_runs.requirement` input, increments the version, and appends the typed `clarification` event. The runner therefore consumes the merged durable input when it starts the next graph execution; it cannot parse the stale pre-clarification requirement.
- Added an API → real clarification service → runner seam test. It verifies authorization/version flow, durable requirement mutation, event payload/version, persistent-checkpointer acquisition, and the exact runner payload containing the supplied specification.
- Every graph checkpoint status now matches the status written by the same `_event(..., status=...)` call through `record_orchestration_state`. Intermediate parsing stays `CREATED` until policy lock, provider failures remain in their current durable stage until the final `PARTIAL` decision, and clear evidence validation atomically records `INVESTIGATING` with its typed event.
- Added graph alignment tests for requirement-ready, external-provider failure, investigation failure, and clear validation, plus a rollback test proving an event-insert failure escapes the shared cursor transaction rather than committing a standalone status transition.

## Final Verification

- `cd backend && pytest tests/test_agent_run_api.py tests/test_agent_run_service.py tests/test_agent_run_checkpointer.py tests/test_sourcing_risk_graph.py tests/test_sourcing_risk_requirement_service.py tests/test_sourcing_risk_policy_service.py tests/test_sourcing_risk_discovery_service.py tests/test_sourcing_risk_identity_service.py tests/test_sourcing_risk_evidence_service.py tests/test_sourcing_risk_decision_service.py -q` — 99 passed (existing dependency deprecation warnings only).
- `cd backend && python -m compileall -q app/graphs/sourcing_risk_v2 app/domains/agent_run` — passed.
- `git diff --check` — passed.
