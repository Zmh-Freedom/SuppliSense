from app.domains.sourcing.gaishi_import_service import parse_gaishi_chip_row, parse_gaishi_row


def test_parse_gaishi_row_keeps_external_evidence_and_category_relation():
    parsed = parse_gaishi_row(
        "制动系统",
        "制动系统.xlsx",
        (
            "上海 示例 汽车 部件有限公司", "A股 | 示例000001.SZ", None, "高新技术企业", "民营企业",
            "收录3年", "上海市", "5000万人民币", "2019-01-01", "制动系统，制动卡钳", "示例主机厂",
        ),
    )

    assert parsed is not None
    profile, link = parsed
    assert profile["company_name"] == "上海 示例 汽车 部件有限公司"
    assert profile["company_key"] == "上海示例汽车部件有限公司"
    assert profile["main_products"] == "制动系统，制动卡钳"
    assert profile["source_provider"] == "gasgoo_manual_export"
    assert link["category"] == "制动系统"
    assert link["source_file"] == "制动系统.xlsx"
    assert link["supplier_profile_id"] == profile["_id"]


def test_parse_gaishi_row_skips_blank_company_name():
    assert parse_gaishi_row("制动系统", "制动系统.xlsx", (None,) * 11) is None


def test_parse_gaishi_chip_row_maps_the_special_workbook_to_chip_category():
    parsed = parse_gaishi_chip_row("芯片.xlsx", ("示例芯片有限公司", "上海市", "已上市", "设计企业"))

    assert parsed is not None
    profile, link = parsed
    assert profile["location"] == "上海市"
    assert profile["tags"] == ["已上市", "设计企业"]
    assert link["category"] == "芯片"
    assert link["source_file"] == "芯片.xlsx"
