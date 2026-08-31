from app.domains.risk import repo_company


class _Collection:
    def __init__(self, document: dict | None) -> None:
        self._document = document

    def find_one(self, _query: dict) -> dict | None:
        return self._document


class _Database:
    def __init__(self, documents: dict[str, dict | None]) -> None:
        self._documents = documents

    def __getitem__(self, name: str) -> _Collection:
        return _Collection(self._documents.get(name))


def _provider_total(total: int) -> dict:
    return {"items": {"result": {"total": total}}}


def test_risk_info_supplements_lawsuits_with_court_register(monkeypatch) -> None:
    database = _Database({
        "baseinfo": {"name": "示例供应商", "items": {"result": {}}},
        "lawSuit": _provider_total(2),
        "courtRegister": _provider_total(3),
        "abnormal": _provider_total(0),
        "punishmentInfo": _provider_total(0),
        "executedPerson": _provider_total(0),
        "riskInfo": None,
    })
    monkeypatch.setattr(repo_company, "get_db", lambda: database)

    risk = repo_company.get_risk_info("示例供应商")

    assert risk is not None
    assert risk.lawsuit_count == 5


def test_risk_indicators_supplement_lawsuits_with_court_register(monkeypatch) -> None:
    database = _Database({
        "riskInfo": None,
        "lawSuit": _provider_total(2),
        "courtRegister": _provider_total(3),
        "executedPerson": _provider_total(0),
        "dishonesty": _provider_total(0),
        "equityPledge": _provider_total(0),
    })
    monkeypatch.setattr(repo_company, "get_db", lambda: database)

    indicators = repo_company.get_risk_indicators("示例供应商")

    assert indicators["lawsuit_count"] == 5
