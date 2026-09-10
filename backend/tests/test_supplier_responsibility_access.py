"""Access rules for the Feishu-owned supplier responsibility model."""

from contextlib import contextmanager

from app.domains.supplier import access


def test_unlinked_user_has_no_formal_supplier_scope(monkeypatch):
    monkeypatch.setattr(access, "_feishu_open_id_for_user", lambda _user_id: None)

    assert access.list_assigned_supplier_ids("user-1", "viewer") == set()


def test_manager_scope_uses_department_and_purchaser_open_id(monkeypatch):
    captured = {}

    class Cursor:
        def execute(self, query, params):
            captured["query"] = query
            captured["params"] = params

        def fetchall(self):
            return [("supplier-a",), ("supplier-b",)]

    @contextmanager
    def cursor_context():
        yield None, Cursor()

    monkeypatch.setattr(access, "_feishu_open_id_for_user", lambda _user_id: "ou-manager")
    monkeypatch.setattr(access, "get_cursor", cursor_context)

    assert access.list_assigned_supplier_ids("user-1", "analyst") == {"supplier-a", "supplier-b"}
    assert captured["params"] == ("ou-manager", "ou-manager")
    assert "manager_open_id" in captured["query"]
    assert "source_active = TRUE" in captured["query"]


def test_admin_bypasses_formal_supplier_scope():
    assert access.can_access_supplier("supplier-a", "admin-user", "admin") is True
