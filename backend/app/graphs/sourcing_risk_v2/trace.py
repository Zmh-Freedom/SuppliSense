"""Production trace recorder for the durable sourcing-risk graph."""

from __future__ import annotations

import contextvars
import time
from typing import Any


_CURRENT_RECORDER: contextvars.ContextVar["GraphTraceRecorder | None"] = contextvars.ContextVar(
    "sourcing_risk_graph_trace_recorder", default=None
)


class GraphTraceRecorder:
    """Record real graph lifecycle events without changing graph business output."""

    def __init__(self, run_id: str, sink: Any = None) -> None:
        self.run_id = run_id
        self.sink = sink
        self.started_at = time.monotonic()
        self.events: list[dict[str, Any]] = []

    def record(self, event_type: str, *, at_ms: int | float | None = None, **payload: Any) -> None:
        elapsed = (time.monotonic() - self.started_at) * 1000 if at_ms is None else at_ms
        event = {"type": event_type, "at_ms": elapsed, "payload": dict(payload)}
        self.events.append(event)
        if self.sink is not None:
            try:
                self.sink(self.run_id, "graph_trace", event)
            except Exception:
                pass

    def set_result(self, result: dict[str, Any] | None) -> None:
        self.result = dict(result or {})

    def snapshot(self) -> dict[str, Any]:
        return {"run_id": self.run_id, "events": list(self.events), "result": dict(getattr(self, "result", {}))}

    def as_eval_trace(self) -> dict[str, Any]:
        """Expose the exact production trace in the evaluator's recorded format."""
        return self.snapshot()

    def load_snapshot(self, snapshot: dict[str, Any]) -> None:
        """Copy a completed production trace into an evaluator-owned recorder."""
        events = snapshot.get("events")
        if not isinstance(events, list):
            raise ValueError("production trace snapshot must contain events")
        self.events = [dict(event) for event in events]
        self.result = dict(snapshot.get("result") or {})


def current_graph_trace_recorder() -> GraphTraceRecorder | None:
    return _CURRENT_RECORDER.get()


def bind_graph_trace_recorder(recorder: GraphTraceRecorder) -> contextvars.Token:
    return _CURRENT_RECORDER.set(recorder)


def reset_graph_trace_recorder(token: contextvars.Token) -> None:
    _CURRENT_RECORDER.reset(token)


def record_graph_trace(event_type: str, **payload: Any) -> None:
    recorder = current_graph_trace_recorder()
    if recorder is not None:
        recorder.record(event_type, **payload)
