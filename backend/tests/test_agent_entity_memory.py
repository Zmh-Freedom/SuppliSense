"""P0 tests for deterministic entity memory and turn resolution."""

from app.graphs.agent_core.entity_memory import (
    EntityIdentityStatus,
    EntityMemory,
    MentionMatchType,
    memory_from_state,
    resolve_turn,
)


REFERENCES = [
    {
        "name": "深圳市立创电子有限公司",
        "aliases": ["立创电子"],
        "supplier_code": "SUP-001",
        "supplier_id": "supplier-1",
        "candidate_type": "local",
        "source": "feishu_formal_suppliers",
    },
    {
        "name": "八方电气（苏州）股份有限公司",
        "aliases": ["八方电气"],
        "supplier_code": "SUP-002",
        "candidate_id": "candidate-2",
        "candidate_type": "external",
        "source": "discover_web_suppliers",
    },
]


def test_explicit_current_company_beats_previous_focus_and_creates_stable_memory() -> None:
    first = resolve_turn("推荐电机供应商", session_id="session-a", references=REFERENCES)
    second = resolve_turn(
        "对四川建安工业有限责任公司做风险分析",
        session_id="session-a",
        previous_memory=first.memory,
        references=REFERENCES,
        turn_id="turn-2",
    )

    assert second.target_supplier_names == ["四川建安工业有限责任公司"]
    assert second.reason == "explicit_name_or_code"
    pending = next(item for item in second.memory.entities if item.canonical_name == "四川建安工业有限责任公司")
    assert pending.identity_status == EntityIdentityStatus.PENDING_VERIFICATION
    assert second.focus_set is not None
    assert second.focus_set.entity_ids == [pending.entity_id]


def test_alias_and_supplier_code_bind_to_existing_entities() -> None:
    result = resolve_turn(
        "对立创电子和 SUP-002 做风险分析",
        session_id="session-a",
        references=REFERENCES,
        turn_id="turn-1",
    )

    assert result.target_supplier_names == [
        "深圳市立创电子有限公司",
        "八方电气（苏州）股份有限公司",
    ]
    assert {item.match_type for item in result.memory.mentions} == {
        MentionMatchType.ALIAS,
        MentionMatchType.SUPPLIER_CODE,
    }
    assert all(item.identity_status != EntityIdentityStatus.PENDING_VERIFICATION for item in result.memory.entities)


def test_plural_and_singular_references_only_use_previous_focus_set() -> None:
    first = resolve_turn(
        "推荐电机供应商",
        session_id="session-a",
        references=REFERENCES,
        turn_id="turn-1",
    )
    plural = resolve_turn(
        "对这两家做风险和舆情分析",
        session_id="session-a",
        previous_memory=first.memory,
        references=REFERENCES,
        turn_id="turn-2",
    )
    singular = resolve_turn(
        "再看这家合规情况",
        session_id="session-a",
        previous_memory=plural.memory,
        references=REFERENCES,
        turn_id="turn-3",
    )

    assert plural.target_supplier_names == [item["name"] for item in REFERENCES]
    assert singular.target_supplier_names == [REFERENCES[0]["name"]]


def test_unknown_llm_candidate_stays_pending_and_is_not_verified() -> None:
    result = resolve_turn(
        "做风险分析",
        session_id="session-a",
        references=REFERENCES,
        llm_candidates=["未知供应商有限公司"],
    )

    assert result.target_supplier_names == ["未知供应商有限公司"]
    assert result.memory.entities[-1].identity_status == EntityIdentityStatus.PENDING_VERIFICATION
    assert result.memory.mentions[-1].match_type == MentionMatchType.LLM_CANDIDATE


def test_memory_from_state_does_not_leak_between_sessions() -> None:
    first = resolve_turn("对深圳市立创电子有限公司做风险分析", session_id="session-a")
    restored = memory_from_state({"entity_memory": first.memory.model_dump(mode="json")}, session_id="session-a")
    other = resolve_turn("对这家公司做风险分析", session_id="session-b", previous_memory=EntityMemory(session_id="session-b"))

    assert restored.session_id == "session-a"
    assert other.target_supplier_names == []
    assert other.needs_clarification is True


def test_explicit_company_name_ignores_copied_spaces_inside_branch_name() -> None:
    result = resolve_turn(
        "查找纬湃汽车电子 （长春）有限公司的最新舆情和新闻动态",
        session_id="session-a",
    )

    assert result.target_supplier_names == ["纬湃汽车电子（长春）有限公司"]
    assert result.reason == "explicit_name_or_code"


def test_common_confirmation_and_display_prefixes_are_not_persisted_as_company_name() -> None:
    messages = [
        "看看青岛三祥科技股份有限公司的综合风险",
        "是上海海拉电子有限公司，继续查风险",
        "展示上海海拉电子有限公司的经营风险",
        "给青岛三祥科技股份有限公司设置每周风险报告",
    ]

    for message in messages:
        result = resolve_turn(message, session_id="prefix-cleanup")
        assert result.target_supplier_names == [
            "上海海拉电子有限公司" if "上海海拉" in message else "青岛三祥科技股份有限公司"
        ]


def test_scheduled_report_prefix_is_not_persisted_as_company_name() -> None:
    result = resolve_turn(
        "请每周生成一次上海海拉电子有限公司的风险报告，并在生成前让我确认",
        session_id="scheduled-report-prefix",
    )

    assert result.target_supplier_names == ["上海海拉电子有限公司"]


def test_contextual_follow_up_reuses_canonical_name_after_spoken_prefix() -> None:
    first = resolve_turn(
        "看看青岛三祥科技股份有限公司的综合风险",
        session_id="context-prefix",
        turn_id="turn-1",
    )
    second = resolve_turn(
        "它需要采取采购动作吗？",
        session_id="context-prefix",
        previous_memory=first.memory,
        turn_id="turn-2",
    )

    assert first.target_supplier_names == ["青岛三祥科技股份有限公司"]
    assert second.target_supplier_names == ["青岛三祥科技股份有限公司"]
    assert second.reason == "singular_reference"
