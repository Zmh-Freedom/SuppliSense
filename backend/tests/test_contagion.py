from app.domains.risk import contagion


class _Collection:
    def __init__(self, document: dict | None) -> None:
        self._document = document

    def find_one(self, _query) -> dict | None:
        return self._document


class _Database:
    def __init__(self, branch_document: dict | None) -> None:
        self._branch = _Collection(branch_document)

    def __getitem__(self, name: str) -> _Collection:
        assert name == "branch"
        return self._branch


def test_get_branches_treats_null_nested_result_as_empty(monkeypatch) -> None:
    monkeypatch.setattr(
        contagion,
        "get_db",
        lambda: _Database({"items": {"result": None}}),
    )

    assert contagion.get_branches("重庆红旗弹簧有限公司") == []
