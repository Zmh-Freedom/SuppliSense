from datetime import datetime, timezone

from scripts.seed_feishu_transaction_snapshot import build_synthetic_records


def test_build_synthetic_records_marks_all_rows_as_synthetic() -> None:
    records = build_synthetic_records([
        {"supplier_code": "S-1", "supplier_name": "供应商一"},
        {"supplier_code": "S-2", "supplier_name": "供应商二"},
        {"supplier_code": "S-3", "supplier_name": "供应商三"},
    ], {"统计月份": 5, "快照日期": 5, "更新时间": 5})

    assert len(records) == 12
    assert {record["数据模式"] for record in records} == {"synthetic"}
    assert all(record["快照ID"].startswith("SYN-TXN-V1-") for record in records)
    assert records[0]["统计月份"] == int(datetime(2026, 5, 1, tzinfo=timezone.utc).timestamp() * 1000)
    assert records[8]["负结算数量"] == -100
    assert records[8]["实结算数量"] == records[8]["正结算数量"] + records[8]["负结算数量"]
    assert records[0]["实结算金额"] == records[0]["已结算金额"] + records[0]["未结算金额"]


def test_build_synthetic_records_serializes_numbers_for_text_columns() -> None:
    records = build_synthetic_records([
        {"supplier_code": "S-1", "supplier_name": "供应商一"},
    ], {"收货数量": 1, "实结算数量": 1, "单价": 1})

    assert records[0]["收货数量"] == "8000"
    assert records[0]["实结算数量"] == "8000"
    assert records[0]["单价"] == "120.00"
