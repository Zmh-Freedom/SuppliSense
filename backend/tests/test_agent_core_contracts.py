from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from app.graphs.agent_core.contracts import (
    AgentSubtask,
    AgentTask,
    ConversationState,
    LoopState,
    migrate_conversation_state,
)
from app.graphs.agent_core.adapter import collect_supplier_references


def test_conversation_state_migrates_legacy_references_without_losing_contacts():
    state = migrate_conversation_state(
        {
            "active_suppliers": [
                {
                    "name": "华东钢材供应有限公司",
                    "source": "tianyancha_search",
                    "website_url": "https://steel.example.com",
                    "website_status": "unverified",
                    "contact_phone": "021-12345678",
                    "contact_status": "unverified",
                }
            ],
            "selected_suppliers": ["华东钢材供应有限公司"],
            "current_task": {"user_message": "评估这家企业风险"},
        },
        session_id="session-1",
    )

    assert state.schema_version == 1
    assert state.active_suppliers[0].website_url == "https://steel.example.com"
    assert state.active_suppliers[0].contact_phone == "021-12345678"
    assert state.active_suppliers[0].discovery_source == "tianyancha_search"
    assert state.current_task is not None
    assert state.current_task.target_supplier_names == ["华东钢材供应有限公司"]


def test_conversation_state_is_json_serializable():
    state = ConversationState(
        session_id="session-1",
        updated_at=datetime.now(timezone.utc),
    )

    payload = state.model_dump(mode="json")

    assert payload["schema_version"] == 1
    assert payload["session_id"] == "session-1"


def test_collect_supplier_references_merges_same_supplier_evidence():
    collected = collect_supplier_references(
        [{"name": "甲公司", "candidate_id": "candidate-1"}],
        {"supplier_name": "甲公司", "contact_phone": "0755-12345678"},
        "supplier_detail",
    )

    assert collected == [{
        "name": "甲公司",
        "candidate_id": "candidate-1",
        "kind": "supplier",
        "source": "supplier_detail",
        "contact_phone": "0755-12345678",
    }]


def test_agent_task_rejects_duplicate_supplier_dimension_subtasks():
    with pytest.raises(ValidationError):
        AgentTask(
            task_id="task-1",
            task_type="analysis",
            target_supplier_names=["甲公司"],
            analysis_dimensions=["risk"],
            subtasks=[
                AgentSubtask(subtask_id="risk-1", supplier_name="甲公司", dimension="risk"),
                AgentSubtask(subtask_id="risk-2", supplier_name="甲公司", dimension="risk"),
            ],
        )


def test_loop_state_rejects_iterations_beyond_budget():
    with pytest.raises(ValidationError):
        LoopState(
            loop_type="evidence",
            iteration=3,
            max_iterations=2,
            tool_call_count=1,
            max_tool_calls=4,
            started_at=datetime.now(timezone.utc),
            timeout_seconds=30,
            evidence_count_before=0,
        )
