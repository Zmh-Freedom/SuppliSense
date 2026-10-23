from __future__ import annotations

import pytest

from app.domains.alert import case_access


def _resolve(*args, **kwargs):
    resolver = getattr(case_access, "resolve_case_scope", None)
    assert callable(resolver), "resolve_case_scope must be implemented"
    return resolver(*args, **kwargs)


def _capabilities(*args, **kwargs):
    capability_builder = getattr(case_access, "case_capabilities", None)
    assert callable(capability_builder), "case_capabilities must be implemented"
    return capability_builder(*args, **kwargs)


def _install_scope_data(
    monkeypatch: pytest.MonkeyPatch,
    *,
    identities: dict[str, str | None],
    assignments: list[dict],
    targets: dict[str, dict],
) -> None:
    monkeypatch.setattr(
        case_access,
        "feishu_open_id_for_user",
        lambda user_id: identities.get(str(user_id)),
        raising=False,
    )
    monkeypatch.setattr(
        case_access,
        "_current_supplier_assignments",
        lambda supplier_id: assignments,
        raising=False,
    )
    monkeypatch.setattr(
        case_access,
        "_target_for_case",
        lambda target_id: targets.get(str(target_id)),
        raising=False,
    )


def test_manager_scope_does_not_equal_supplier_visibility(monkeypatch: pytest.MonkeyPatch):
    _install_scope_data(
        monkeypatch,
        identities={"buyer": "ou-buyer", "manager": "ou-manager"},
        assignments=[
            {
                "department_code": "D1",
                "purchaser_open_id": "ou-buyer",
                "manager_open_id": "ou-manager",
            }
        ],
        targets={
            "target-1": {
                "monitor_target_id": "target-1",
                "supplier_id": "supplier-1",
                "target_type": "formal_supplier",
            }
        },
    )

    buyer_scope = _resolve(
        {"monitor_target_id": "target-1", "supplier_id": "supplier-1"},
        "buyer",
        "purchaser",
        None,
    )
    manager_scope = _resolve(
        {"monitor_target_id": "target-1", "supplier_id": "supplier-1"},
        "manager",
        "purchaser",
        None,
    )
    buyer_case = {
        "monitor_target_id": "target-1",
        "department_code": "D1",
        "created_by": "buyer",
        "owner_user_id": "buyer",
        "status": "in_progress",
    }
    buyer_capabilities = _capabilities(buyer_case, "buyer", "purchaser")
    manager_capabilities = _capabilities(
        {**buyer_case, "created_by": "buyer", "owner_user_id": "buyer"},
        "manager",
        "purchaser",
    )

    assert buyer_scope == {
        "target_id": "target-1",
        "department_code": "D1",
        "is_manager": False,
    }
    assert manager_scope["is_manager"] is True
    assert buyer_capabilities["can_read"] is True
    assert buyer_capabilities["can_assign"] is False
    assert buyer_capabilities["can_decide_closure"] is False
    assert manager_capabilities["can_read"] is True
    assert manager_capabilities["can_assign"] is True


def test_multiple_supplier_departments_require_explicit_selection(monkeypatch: pytest.MonkeyPatch):
    _install_scope_data(
        monkeypatch,
        identities={"buyer": "ou-buyer"},
        assignments=[
            {"department_code": "D1", "purchaser_open_id": "ou-buyer", "manager_open_id": "ou-m1"},
            {"department_code": "D2", "purchaser_open_id": "ou-buyer", "manager_open_id": "ou-m2"},
        ],
        targets={"target-1": {"monitor_target_id": "target-1", "supplier_id": "supplier-1"}},
    )
    target = {"monitor_target_id": "target-1", "supplier_id": "supplier-1"}

    with pytest.raises(ValueError, match="科室"):
        _resolve(target, "buyer", "purchaser", None)

    assert _resolve(target, "buyer", "purchaser", "D2")["department_code"] == "D2"

    wrong_department = _capabilities(
        {
            "monitor_target_id": "target-1",
            "department_code": "D3",
            "created_by": "buyer",
            "owner_user_id": "buyer",
            "status": "in_progress",
        },
        "buyer",
        "purchaser",
    )
    assert wrong_department["can_read"] is False


def test_revoked_department_scope_removes_read_and_update(monkeypatch: pytest.MonkeyPatch):
    _install_scope_data(
        monkeypatch,
        identities={"buyer": "ou-buyer"},
        assignments=[],
        targets={"target-1": {"monitor_target_id": "target-1", "supplier_id": "supplier-1"}},
    )

    capabilities = _capabilities(
        {
            "monitor_target_id": "target-1",
            "department_code": "D1",
            "created_by": "buyer",
            "owner_user_id": "buyer",
            "status": "in_progress",
        },
        "buyer",
        "purchaser",
    )

    assert capabilities["can_read"] is False
    assert capabilities["can_update"] is False


def test_external_targets_are_private_to_owner_and_admin(monkeypatch: pytest.MonkeyPatch):
    _install_scope_data(
        monkeypatch,
        identities={"owner": "ou-owner", "other": "ou-other"},
        assignments=[],
        targets={
            "external-1": {
                "monitor_target_id": "external-1",
                "target_type": "external_candidate",
                "owner_user_id": "owner",
            }
        },
    )
    target = {
        "monitor_target_id": "external-1",
        "target_type": "external_candidate",
        "owner_user_id": "owner",
    }

    assert _resolve(target, "owner", "purchaser", None)["department_code"] is None
    with pytest.raises(PermissionError):
        _resolve(target, "other", "purchaser", None)
    assert _resolve(target, "admin", "admin", None)["department_code"] is None


def test_applicant_cannot_approve_own_closure(monkeypatch: pytest.MonkeyPatch):
    _install_scope_data(
        monkeypatch,
        identities={},
        assignments=[],
        targets={"external-1": {"monitor_target_id": "external-1", "owner_user_id": "owner"}},
    )

    capabilities = _capabilities(
        {
            "monitor_target_id": "external-1",
            "department_code": None,
            "created_by": "owner",
            "owner_user_id": "owner",
            "status": "pending_closure",
            "closure_request": {"requested_by": "admin"},
        },
        "admin",
        "admin",
    )

    assert capabilities["can_read"] is True
    assert capabilities["can_decide_closure"] is False


def test_admin_can_read_case_after_department_mapping_disappears(monkeypatch: pytest.MonkeyPatch):
    _install_scope_data(
        monkeypatch,
        identities={},
        assignments=[],
        targets={"target-1": {"monitor_target_id": "target-1", "supplier_id": "supplier-1"}},
    )

    capabilities = _capabilities(
        {
            "monitor_target_id": "target-1",
            "department_code": "D1",
            "created_by": "former-buyer",
            "owner_user_id": "former-buyer",
            "status": "pending_closure",
            "closure_request": {"requested_by": "former-buyer"},
        },
        "admin",
        "admin",
    )

    assert capabilities["can_read"] is True
    assert capabilities["can_decide_closure"] is True


def test_viewer_has_no_case_capabilities(monkeypatch: pytest.MonkeyPatch):
    _install_scope_data(
        monkeypatch,
        identities={},
        assignments=[],
        targets={"target-1": {"monitor_target_id": "target-1", "supplier_id": "supplier-1"}},
    )

    capabilities = _capabilities(
        {
            "monitor_target_id": "target-1",
            "department_code": "D1",
            "created_by": "viewer",
            "owner_user_id": "viewer",
            "status": "in_progress",
        },
        "viewer",
        "viewer",
    )

    assert not any(capabilities.values())


def test_legacy_analyst_retains_purchaser_scope(monkeypatch: pytest.MonkeyPatch):
    _install_scope_data(
        monkeypatch,
        identities={"analyst": "ou-analyst"},
        assignments=[
            {"department_code": "D1", "purchaser_open_id": "ou-analyst", "manager_open_id": "ou-manager"}
        ],
        targets={},
    )

    scope = _resolve(
        {"monitor_target_id": "target-1", "supplier_id": "supplier-1"},
        "analyst",
        "analyst",
        None,
    )

    assert scope["department_code"] == "D1"
    assert scope["is_manager"] is False


def test_responsibility_lookup_failure_fails_closed(monkeypatch: pytest.MonkeyPatch):
    _install_scope_data(
        monkeypatch,
        identities={"buyer": "ou-buyer"},
        assignments=[],
        targets={"target-1": {"monitor_target_id": "target-1", "supplier_id": "supplier-1"}},
    )
    monkeypatch.setattr(
        case_access,
        "_current_supplier_assignments",
        lambda _supplier_id: (_ for _ in ()).throw(RuntimeError("database unavailable")),
    )

    with pytest.raises(PermissionError):
        _resolve({"monitor_target_id": "target-1", "supplier_id": "supplier-1"}, "buyer", "purchaser", None)

    capabilities = _capabilities(
        {
            "monitor_target_id": "target-1",
            "department_code": "D1",
            "created_by": "buyer",
            "owner_user_id": "buyer",
            "status": "in_progress",
        },
        "buyer",
        "purchaser",
    )
    assert not any(capabilities.values())
