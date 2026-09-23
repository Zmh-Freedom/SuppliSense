from app.graphs.agent_core.entity_normalization import normalize_company_mention


def test_normalizer_preserves_company_name_starting_with_bi() -> None:
    assert normalize_company_mention("比亚迪") == "比亚迪"


def test_normalizer_strips_explicit_comparison_filler() -> None:
    assert normalize_company_mention("比一下青岛三祥科技股份有限公司") == "青岛三祥科技股份有限公司"
