"""Scope and evidence-boundary tests for dashboard risk predictions."""

from __future__ import annotations

from app.domains.risk import predictor


class _EmptyCursor:
    def sort(self, *_args, **_kwargs):
        return self

    def limit(self, *_args, **_kwargs):
        return []


class _Collection:
    def find(self, *_args, **_kwargs):
        return _EmptyCursor()


class _Db:
    def __getitem__(self, _name):
        return _Collection()


def test_predict_company_without_minimum_evidence_is_unknown(monkeypatch):
    monkeypatch.setattr(predictor, "get_baseinfo", lambda _name: {"company_name": "甲"})
    monkeypatch.setattr(predictor, "get_financial_metrics", lambda _name: None)
    monkeypatch.setattr(predictor, "get_db", lambda: _Db())

    result = predictor.predict_company("甲")

    assert result["probability"] == "unknown"
    assert result["label"] == "当前未覆盖"
    assert result["has_data"] is False


def test_predict_all_uses_current_user_scope(monkeypatch):
    captured = {}

    def visible_targets(user_id, user_role):
        captured.update(user_id=user_id, user_role=user_role)
        return [{"company_name": "甲", "monitor_target_id": "target-1", "target_type": "formal_supplier"}]

    monkeypatch.setattr("app.domains.alert.service.get_watchlist_targets", visible_targets)
    monkeypatch.setattr(predictor, "predict_company", lambda *args, **kwargs: {
        "company_name": args[0], "probability": "unknown", "label": "当前未覆盖", "warning_score": 0,
        "max_score": 14, "signals": [], "has_data": False,
    })

    result = predictor.predict_all("user-1", "analyst")

    assert captured == {"user_id": "user-1", "user_role": "analyst"}
    assert [item["company_name"] for item in result] == ["甲"]
