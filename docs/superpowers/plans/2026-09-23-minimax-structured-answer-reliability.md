# MiniMax Structured Answer Reliability Plan

> **For agentic workers:** This plan is being executed natively in the current task.

**Goal:** Make structured MiniMax responses diagnosable and recoverable, and ensure every completed or disconnected chat stream leaves a visible assistant result.

**Architecture:** Keep the Harness deterministic answer as the source of truth. Normalize known JSON wrappers in a shared parser, record response metadata without logging model content, and use bounded retry only for optional soft-risk enrichment. Make the chat client synthesize a visible fallback from the structured answer or terminal status when answer text is missing.

**Tech Stack:** Python, FastAPI/LangGraph streaming, OpenAI-compatible SDK, Pydantic, React, TypeScript, Vitest.

**Spec:** `docs/issue-log.md`, ISS-20260923-003 and ISS-20260923-004.

## Global Constraints

- Read-only chat stays on the single Harness runtime.
- Keep the existing API response and SSE event structures.
- Keep services framework-agnostic.
- Do not log prompt or response content.
- Every discovered issue and verification result remains in `docs/issue-log.md`.

## Review Focus

- Empty or whitespace model content: deterministic answer remains visible.
- JSON code fences and known provider wrappers: accepted only after normal parsing and schema validation.
- Truncated or malformed JSON: one bounded soft-risk retry; optional narration falls back safely.
- Empty final `done.answer`: use the structured answer summary, then a clear terminal message.
- Clean EOF without terminal event: replay persisted run events when possible; otherwise persist a visible failure state.
- Company names beginning with “比”: preserve the literal company name while still removing the explicit filler “比一下”.

### Task 1: Structured provider responses

**Files:** `backend/app/core/structured_response.py`, narrator, soft-risk scoring, relevant backend tests, `docs/issue-log.md`.

- [x] Test JSON fences/wrappers and empty/malformed responses.
- [x] Capture failure categories, finish reason, and completion-token metadata without raw content.
- [x] Add a single bounded retry for malformed or invalid soft-risk JSON.
- [x] Keep narrator fallback deterministic and preserve its existing bounded timeout.

### Task 2: Chat terminal answer delivery

**Files:** `frontend/src/components/ChatView.tsx`, `frontend/src/__tests__/ChatView.test.tsx`, targeted streaming tests if needed, `docs/issue-log.md`.

- [x] Test empty `done.answer` with a structured answer summary.
- [x] Test clean EOF with no terminal event and verify a visible persisted status.
- [x] Implement fallback selection and terminal replay/cleanup.
- [x] Run relevant backend and frontend verification, then record results.

### Task 3: Explicit supplier name integrity

**Files:** `backend/app/graphs/agent_core/entity_normalization.py`, intent extraction, and their backend tests.

- [x] Add a regression for “比亚迪” being shortened by normalization.
- [x] Remove ambiguous one-character prefix trimming and add narrow query-context recovery.
- [x] Run the full backend suite after the shared normalizer change and record results.
