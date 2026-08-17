"""Regression tests for the self-reflection prompt."""

from app.graphs.reflection import REFLECTOR_SYSTEM_PROMPT, _parse_reflection


def test_reflector_prompt_formats_json_examples_without_key_error():
    prompt = REFLECTOR_SYSTEM_PROMPT.format(
        tool_results="[assess_risk] result",
        agent_output="风险较低",
        user_query="评估供应商风险",
    )

    assert '{"pass": true, "feedback": ""}' in prompt
    assert "评估供应商风险" in prompt


def test_parse_reflection_preserves_pass_result():
    result = _parse_reflection('{"pass": false, "feedback": "缺少舆情证据"}')

    assert result == {"pass": False, "feedback": "缺少舆情证据"}
