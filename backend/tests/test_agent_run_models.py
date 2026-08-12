from datetime import datetime, timezone
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.domains.agent_run.models import (
    ALLOWED_STATUS_TRANSITIONS,
    ActionProposalStatus,
    AgentRunStatus,
    CandidateSource,
    CandidateStatus,
)
from app.domains.agent_run.schemas import (
    AgentRunEventResponse,
    AgentRunResponse,
    ApprovalDecisionRequest,
    ClarificationRequest,
    CreateSourcingRiskRunRequest,
)


def test_agent_run_status_allows_clarification_and_rejects_terminal_resume():
    assert AgentRunStatus.CLARIFYING in ALLOWED_STATUS_TRANSITIONS[AgentRunStatus.CREATED]
    assert AgentRunStatus.LOCAL_SEARCHING not in ALLOWED_STATUS_TRANSITIONS[AgentRunStatus.COMPLETED]


def test_terminal_statuses_have_no_outgoing_transitions():
    for status in (
        AgentRunStatus.COMPLETED,
        AgentRunStatus.PARTIAL,
        AgentRunStatus.NEEDS_REVIEW,
        AgentRunStatus.ACTION_FAILED,
        AgentRunStatus.FAILED,
        AgentRunStatus.CANCELLED,
    ):
        assert ALLOWED_STATUS_TRANSITIONS[status] == frozenset()


def test_create_request_rejects_multiple_categories():
    with pytest.raises(ValidationError, match="一个任务只能包含一个采购品类"):
        CreateSourcingRiskRunRequest(
            requirement_text="采购摄像头和网关",
            category="摄像头、网关",
            specification="IP67",
        )


def test_create_request_strips_category_and_applies_candidate_default():
    request = CreateSourcingRiskRunRequest(requirement_text="采购摄像头", category="  摄像头  ")
    assert request.category == "摄像头"
    assert request.expected_candidate_count == 3


def test_mutation_requests_require_positive_expected_version():
    for request_type, kwargs in (
        (ClarificationRequest, {"expected_version": 0, "answers": {"region": "华东"}}),
        (ApprovalDecisionRequest, {"expected_version": 0, "decision": "approved"}),
    ):
        with pytest.raises(ValidationError):
            request_type(**kwargs)


def test_event_response_contains_replay_fields_and_response_is_immutable():
    run_id = uuid4()
    event = AgentRunEventResponse(
        event_id=1,
        run_id=run_id,
        version=2,
        event_type="stage",
        occurred_at=datetime.now(timezone.utc),
        data={"status": AgentRunStatus.LOCAL_SEARCHING.value},
    )
    assert event.event_id == 1
    with pytest.raises(ValidationError):
        event.version = 3


def test_nested_request_payloads_reject_in_place_mutation():
    request = ClarificationRequest(
        expected_version=1,
        answers={"region": {"name": "华东"}},
    )
    with pytest.raises(TypeError):
        request.answers["region"]["name"] = "华南"


def test_nested_event_and_candidate_payloads_reject_in_place_mutation():
    run_id = uuid4()
    event = AgentRunEventResponse(
        event_id=1,
        run_id=run_id,
        version=1,
        event_type="candidate_batch",
        occurred_at=datetime.now(timezone.utc),
        data={"items": [{"name": "供应商 A"}]},
    )
    response = AgentRunResponse(
        run_id=run_id,
        status=AgentRunStatus.CREATED,
        version=1,
        requirement=CreateSourcingRiskRunRequest(requirement_text="采购摄像头"),
        candidates=[{"name": "供应商 A", "tags": ["local"]}],
    )
    with pytest.raises(TypeError):
        event.data["items"].append({"name": "供应商 B"})
    with pytest.raises(TypeError):
        response.candidates[0]["name"] = "供应商 B"
    with pytest.raises(TypeError):
        response.candidates.append({"name": "供应商 C"})


def test_public_contracts_expose_core_enum_values():
    assert CandidateSource.LOCAL.value == "local"
    assert CandidateSource.STAGED_EXTERNAL.value == "staged_external"
    assert CandidateStatus.STAGED_CANDIDATE.value == "staged_candidate"
    assert ActionProposalStatus.PENDING.value == "pending"
