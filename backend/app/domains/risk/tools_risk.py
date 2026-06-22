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
    return result.model_dump()


@tool
def predict_risk(company_name: str) -> dict:
    """预测企业未来6-12月风险恶化概率。

    Args:
        company_name: 企业全称
    """
    from app.domains.risk.predictor import predict_company
    result = predict_company(company_name)
    if result is None:
        return {"error": "未找到企业数据"}
    return result


@tool
def macro_risk(company_name: str) -> dict:
    """分析企业宏观风险（行业PMI景气+地区信用+政策标签）。

    Args:
        company_name: 企业全称
    """
    from app.domains.risk.macro_service import assess_macro_risk
    return assess_macro_risk(company_name)


@tool
def scenario_simulate(company_name: str, scenario: str = "bankruptcy") -> dict:
    """模拟供应商倒闭/诉讼等情景下的影响。

    Args:
        company_name: 企业全称
        scenario: 情景类型，可选值: bankruptcy(破产), lawsuit(诉讼), disruption(供应中断), quality(质量)
    """
    from app.domains.risk.scenario_service import simulate
    return simulate(company_name, scenario)
