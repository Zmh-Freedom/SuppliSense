"""Focused regressions for chat intent routing compatibility."""

import pytest

from app.graphs.router import Intent, IntentRouter


def test_composite_sourcing_risk_message_routes_to_supervisor() -> None:
    router = IntentRouter()

    assert router.route("帮我找华东电机供应商并评估风险") == Intent.SUPERVISOR


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        ("分析监控清单中本月风险变化趋势", Intent.RISK),
        ("帮我找华东电机供应商", Intent.SOURCING),
        ("帮我制定方案评估某公司的风险", Intent.PLAN_EXECUTE),
        ("从多角度评估某公司的风险", Intent.MULTI_AGENT),
    ],
)
def test_single_domain_messages_keep_existing_routes(
    message: str, expected: Intent
) -> None:
    router = IntentRouter()

    assert router.route(message) == expected


def test_simple_risk_message_keeps_react_route(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    router = IntentRouter()
    monkeypatch.setattr(router, "_classify_by_llm", lambda _message: Intent.RISK)

    assert router.route("评估某公司的司法风险") == Intent.RISK


def test_llm_supervisor_classification_is_supported(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    router = IntentRouter()
    monkeypatch.setattr(router, "_classify_by_llm", lambda _message: Intent.SUPERVISOR)

    assert router.route("请综合判断业务风险") == Intent.SUPERVISOR
