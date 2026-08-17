from app.services.clarification import detect_clarification_needed, ClarificationNeeded


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

    def test_empty_string_returns_none(self):
        result = detect_clarification_needed("   ")
        assert result is None
