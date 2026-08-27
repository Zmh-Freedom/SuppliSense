from __future__ import annotations

import csv
import json

from openpyxl import Workbook

from scripts.convert_supplier_workbook import convert_workbook


def _add_sheet(workbook: Workbook, title: str, headers: list[str], row: list[object]) -> None:
    sheet = workbook.create_sheet(title)
    sheet.append(headers)
    sheet.append(row)


def test_convert_supplier_workbook_creates_three_feishu_csvs(tmp_path) -> None:
    source = tmp_path / "supplier-registration.xlsx"
    output_dir = tmp_path / "feishu-import"
    workbook = Workbook()
    workbook.remove(workbook.active)
    _add_sheet(
        workbook,
        "1-基础信息",
        ["供货商代码", "供应商简称", "供应商全称", "社会统一信用代码", "成立日期", "主要产品", "是否上市"],
        ["S-001", "示例供应商", "示例供应商有限公司", "91310000TEST", "2024-01-02", "钢材，板材", "否"],
    )
    _add_sheet(
        workbook,
        "2-联系人信息",
        ["供应商代码", "职务", "联系人姓名", "电话", "邮箱", "是否商务联系人"],
        ["S-001", "销售经理", "张三", "13800000000", "sales@example.com", "是"],
    )
    _add_sheet(
        workbook,
        "3-生产地信息",
        ["供应商代码", "主要产品", "省份", "市", "厂房面积（平方米）"],
        ["S-001", "板材", "江苏省", "苏州市", "10000"],
    )
    workbook.save(source)

    report = convert_workbook(source, output_dir, default_category="钢材")

    assert report["counts"] == {
        "供应商主数据": 1,
        "供应商供货能力": 1,
        "供应商联系人": 1,
        "warnings": 0,
        "errors": 0,
    }
    with (output_dir / "供应商主数据.csv").open(encoding="utf-8-sig", newline="") as handle:
        assert list(csv.DictReader(handle))[0]["供应商代码"] == "S-001"
    with (output_dir / "供应商供货能力.csv").open(encoding="utf-8-sig", newline="") as handle:
        assert list(csv.DictReader(handle))[0]["品类"] == "钢材"
    with (output_dir / "供应商联系人.csv").open(encoding="utf-8-sig", newline="") as handle:
        assert list(csv.DictReader(handle))[0]["联系人类型"] == "商务"
    with (output_dir / "conversion_report.json").open(encoding="utf-8") as handle:
        assert json.load(handle)["unmapped_source_sheets"] == ["5-业务能力", "6-主要客户", "7-关联企业"]
