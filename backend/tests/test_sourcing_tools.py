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
    assert result["error"] == "scope_restricted"


def test_supplier_library_expansion_is_disabled_in_current_scope() -> None:
    result = tools.expand_supplier_library.invoke({"keyword": "钢材"})

    assert result["success"] is False
    assert result["error"] == "scope_restricted"
    assert "不自动写入供应商主数据" in result["message"]


def test_formal_supplier_directory_emits_one_supported_claim_per_supplier(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.domains.sourcing.supplier_repo.list_formal_suppliers",
        lambda limit: {
            "total": 1,
            "items": [{
                "supplier_id": "supplier:formal-1",
                "supplier_name": "甲公司",
                "source": "feishu_bitable",
            }],
        },
    )

    result = tools.list_formal_suppliers.invoke({"limit": 20})

    assert len(result["evidence_records"]) == 2
    assert len(result["claims"]) == 1
    assert result["claims"][0]["fact_path"] == "supplier_name"
    supplier_record = next(
        item for item in result["evidence_records"]
        if item["facts"].get("supplier_name") == "甲公司"
    )
    assert supplier_record["content_hash"]
