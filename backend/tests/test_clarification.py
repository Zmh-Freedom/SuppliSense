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

    def test_empty_string_returns_none(self):
        result = detect_clarification_needed("   ")
        assert result is None
