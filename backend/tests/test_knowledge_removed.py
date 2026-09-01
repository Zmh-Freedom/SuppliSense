"""Knowledge document ingestion is intentionally outside the current scope."""

from app.main import app


def test_knowledge_and_generic_document_upload_routes_are_removed() -> None:
    paths = {route.path for route in app.routes if hasattr(route, "path")}

    assert "/api/v1/knowledge/upload" not in paths
    assert "/api/v1/knowledge/search" not in paths
    assert "/api/v1/upload/upload" not in paths


def test_agent_tool_catalog_does_not_expose_knowledge_search() -> None:
    from app.tools import TOOLS_LIST

    assert all(tool.name != "knowledge_search" for tool in TOOLS_LIST)
