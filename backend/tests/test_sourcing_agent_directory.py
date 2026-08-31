import asyncio

from langchain_core.messages import HumanMessage

from app.graphs.agents.sourcing import _is_formal_supplier_directory_query, _sourcing_agent


def _state(message: str) -> dict:
    return {
        "messages": [HumanMessage(content=message)],
        "request_input": None,
        "candidates": [],
        "risk_results": {},
        "final_results": [],
        "error": None,
        "conversation_state": {},
        "current_task": {},
    }


def test_formal_supplier_directory_query_is_recognized() -> None:
    assert _is_formal_supplier_directory_query(_state("当前正式供应商有哪些？"))


def test_formal_supplier_directory_query_returns_read_only_directory(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.domains.sourcing.supplier_repo.list_formal_suppliers",
        lambda limit: {
            "total": 1,
            "items": [{
                "supplier_code": "8110023",
                "supplier_name": "汉拿万都（北京）汽车部件有限公司",
                "categories": ["汽车零部件"],
                "regions": ["北京"],
            }],
        },
    )

    result = asyncio.run(_sourcing_agent(_state("当前正式供应商有哪些？")))
    answer = result["messages"][0].content

    assert "当前共有 1 家正式（已准入）供应商" in answer
    assert "汉拿万都（北京）汽车部件有限公司" in answer
    assert result["messages"][0].tool_calls == []


def test_supplier_recommendation_does_not_force_directory_tool() -> None:
    assert not _is_formal_supplier_directory_query(_state("给我推荐汽车零部件供应商"))
