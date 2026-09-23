import pytest
from types import SimpleNamespace

from app.core.structured_response import parse_json_object_content
from app.domains.risk.soft_risk import _call_llm


def test_parse_json_object_content_accepts_code_fence_and_provider_wrapper() -> None:
    parsed = parse_json_object_content(
        '```json\n{"type":"json_object","content":"{\\"ok\\":true}"}\n```',
        operation="test",
    )

    assert parsed == {"ok": True}


@pytest.mark.parametrize("content", [None, "", "   ", "模型暂时无法回答"])
def test_parse_json_object_content_reports_non_json_response(content: str | None) -> None:
    with pytest.raises(ValueError, match="structured_response_"):
        parse_json_object_content(content, operation="test")


def test_soft_risk_retries_once_after_malformed_json(monkeypatch) -> None:
    valid = '{"dimensions":{"舆情风险":{"score":1,"reason":"近期舆情较少"}},"summary":"风险较低"}'
    contents = iter(["", valid])
    prompts: list[str] = []

    class FakeClient:
        class chat:
            class completions:
                @staticmethod
                def create(**kwargs):
                    prompts.append(kwargs["messages"][0]["content"])
                    return SimpleNamespace(
                        choices=[SimpleNamespace(
                            message=SimpleNamespace(content=next(contents)),
                            finish_reason="stop",
                        )],
                        usage=SimpleNamespace(completion_tokens=10),
                    )

    monkeypatch.setattr("app.domains.risk.soft_risk.settings.LLM_API_KEY", "test-key")
    monkeypatch.setattr("app.domains.risk.soft_risk.OpenAI", lambda **_kwargs: FakeClient())

    result = _call_llm("测试供应商有限公司", None)

    assert result is not None
    assert result["summary"] == "风险较低"
    assert len(prompts) == 2
    assert "完整 JSON" in prompts[1]
