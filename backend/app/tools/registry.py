"""Versioned registry for every LangGraph tool exposed by the Agent."""

from __future__ import annotations

from typing import Literal

from langchain_core.tools import BaseTool, StructuredTool
from pydantic import BaseModel, ConfigDict, Field

from app.tools.contracts import TOOL_OUTPUT_MODELS


class ToolSpec(BaseModel):
    """Execution policy attached to one registered tool."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str = Field(min_length=1, max_length=128)
    version: str = Field(default="1.0", min_length=1, max_length=32)
    capability: str = Field(min_length=1, max_length=64)
    side_effect: Literal["read", "write"]
    approval_policy: Literal["none", "required"]
    timeout_seconds: int = Field(default=60, gt=0, le=300)
    max_attempts: int = Field(default=1, gt=0, le=3)
    idempotent: bool = True
    evidence_required: bool = False


class ToolDefinition(BaseModel):
    """Tool implementation plus its validated input/output contracts."""

    model_config = ConfigDict(arbitrary_types_allowed=True, frozen=True)

    spec: ToolSpec
    tool: BaseTool
    input_model: type[BaseModel]
    output_model: type[BaseModel]


class ToolPayload(BaseModel):
    """Common structural output schema for existing dict-returning tools."""

    model_config = ConfigDict(extra="allow")


class ToolRegistry:
    """Single source of truth for tools available to all graph runtimes."""

    def __init__(self) -> None:
        self._definitions: dict[str, ToolDefinition] = {}

    def register(
        self,
        tool: BaseTool,
        spec: ToolSpec,
        *,
        output_model: type[BaseModel] = ToolPayload,
    ) -> None:
        input_model = tool.args_schema
        if not isinstance(input_model, type) or not issubclass(input_model, BaseModel):
            raise TypeError(f"工具 {tool.name} 缺少 Pydantic 输入契约")
        if tool.name != spec.name:
            raise ValueError(f"工具名与 ToolSpec 不一致: {tool.name} != {spec.name}")
        if tool.name in self._definitions:
            raise ValueError(f"工具重复注册: {tool.name}")
        self._definitions[tool.name] = ToolDefinition(
            spec=spec,
            tool=tool,
            input_model=input_model,
            output_model=output_model,
        )

    def get(self, name: str) -> ToolDefinition | None:
        return self._definitions.get(name)

    def definitions(self) -> tuple[ToolDefinition, ...]:
        return tuple(self._definitions.values())

    def specs(self) -> tuple[ToolSpec, ...]:
        return tuple(definition.spec for definition in self._definitions.values())

    def langchain_tools(self, executor_factory=None) -> list[BaseTool]:
        """Return ToolNode-compatible wrappers that always use ToolExecutor."""
        from app.tools.executor import ToolContext, ToolExecutor

        executor = executor_factory() if executor_factory else None
        wrapped: list[BaseTool] = []
        for definition in self._definitions.values():
            async def invoke_tool(
                _definition: ToolDefinition = definition,
                **arguments: object,
            ) -> dict:
                active_executor = executor or ToolExecutor(self)
                outcome = await active_executor.execute(
                    _definition.spec.name,
                    arguments,
                    ToolContext(),
                )
                return outcome.model_dump(mode="json")

            wrapped.append(StructuredTool.from_function(
                coroutine=invoke_tool,
                name=definition.tool.name,
                description=definition.tool.description,
                args_schema=definition.input_model,
                infer_schema=False,
            ))
        return wrapped


_WRITE_TOOLS = {
    "add_to_watchlist",
    "remove_from_watchlist",
    "create_sourcing_request",
    "select_sourcing_result",
    "select_external_supplier_candidate",
    "expand_supplier_library",
    "discover_web_suppliers",
    "manage_scheduled_report",
    "generate_report",
}
_EVIDENCE_TOOLS = {
    "assess_risk", "assess_business_risk", "check_alert", "esg_assessment",
    "contagion_analysis", "sentiment_analysis", "check_sanctions", "compare_companies",
    "analyze_trend", "query_financials", "predict_risk", "macro_risk", "find_alternatives",
    "scenario_simulate", "list_formal_suppliers", "search_suppliers", "discover_supplier_candidates", "discover_web_suppliers",
}
_CAPABILITIES = {
    "search_company": "company_lookup", "assess_risk": "risk", "assess_business_risk": "business_risk",
    "check_alert": "risk_monitoring", "get_watchlist": "risk_monitoring", "analyze_watchlist_trend": "risk_monitoring",
    "add_to_watchlist": "risk_monitoring", "remove_from_watchlist": "risk_monitoring", "esg_assessment": "esg",
    "contagion_analysis": "risk_network", "sentiment_analysis": "sentiment", "predict_risk": "risk_prediction",
    "macro_risk": "macro_risk", "find_alternatives": "sourcing", "scenario_simulate": "scenario",
    "check_sanctions": "compliance", "generate_report": "report", "analyze_trend": "risk_monitoring",
    "compare_companies": "risk_comparison", "query_financials": "financial", "manage_scheduled_report": "report",
    "list_formal_suppliers": "sourcing", "create_sourcing_request": "sourcing", "search_suppliers": "sourcing",
    "discover_supplier_candidates": "sourcing",
    "select_sourcing_result": "sourcing", "expand_supplier_library": "sourcing", "discover_web_suppliers": "sourcing",
    "select_external_supplier_candidate": "sourcing",
}


def build_default_tool_registry(tools: list[BaseTool]) -> ToolRegistry:
    """Register the supplied tool list and fail fast on missing policies."""
    registry = ToolRegistry()
    for tool in tools:
        name = tool.name
        output_model = TOOL_OUTPUT_MODELS.get(name)
        if output_model is None:
            raise ValueError(f"生产工具 {name} 缺少严格输出契约")
        registry.register(
            tool,
            ToolSpec(
                name=name,
                capability=_CAPABILITIES.get(name, "unclassified"),
                side_effect="write" if name in _WRITE_TOOLS else "read",
                approval_policy="required" if name in _WRITE_TOOLS else "none",
                max_attempts=2 if name not in _WRITE_TOOLS else 1,
                idempotent=name not in {"create_sourcing_request", "generate_report"},
                evidence_required=name in _EVIDENCE_TOOLS,
            ),
            output_model=output_model,
        )
    return registry


__all__ = ["ToolDefinition", "ToolPayload", "ToolRegistry", "ToolSpec", "build_default_tool_registry"]
