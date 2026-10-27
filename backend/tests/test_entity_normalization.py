from app.graphs.agent_core.entity_normalization import normalize_company_mention


def test_normalizer_preserves_company_name_starting_with_bi() -> None:
    assert normalize_company_mention("比亚迪") == "比亚迪"


def test_normalizer_strips_explicit_comparison_filler() -> None:
    assert normalize_company_mention("比一下青岛三祥科技股份有限公司") == "青岛三祥科技股份有限公司"


def test_normalizer_strips_chat_and_time_prefixes_from_company_mentions() -> None:
    assert normalize_company_mention("看青岛三祥科技股份有限公司") == "青岛三祥科技股份有限公司"
    assert normalize_company_mention("查一下苏州恒远电子有限公司") == "苏州恒远电子有限公司"
    assert normalize_company_mention("最近30天华东精密有限公司") == "华东精密有限公司"
    assert normalize_company_mention("不要把甲电机有限公司") == "甲电机有限公司"
    assert normalize_company_mention("就是甲电机有限公司") == "甲电机有限公司"
