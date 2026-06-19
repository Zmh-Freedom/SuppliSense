from fastapi import HTTPException

from app.core.cache import cached
from app.repositories.company_repo import get_baseinfo, get_risk_info
from app.schemas.company import CompanyProfile
from app.schemas.risk import RiskInfo
from app.services.tianyancha_client import fetch_company


@cached("company_profile", ttl=7200)
def get_company_profile(company_name: str) -> CompanyProfile:
    from app.repositories.company_repo import normalize_company_name

    name = normalize_company_name(company_name)
    profile = get_baseinfo(name)
    if profile is None:
        _try_fetch_from_api(name)
        profile = get_baseinfo(name)
        # If still not found, try fuzzy search (short name → full name in DB)
        if profile is None:
            from app.repositories.company_repo import search_companies
            matches = search_companies(name, limit=1)
            if matches:
                profile = get_baseinfo(matches[0])
    if profile is None:
        raise HTTPException(status_code=404, detail=f"企业 '{company_name}' 未找到")
    return profile


@cached("company_risk", ttl=7200)
def get_company_risk(company_name: str) -> RiskInfo:
    risk = get_risk_info(company_name)
    if risk is None:
        _try_fetch_from_api(company_name)
        risk = get_risk_info(company_name)
        if risk is None:
            from app.repositories.company_repo import search_companies
            matches = search_companies(company_name, limit=1)
            if matches:
                risk = get_risk_info(matches[0])
    if risk is None:
        raise HTTPException(status_code=404, detail=f"企业 '{company_name}' 未找到")
    return risk


def _try_fetch_from_api(company_name: str) -> None:
    try:
        ok = fetch_company(company_name)
        # If direct call failed, try resolving to full name
        if not ok:
            from app.repositories.financial_repo import resolve_full_name
            full = resolve_full_name(company_name)
            if full and full != company_name:
                fetch_company(full)
    except RuntimeError:
        pass
    except Exception:
        pass
