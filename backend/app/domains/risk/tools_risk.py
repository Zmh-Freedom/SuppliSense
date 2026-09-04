"""风险评估工具。"""
from langchain_core.tools import tool


@tool
def assess_risk(company_name: str) -> dict:
    """评估供应商风险，返回风险评分、财报、风险明细。

    Args:
        company_name: 企业全称
    """
    from app.schemas import RiskAssessRequest
    from app.domains.risk.service import assess_risk as _assess
    result = _assess(RiskAssessRequest(company_name=company_name))
    from app.tools.evidence import attach_tool_evidence

    return attach_tool_evidence(
        result.model_dump(),
        tool_name="assess_risk",
        entity_id=f"entity:{company_name}",
        dimension="risk",
        source_type="risk_service_result",
        claim_fields=["risk_score", "risk_level"],
    )


@tool
def predict_risk(company_name: str) -> dict:
    """预测企业未来6-12月风险恶化概率。

    Args:
        company_name: 企业全称
    """
    from app.domains.risk.predictor import predict_company
    result = predict_company(company_name)
    if result is None:
        return {"status": "not_found", "error": "未找到企业数据", "company_name": company_name}
    from app.tools.evidence import attach_tool_evidence

    return attach_tool_evidence(result, tool_name="predict_risk", entity_id=f"entity:{company_name}", dimension="risk_prediction")


@tool
def macro_risk(company_name: str) -> dict:
    """分析企业宏观风险（行业PMI景气+地区信用+政策标签）。

    Args:
        company_name: 企业全称
    """
    from app.domains.risk.macro_service import assess_macro_risk
    from app.tools.evidence import attach_tool_evidence

    return attach_tool_evidence(assess_macro_risk(company_name), tool_name="macro_risk", entity_id=f"entity:{company_name}", dimension="macro_risk")


@tool
def scenario_simulate(company_name: str, scenario: str = "bankruptcy") -> dict:
    """模拟供应商倒闭/诉讼等情景下的影响。

    Args:
        company_name: 企业全称
        scenario: 情景类型，可选值: bankruptcy(破产), lawsuit(诉讼), disruption(供应中断), quality(质量)
    """
    from app.domains.risk.scenario_service import simulate
    from app.tools.evidence import attach_tool_evidence

    return attach_tool_evidence(simulate(company_name, scenario), tool_name="scenario_simulate", entity_id=f"entity:{company_name}", dimension="scenario")


@tool
def assess_business_risk(supplier_reference: str, category_code: str = "") -> dict:
    """评估正式供应商的商务风险 P0。

    仅使用飞书真实且校验通过的交易月度快照，当前正式输出供应依赖与
    可替代性；合同、结算和价格仅作为观察信号，不会被误报为完整评分。

    Args:
        supplier_reference: 飞书供应商代码或供应商全称。
        category_code: 可选品类代码；供应商有多个采购品类时建议提供。
    """
    from app.domains.risk.business_risk_service import assess_business_risk_p0

    from app.tools.evidence import attach_tool_evidence

    return attach_tool_evidence(assess_business_risk_p0(
        supplier_reference,
        category_code=category_code or None,
    ), tool_name="assess_business_risk", entity_id=f"entity:{supplier_reference}", dimension="business_risk", claim_fields=[
        "risk_level", "supplier_spend_share", "active_supplier_count",
    ])


@tool
def assess_operational_risk(company_name: str, dimension: str) -> dict:
    """评估质量或交付风险，仅使用正式且校验通过的内部快照。

    缺少质量/交付指标时返回数据不足，不会把缺失当成低风险。

    Args:
        company_name: 企业全称
        dimension: 风险维度，只能是 quality 或 delivery
    """
    if dimension not in {"quality", "delivery"}:
        return {
            "status": "invalid",
            "dimension": dimension,
            "company_name": company_name,
            "error": "dimension 必须是 quality 或 delivery",
        }
    from app.domains.risk.operational_risk_service import assess_operational_risk as _assess
    from app.tools.evidence import attach_tool_evidence

    result = _assess(company_name, dimension)  # type: ignore[arg-type]
    return attach_tool_evidence(
        result,
        tool_name="assess_operational_risk",
        entity_id=f"entity:{company_name}",
        dimension=dimension,
        claim_fields=["risk_score", "risk_level"],
    )
