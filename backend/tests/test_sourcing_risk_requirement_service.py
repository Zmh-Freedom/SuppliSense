"""Behavior tests for the sourcing-risk requirement boundary."""

from collections.abc import Callable
from typing import Any

from app.domains.sourcing_risk import requirement_service


def _responses(items: list[dict[str, Any]]) -> Callable[..., dict[str, Any]]:
    responses = iter(items)

    def adapter(*_: Any) -> dict[str, Any]:
        return next(responses)

    return adapter


def test_parse_requirement_requests_clarification_when_specification_is_missing(
    monkeypatch,
):
    """Removing required-field detection would allow an underspecified run."""
    monkeypatch.setattr(
        requirement_service,
        "extract_requirement",
        lambda *_: {"category": "摄像头"},
    )

    result = requirement_service.parse_requirement("找摄像头供应商")

    assert result == {
        "status": "clarification_required",
        "missing": ["specification"],
    }


def test_parse_requirement_repairs_invalid_llm_output_once(monkeypatch):
    """Removing the single repair attempt would reject valid corrected JSON."""
    monkeypatch.setattr(
        requirement_service,
        "extract_requirement",
        _responses([
            {"category": 1},
            {"category": "摄像头", "specification": "IP67"},
        ]),
    )

    result = requirement_service.parse_requirement("采购 IP67 摄像头")

    assert result["status"] == "ready"
    assert result["requirement"]["category"] == "摄像头"


def test_parse_requirement_requests_clarification_for_multiple_categories(monkeypatch):
    """Allowing separator-delimited categories would silently split one task."""
    monkeypatch.setattr(
        requirement_service,
        "extract_requirement",
        lambda *_: {"category": "摄像头、网关", "specification": "IP67"},
    )

    result = requirement_service.parse_requirement("采购摄像头和网关")

    assert result == {
        "status": "clarification_required",
        "missing": ["category"],
    }


def test_parse_requirement_returns_clarification_after_second_invalid_answer(monkeypatch):
    """Allowing a second repair or raising would make invalid output nondeterministic."""
    monkeypatch.setattr(
        requirement_service,
        "extract_requirement",
        _responses([{"category": 1}, {"category": 2}]),
    )

    result = requirement_service.parse_requirement("采购摄像头")

    assert result == {
        "status": "clarification_required",
        "missing": ["category", "specification"],
    }


def test_provided_values_override_extracted_values(monkeypatch):
    """Ignoring explicit request fields could select suppliers for the wrong constraint."""
    monkeypatch.setattr(
        requirement_service,
        "extract_requirement",
        lambda *_: {"category": "摄像头", "specification": "IP65"},
    )

    result = requirement_service.parse_requirement(
        "采购摄像头", {"specification": "IP67", "candidate_count": 5}
    )

    assert result["requirement"] == {
        "category": "摄像头",
        "specification": "IP67",
        "region": None,
        "quantity": None,
        "budget": None,
        "qualifications": None,
        "delivery": None,
        "risk_limit": None,
        "candidate_count": 5,
    }
