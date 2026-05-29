from fastapi import HTTPException

from app.repositories.company_repo import get_baseinfo, get_risk_info
from app.schemas.company import CompanyProfile
from app.schemas.risk import RiskInfo
from app.services.tianyancha_client import fetch_company


def get_company_profile(company_name: str) -> CompanyProfile:
    profile = get_baseinfo(company_name)
    if profile is None:
        _try_fetch_from_api(company_name)
        profile = get_baseinfo(company_name)
    if profile is None:
        raise HTTPException(status_code=404, detail=f"企业 '{company_name}' 未找到")
    return profile


def get_company_risk(company_name: str) -> RiskInfo:
    risk = get_risk_info(company_name)
    if risk is None:
        _try_fetch_from_api(company_name)
        risk = get_risk_info(company_name)
    if risk is None:
        raise HTTPException(status_code=404, detail=f"企业 '{company_name}' 未找到")
    return risk


def _try_fetch_from_api(company_name: str) -> None:
    try:
        fetch_company(company_name)
    except RuntimeError:
        pass  # TOKEN 未配置，跳过 API 调用
    except Exception:
        pass  # API 调用失败不影响主流程
