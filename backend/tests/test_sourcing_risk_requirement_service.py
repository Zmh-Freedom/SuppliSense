"""Behavior tests for the sourcing-risk requirement boundary."""

from collections.abc import Callable
import json
from typing import Any

import pytest

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


def test_parse_requirement_extracts_complete_natural_language_purchase_request(monkeypatch):
    monkeypatch.setattr(requirement_service.settings, "LLM_API_KEY", "")

    result = requirement_service.parse_requirement(
        "采购工业摄像头，要求 IP67，支持 PoE，优先华东地区交付"
    )

    assert result["status"] == "ready"
    assert result["requirement"]["category"] == "工业摄像头"
    assert result["requirement"]["specification"] == "IP67，支持 PoE"
    assert result["requirement"]["region"] == "华东"


def test_parse_requirement_uses_deterministic_historical_supplier_fallback(monkeypatch):
    """Historical-supplier wording must not be blocked by an incomplete LLM extraction."""
    monkeypatch.setattr(
        requirement_service,
        "extract_requirement",
        lambda *_: {"category": None, "specification": None},
    )

    result = requirement_service.parse_requirement(
        "后轮制动鼓有哪些历史合作供应商？再补充盖世候选"
    )

    assert result["status"] == "ready"
    assert result["requirement"]["category"] == "后轮制动鼓"
    assert result["requirement"]["specification"] == "后轮制动鼓"


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


def test_explicit_new_sourcing_request_does_not_reuse_previous_requirement(monkeypatch):
    monkeypatch.setattr(requirement_service.settings, "LLM_API_KEY", "")

    previous = {
        "category": "蓄电池",
        "product": "蓄电池",
        "specification": "蓄电池",
    }

    result = requirement_service.resolve_harness_requirement(
        "找一下做安全带的供应商",
        previous,
    )

    assert result["status"] == "ready"
    assert result["requirement"]["category"] == "安全带"
    assert result["requirement"]["specification"] == "安全带"


def test_sourcing_fallback_removes_conversational_filler(monkeypatch):
    monkeypatch.setattr(requirement_service.settings, "LLM_API_KEY", "")

    result = requirement_service.resolve_harness_requirement("找一下蓄电池的供应商")

    assert result["status"] == "ready"
    assert result["requirement"]["category"] == "蓄电池"


@pytest.mark.parametrize(
    "message",
    [
        "做一下蓄电池的寻源",
        "看一下蓄电池的供应商",
        "帮我查一下做蓄电池的供应商",
    ],
)
def test_sourcing_fallback_accepts_common_query_verbs(monkeypatch, message):
    """Common conversational sourcing phrasing must enter the candidate flow."""
    monkeypatch.setattr(requirement_service.settings, "LLM_API_KEY", "")

    result = requirement_service.resolve_harness_requirement(message)

    assert result["status"] == "ready"
    assert result["requirement"]["category"] == "蓄电池"
    assert result["requirement"]["specification"] == "蓄电池"


def test_llm_requirement_is_validated_without_reparsing_user_wording():
    result = requirement_service.resolve_harness_requirement_from_llm({
        "category": "蓄电池",
        "product": "蓄电池",
    })

    assert result["status"] == "ready"
    assert result["extraction_source"] == "llm_validated"
    assert result["requirement"]["specification"] == "蓄电池"


def test_parse_requirement_repairs_unparseable_initial_adapter_response(monkeypatch):
    """Letting malformed initial JSON escape would skip the permitted repair."""
    calls = 0

    def adapter(*_: Any) -> dict[str, Any]:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise json.JSONDecodeError("Expecting value", "not-json", 0)
        return {"category": "摄像头", "specification": "IP67"}

    monkeypatch.setattr(requirement_service, "extract_requirement", adapter)

    result = requirement_service.parse_requirement("采购 IP67 摄像头")

    assert result["status"] == "ready"
    assert result["requirement"]["specification"] == "IP67"
    assert calls == 2


def test_parse_requirement_reports_each_second_validation_error(monkeypatch):
    """Discarding repair errors would ask for category instead of bad constraints."""
    monkeypatch.setattr(
        requirement_service,
        "extract_requirement",
        _responses([
            {"category": 1},
            {
                "category": "摄像头",
                "specification": "x" * 2001,
                "quantity": 0,
                "supplier_status": "active",
            },
        ]),
    )

    result = requirement_service.parse_requirement("采购摄像头")

    assert result == {
        "status": "clarification_required",
        "missing": ["specification", "quantity", "supplier_status"],
    }


def test_parse_requirement_reports_category_when_repair_contains_multiple_categories(
    monkeypatch,
):
    """Treating a repaired multi-category result as valid would create an ambiguous run."""
    monkeypatch.setattr(
        requirement_service,
        "extract_requirement",
        _responses([
            {"category": 1},
            {"category": "摄像头、网关", "specification": "IP67"},
        ]),
    )

    result = requirement_service.parse_requirement("采购摄像头和网关")

    assert result == {
        "status": "clarification_required",
        "missing": ["category"],
    }
