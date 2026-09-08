from app.domains.risk import business_risk_service
from app.core.config import settings


class FakeCollection:
    def __init__(self, documents: list[dict]):
        self.documents = documents

    def find_one(self, query: dict):
        return next((item for item in self.documents if _matches(item, query)), None)

    def find(self, query: dict):
        return [item for item in self.documents if _matches(item, query)]


class FakeDatabase:
    def __init__(self, collections: dict[str, list[dict]]):
        self.collections = {name: FakeCollection(documents) for name, documents in collections.items()}

    def __getitem__(self, name: str):
        return self.collections[name]


def _matches(document: dict, query: dict) -> bool:
    return all(document.get(key) == value for key, value in query.items())


def _transaction(supplier_code: str, amount: float, *, month: str = "2026-08", mode: str = "real") -> dict:
    return {
        "supplier_code": supplier_code,
        "sync_status": "current",
        "data_mode": mode,
        "eligible_for_formal_assessment": mode == "real",
        "data_quality_status": "valid",
        "validation_errors": [],
        "data_quality_issues": [],
        "snapshot_month": month,
        "purchasing_org_code": "PO-1",
        "base": "华南",
        "category_code": "CAM",
        "currency": "CNY",
        "amount_basis": "含税",
        "received_amount": amount,
        "received_qty": amount / 100,
        "unit_price": 100,
        "actual_settlement_amount": amount,
        "unsettled_amount": amount * 0.2,
        "contract_status": "active",
    }


def test_business_risk_p0_returns_dependency_signal_from_real_snapshots(monkeypatch) -> None:
    database = FakeDatabase({
        "supplier_master_snapshots": [{"supplier_id": "supplier:1", "supplier_code": "S-1", "name": "企业一", "sync_status": "current"}],
        "supplier_transaction_snapshots": [
            _transaction("S-1", 800),
            _transaction("S-2", 200),
            _transaction("S-1", 500, month="2026-07"),
        ],
    })
    monkeypatch.setattr(business_risk_service, "get_db", lambda: database)

    result = business_risk_service.assess_business_risk_p0("企业一")

    assert result["assessment_status"] == "partial"
    assert result["coverage"] == 0.30
    assert result["formal_business_score"] is None
    assert result["enabled_dimension"]["risk_level"] == "high"
    assert result["enabled_dimension"]["supplier_spend_share"] == 0.8
    assert result["observed_signals"]["settlement"]["unsettled_ratio"] == 0.2


def test_business_risk_p0_excludes_synthetic_snapshots_from_formal_assessment(monkeypatch) -> None:
    database = FakeDatabase({
        "supplier_master_snapshots": [{"supplier_id": "supplier:1", "supplier_code": "S-1", "name": "企业一", "sync_status": "current"}],
        "supplier_transaction_snapshots": [_transaction("S-1", 1000, mode="synthetic")],
    })
    monkeypatch.setattr(business_risk_service, "get_db", lambda: database)
    monkeypatch.setattr(settings, "DEBUG", False)
    monkeypatch.setattr(settings, "BUSINESS_RISK_DEMO_ENABLED", False)

    result = business_risk_service.assess_business_risk_p0("S-1")

    assert result["assessment_status"] == "missing_data"
    assert result["formal_business_score"] is None
    assert "合成" in result["reason"]


def test_business_risk_p0_uses_synthetic_snapshots_only_in_enabled_debug_demo(monkeypatch) -> None:
    database = FakeDatabase({
        "supplier_master_snapshots": [{"supplier_id": "supplier:1", "supplier_code": "S-1", "name": "企业一", "sync_status": "current"}],
        "supplier_transaction_snapshots": [
            _transaction("S-1", 800, mode="synthetic"),
            _transaction("S-2", 200, mode="synthetic"),
        ],
    })
    monkeypatch.setattr(business_risk_service, "get_db", lambda: database)
    monkeypatch.setattr(settings, "DEBUG", True)
    monkeypatch.setattr(settings, "BUSINESS_RISK_DEMO_ENABLED", True)

    result = business_risk_service.assess_business_risk_p0("S-1")

    assert result["assessment_status"] == "partial"
    assert result["assessment_data_mode"] == "demo"
    assert result["decision_usable"] is False


def test_business_risk_p0_resolves_supplier_by_internal_supplier_id(monkeypatch) -> None:
    database = FakeDatabase({
        "supplier_master_snapshots": [
            {
                "_id": "supplier:feishu:1",
                "supplier_id": "supplier:feishu:1",
                "supplier_code": "S-1",
                "name": "企业一",
                "sync_status": "current",
            }
        ],
        "supplier_transaction_snapshots": [
            _transaction("S-1", 100),
            _transaction("S-2", 100),
        ],
    })
    monkeypatch.setattr(business_risk_service, "get_db", lambda: database)

    result = business_risk_service.assess_business_risk_p0("supplier:feishu:1")

    assert result["assessment_status"] == "partial"
    assert result["supplier"]["supplier_code"] == "S-1"


def test_business_risk_p0_reports_supplier_month_exposure_without_overclaiming(monkeypatch) -> None:
    def monthly(code: str, month: str, amount: float, receipts: int) -> dict:
        return {
            "supplier_code": code,
            "sync_status": "current",
            "data_mode": "real",
            "eligible_for_formal_assessment": True,
            "data_quality_status": "valid",
            "validation_errors": [],
            "data_quality_issues": [],
            "data_granularity": "supplier_month",
            "snapshot_month": month,
            "received_record_count": receipts,
            "actual_settlement_amount": amount,
        }

    database = FakeDatabase({
        "supplier_master_snapshots": [
            {
                "supplier_id": "supplier:1",
                "supplier_code": "S-1",
                "name": "企业一",
                "sync_status": "current",
            }
        ],
        "supplier_transaction_snapshots": [
            monthly("S-1", "2026-07", 200, 8),
            monthly("S-1", "2026-08", 300, 10),
            monthly("S-2", "2026-08", 700, 20),
        ],
    })
    monkeypatch.setattr(business_risk_service, "get_db", lambda: database)

    result = business_risk_service.assess_business_risk_p0("企业一")

    assert result["assessment_status"] == "partial"
    assert result["assessment_scope"] == "business_p0_supplier_month_summary"
    assert result["enabled_dimension"]["name"] == "内部采购敞口"
    assert result["enabled_dimension"]["settlement_share"] == 0.3
    assert result["enabled_dimension"]["latest_actual_settlement_amount"] == 300
    assert result["observed_signals"]["receipts"]["received_record_count"] == 10
    assert result["observed_signals"]["settlement"]["change_ratio"] == 0.5
    assert result["evidence"][0]["facts"]["monthly_trend"] == [
        {"month": "2026-07", "actual_settlement_amount": 200.0, "received_record_count": 8},
        {"month": "2026-08", "actual_settlement_amount": 300.0, "received_record_count": 10},
    ]
    assert "供应依赖与可替代性" in result["not_formally_enabled_dimensions"]
    assert any("不能单独证明供应商自身风险" in item for item in result["limitations"])


def test_business_risk_p0_does_not_treat_missing_month_as_zero(monkeypatch) -> None:
    row = {
        "supplier_code": "S-1",
        "sync_status": "current",
        "data_mode": "real",
        "eligible_for_formal_assessment": True,
        "data_quality_status": "valid",
        "validation_errors": [],
        "data_quality_issues": [],
        "data_granularity": "supplier_month",
        "snapshot_month": "2026-08",
        "received_record_count": 0,
        "actual_settlement_amount": 100,
    }
    database = FakeDatabase({
        "supplier_master_snapshots": [
            {"supplier_id": "supplier:1", "supplier_code": "S-1", "name": "企业一", "sync_status": "current"}
        ],
        "supplier_transaction_snapshots": [row],
    })
    monkeypatch.setattr(business_risk_service, "get_db", lambda: database)

    result = business_risk_service.assess_business_risk_p0("S-1")

    settlement = result["observed_signals"]["settlement"]
    assert settlement["change_ratio"] is None
    assert settlement["settlement_without_receipts_months"] == ["2026-08"]
    assert "2026-07" in result["observed_signals"]["data_continuity"]["missing_months"]
