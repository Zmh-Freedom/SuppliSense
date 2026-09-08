from app.domains.sourcing.internal_history_import_service import parse_history_row


def test_parse_history_row_preserves_supplier_material_and_base_identifiers():
    relation = parse_history_row((8110026, "北京示例有限公司", 23987432, "自动变速器油", 1000))

    assert relation is not None
    assert relation["supplier_code"] == "8110026"
    assert relation["material_number"] == "23987432"
    assert relation["base_code"] == "1000"
    assert relation["source_provider"] == "internal_supplier_material_list"


def test_parse_history_row_skips_missing_required_values():
    assert parse_history_row((8110026, "北京示例有限公司", None, "自动变速器油", 1000)) is None
