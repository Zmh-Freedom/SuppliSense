from app.graphs.agent_core.adapter import (
    build_execution_context,
    build_execution_prompt,
    save_execution_turn,
)


def test_build_execution_context_creates_task_matrix_for_contextual_analysis():
    execution = build_execution_context(
        session_id="session-1",
        user_message="对这些企业做风险和舆情分析",
        history=[{"role": "assistant", "content": "已推荐两家供应商"}],
        references=[
            {"name": "甲供应商"},
            {"name": "乙供应商"},
        ],
    )

    assert execution["conversation_state"]["selected_supplier_names"] == [
        "甲供应商",
        "乙供应商",
    ]
    task = execution["current_task"]
    assert task["target_supplier_names"] == ["甲供应商", "乙供应商"]
    assert task["analysis_dimensions"] == ["risk", "sentiment"]
    assert {(item["supplier_name"], item["dimension"]) for item in task["subtasks"]} == {
        ("甲供应商", "risk"),
        ("甲供应商", "sentiment"),
        ("乙供应商", "risk"),
        ("乙供应商", "sentiment"),
    }


def test_build_execution_prompt_exposes_structured_targets_and_dimensions():
    execution = build_execution_context(
        session_id="session-1",
        user_message="分析甲供应商的风险",
        references=[{"name": "甲供应商"}],
    )

    prompt = build_execution_prompt(execution)

    assert "结构化任务上下文" in prompt
    assert "甲供应商" in prompt
    assert "risk" in prompt


def test_save_execution_turn_delegates_to_shared_persistence(monkeypatch):
    saved: dict = {}

    def save_turn(session_id, user_message, answer, references):
        saved.update(
            session_id=session_id,
            user_message=user_message,
            answer=answer,
            references=references,
        )

    monkeypatch.setattr("app.services.agent._save_turn", save_turn)

    save_execution_turn(
        "session-1",
        "分析甲供应商风险",
        "风险较低",
        [{"name": "甲供应商"}],
    )

    assert saved == {
        "session_id": "session-1",
        "user_message": "分析甲供应商风险",
        "answer": "风险较低",
        "references": [{"name": "甲供应商"}],
    }
