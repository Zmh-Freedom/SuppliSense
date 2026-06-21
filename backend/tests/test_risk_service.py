from app.services.risk_service import _clamp, _score_to_level
from app.repositories.financial_repo import _parse_float


class TestClamp:
    def test_keeps_value_in_range(self):
        assert _clamp(5, 0, 10) == 5

    def test_clamps_high(self):
        assert _clamp(15, 0, 10) == 10

    def test_clamps_low(self):
        assert _clamp(-5, 0, 10) == 0

    def test_boundary_equals_lo(self):
        assert _clamp(0, 0, 10) == 0

    def test_boundary_equals_hi(self):
        assert _clamp(10, 0, 10) == 10


class TestScoreToLevel:
    def test_low_risk(self):
        assert _score_to_level(20) == "低风险"

    def test_medium_risk(self):
        assert _score_to_level(45) == "中风险"

    def test_high_risk(self):
        assert _score_to_level(75) == "高风险"


class TestParseFloat:
    def test_plain_number(self):
        assert _parse_float("123.45") == 123.45

    def test_with_yi(self):
        assert _parse_float("3亿") == 300000000.0

    def test_with_wan(self):
        assert _parse_float("1.5万") == 15000.0

    def test_empty_string(self):
        assert _parse_float("") == 0.0

    def test_none_value(self):
        assert _parse_float(None) == 0.0
