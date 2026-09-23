# AI Chat Progress Feedback Implementation Plan

> **For agentic workers:** Implement inline with `superpowers:executing-plans`; use TDD for every behavior change.

**Goal:** Replace the generic five-stage timeline with user-facing task milestones, stable progress, elapsed time, and collapsed execution details.

**Architecture:** Keep the existing SSE/API payload unchanged. Derive a risk, comparison, or sourcing journey from the user's prompt and current tool/evidence snapshot. Keep progress monotonic during a run, show review/clarification/approval as explicit states, and move implementation-level details behind a disclosure.

**Tech Stack:** React 19, TypeScript, Tailwind CSS v4, Vitest, Testing Library.

**Spec:** User-approved prototype in the current conversation; issue `ISS-20260923-005` in `docs/issue-log.md`.

## Global Constraints

- Service layer remains framework-agnostic and unchanged.
- Do not change existing API response or SSE event structures.
- Preserve approval actions and execution diagnostics.
- Keep UI styling within existing Tailwind utilities and design tokens.
- Update `docs/issue-log.md` with verification and associated commit.

## Review Focus

- Comparison and sourcing prompts choose task-specific milestone labels.
- Evidence remediation does not move the visible milestone backward.
- `needs_review`, `clarifying`, `waiting_approval`, and failure states are not shown as successful completion.
- Archived answers do not show a live elapsed timer.
- Approval controls remain visible and usable while execution details are collapsed.

### Task 1: User-facing progress panel

**Files:**
- Modify: `frontend/src/components/AgentWorkflowPanel.tsx`
- Modify: `frontend/src/components/ChatMessageList.tsx`
- Modify: `frontend/src/components/ChatView.tsx`
- Test: `frontend/src/__tests__/AgentWorkflowPanel.test.tsx`
- Modify: `docs/issue-log.md`

**Interfaces:**
- `AgentWorkflowState` accepts optional `queryText` for classifying the active task.
- `ChatStreamViewState` carries the submitted prompt; archived messages use the nearest preceding user message.
- Existing `AgentWorkflowSnapshot` and server events remain unchanged.

- [x] Write failing component tests for risk, comparison, sourcing, remediation monotonicity, explicit terminal states, collapsed details, and approval visibility.
- [x] Run `cd frontend && npm test -- src/__tests__/AgentWorkflowPanel.test.tsx` and confirm expected failures.
- [x] Implement task-aware milestones, elapsed time for live runs, status-specific copy, and a collapsed execution-details disclosure.
- [x] Run the focused component test, the full frontend test suite, typecheck, lint, and build.
- [x] Update the issue record with results and commit the complete fix.
