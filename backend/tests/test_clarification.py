from app.services.clarification import (
    detect_clarification_needed,
    external_assessment_clarification,
    review_scope_clarification,
)


class TestClarification:
    def test_analysis_without_company_returns_clarification(self):
        result = detect_clarification_needed("帮我分析一下风险")
        assert result is not None
        assert "哪家公司" in result.message

    def test_analysis_with_company_returns_none(self):
        result = detect_clarification_needed("分析海康威视数字技术股份有限公司的风险")
        assert result is None

    def test_short_company_name_returns_none(self):
        result = detect_clarification_needed("海康威视的风险怎么样")
        assert result is None

    def test_greeting_returns_none(self):
        result = detect_clarification_needed("你好")
        assert result is None

    def test_simple_query_returns_none(self):
        result = detect_clarification_needed("有哪些监控企业")
        assert result is None

    def test_responsible_supplier_trend_query_does_not_require_company_name(self):
        result = detect_clarification_needed("查询本人负责供应商本月风险变化")
        assert result is None

    def test_assess_keyword_with_company(self):
        result = detect_clarification_needed("评估大华股份")
        assert result is None

    def test_multiple_companies_joined_by和_are_not_blocked(self):
        result = detect_clarification_needed(
            "那先看一下深圳市立创电子和八方电气的风险情况"
        )
        assert result is None

    def test_known_session_company_satisfies_clarification_guard(self):
        result = detect_clarification_needed(
            "那先看一下它的风险情况",
            known_company_names=["深圳市立创电子"],
        )
        assert result is None

    def test_known_session_companies_satisfy_plural_reference(self):
        result = detect_clarification_needed(
            "那对这两家做一下ESG评估、舆情分析",
            known_company_names=["深圳市立创电子有限公司", "八方电气（苏州）股份有限公司"],
        )
        assert result is None

    def test_known_session_companies_satisfy_these_companies_reference(self):
        result = detect_clarification_needed(
            "那对这些企业进行风险评估",
            known_company_names=["深圳市立创电子有限公司", "八方电气（苏州）股份有限公司"],
        )
        assert result is None

    def test_these_companies_without_context_still_requires_clarification(self):
        result = detect_clarification_needed("那对这些企业进行风险评估")
        assert result is not None

    def test_empty_string_returns_none(self):
        result = detect_clarification_needed("   ")
        assert result is None

    def test_review_outside_formal_and_monitor_scope_returns_external_assessment_hint(self, monkeypatch):
        monkeypatch.setattr("app.domains.alert.service.get_watchlist_targets", lambda **_: [])
        monkeypatch.setattr("app.domains.supplier.access.formal_supplier_exists_by_name", lambda _: False)

        result = review_scope_clarification(
            ["华为"], [], "user-1", "analyst", "复核华为"
        )

        assert result is not None
        assert "监控清单和正式供应商库中均未找到" in result.message
        assert "评估该企业风险" in result.message

    def test_review_formal_supplier_outside_user_scope_is_denied(self, monkeypatch):
        monkeypatch.setattr("app.domains.alert.service.get_watchlist_targets", lambda **_: [])
        monkeypatch.setattr("app.domains.supplier.access.formal_supplier_exists_by_name", lambda _: True)
        monkeypatch.setattr("app.domains.supplier.access.can_access_formal_supplier_name", lambda *_: False)

        result = review_scope_clarification(
            ["其他供应商有限公司"], [], "user-1", "analyst", "复核其他供应商有限公司"
        )

        assert result is not None
        assert "不在你当前负责范围内" in result.message

    def test_unknown_analysis_subject_is_stopped_before_risk_scoring(self, monkeypatch):
        monkeypatch.setattr("app.domains.alert.service.get_watchlist_targets", lambda **_: [])
        monkeypatch.setattr("app.domains.supplier.access.formal_supplier_exists_by_name", lambda _: False)

        result = review_scope_clarification(
            ["华为"], [], "user-1", "analyst", "分析华为的风险"
        )

        assert result is not None
        assert "不能直接生成供应商风险评分" in result.message
        assert "评估该企业风险" in result.message

    def test_short_formal_supplier_name_requests_identity_confirmation(self, monkeypatch):
        monkeypatch.setattr("app.domains.supplier.access.formal_supplier_exists_by_name", lambda _: False)
        monkeypatch.setattr(
            "app.domains.sourcing.supplier_repo.find_formal_supplier_candidates",
            lambda _: [{
                "supplier_id": "supplier:网易云",
                "supplier_name": "杭州网易云音乐科技有限公司",
                "short_name": "网易云",
                "supplier_code": "8330267",
                "match_type": "name_contains",
                "match_score": 0.92,
            }],
        )

        from app.services.clarification import formal_supplier_identity_clarification

        result = formal_supplier_identity_clarification(["网易云音乐"], "分析网易云音乐的风险")

        assert result is not None
        assert result.missing == ["supplier_identity"]
        assert "杭州网易云音乐科技有限公司" in result.message
        assert result.candidates[0]["supplier_id"] == "supplier:网易云"

    def test_historical_reference_does_not_authorize_unknown_subject(self, monkeypatch):
        monkeypatch.setattr("app.domains.alert.service.get_watchlist_targets", lambda **_: [])
        monkeypatch.setattr("app.domains.supplier.access.formal_supplier_exists_by_name", lambda _: False)

        result = review_scope_clarification(
            ["华为"], [{"name": "华为", "source": "conversation_state"}], "user-1", "analyst", "分析华为的风险"
        )

        assert result is not None
        assert "不能直接生成供应商风险评分" in result.message

    def test_external_assessment_requires_subject_confirmation(self, monkeypatch):
        class SearchTool:
            @staticmethod
            def invoke(_payload):
                return {"results": [{"name": "华为技术有限公司", "credit_code": "91440300"}]}

        monkeypatch.setattr("app.domains.risk.tools_search.search_company", SearchTool())
        monkeypatch.setattr("app.domains.supplier.access.formal_supplier_exists_by_name", lambda _: False)

        result = external_assessment_clarification(["华为"], [], "评估华为")

        assert result is not None
        assert "华为技术有限公司" in result.message
        assert "企业全称" in result.message
