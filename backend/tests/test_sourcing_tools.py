"""Safety tests for LangGraph sourcing tools."""

from app.domains.sourcing import tools


def test_local_access_never_defaults_to_approved_without_interrupt_context(monkeypatch) -> None:
    monkeypatch.setattr("app.graphs.approval.needs_approval", lambda *_args: True)
    monkeypatch.setattr(
        "app.graphs.approval.request_approval",
        lambda *_args: (_ for _ in ()).throw(RuntimeError("no graph")),
    )

    result = tools.select_sourcing_result.invoke({
        "result_id": "local-result-1",
        "action": "apply_access",
    })

    assert result["success"] is False
    assert result["error"] == "approval_context_required"
