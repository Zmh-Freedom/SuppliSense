from app.graphs.streaming import (
    _access_write_succeeded,
    _guard_access_answer,
)


def test_access_answer_is_blocked_without_durable_application_receipt() -> None:
    answer = _guard_access_answer(
        "对深圳市云钥科技有限公司执行准入申请",
        "深圳市云钥科技有限公司已完成准入，正式成为合格供应商。",
        access_succeeded=False,
    )

    assert "尚未提交" in answer
    assert "已完成准入" not in answer


def test_access_answer_can_claim_success_only_with_application_id() -> None:
    result = {"success": True, "application_id": "application-1"}

    assert _access_write_succeeded("select_external_supplier_candidate", result)
    assert _guard_access_answer("确认准入申请", "已完成准入。", True) == "已完成准入。"


def test_access_failure_result_does_not_count_as_success() -> None:
    assert not _access_write_succeeded(
        "select_external_supplier_candidate",
        {"success": False, "error": "approval_context_required"},
    )
