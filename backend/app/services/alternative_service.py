"""
智能替代建议服务。

当供应商风险升高时，自动推荐同行业低风险替代企业。

策略：
  1. 优先从监控清单中匹配同行业低风险企业
  2. 补充搜索天眼查数据库中的同行业未监控企业
  3. 按风险评分升序排列，推荐 Top 5
"""

from app.db.mongo import get_db
from app.repositories.company_repo import get_baseinfo


def _get_industry(company_name: str) -> str:
    """提取企业核心行业关键词。"""
    db = get_db()
    doc = db["baseinfo"].find_one({"name": company_name})
    if not doc:
        return ""
    result = (doc.get("items") or {}).get("result") or {}
    industry = result.get("industry", "") or result.get("industryAll", "")
    # extract first meaningful segment
    if industry:
        parts = [p for p in industry.replace("/", ",").split(",") if len(p) > 1]
        return parts[0].strip() if parts else industry
    return ""


def _same_industry(industry1: str, industry2: str) -> bool:
    """判断两个行业是否相关。"""
    if not industry1 or not industry2:
        return False
    # exact match
    if industry1 == industry2:
        return True
    # partial match
    for kw in [industry1[:4], industry1[:3], industry1[:2]]:
        if len(kw) >= 2 and kw in industry2:
            return True
    return False


def find_alternatives(company_name: str, limit: int = 5) -> dict:
    """为高风险供应商寻找替代企业。"""
    db = get_db()

    # get company info
    profile = get_baseinfo(company_name)
    source_industry = _get_industry(company_name)

    # get current risk score
    snap = db["alert_snapshots"].find_one(
        {"company_name": company_name}, sort=[("checked_at", -1)]
    )
    source_score = snap.get("risk_score", 0) if snap else None

    # find all watchlist companies in same/similar industry
    candidates = []
    all_watched = [d["company_name"] for d in db["watchlist"].find()]

    for name in all_watched:
        if name == company_name:
            continue

        ind = _get_industry(name)

        # check industry match
        if not _same_industry(source_industry, ind):
            # also check from watchlist doc for manual category
            continue

        # get risk score
        s = db["alert_snapshots"].find_one(
            {"company_name": name}, sort=[("checked_at", -1)]
        )
        score = s.get("risk_score", 50) if s else 50
        level = s.get("risk_level", "未知") if s else "未知"

        candidates.append({
            "company_name": name,
            "industry": ind,
            "risk_score": score,
            "risk_level": level,
            "source": "watchlist",
        })

    # if not enough candidates, search Tianyancha baseinfo
    if len(candidates) < limit:
        # search all companies in same industry
        base_query = {}
        if source_industry:
            base_query = {
                "$or": [
                    {"items.result.industry": {"$regex": source_industry[:4]}},
                    {"items.result.industryAll": {"$regex": source_industry[:4]}},
                ]
            }
        external_docs = list(
            db["baseinfo"].find(base_query, {"name": 1}).limit(20)
        )
        existing_names = {company_name} | {c["company_name"] for c in candidates} | set(all_watched)
        for doc in external_docs:
            ext_name = doc.get("name", "")
            if ext_name not in existing_names:
                candidates.append({
                    "company_name": ext_name,
                    "industry": source_industry,
                    "risk_score": None,
                    "risk_level": "未评估",
                    "source": "external",
                })
                existing_names.add(ext_name)

    # sort: low risk first, known risk before unknown
    candidates.sort(key=lambda c: (
        c["risk_score"] if c["risk_score"] is not None else 50,
    ))

    # filter: only suggest companies with lower risk
    if source_score is not None:
        better = [c for c in candidates if c["risk_score"] is None or c["risk_score"] < source_score]
        if better:
            candidates = better

    return {
        "company_name": company_name,
        "source_industry": source_industry,
        "source_risk_score": source_score,
        "alternatives_count": len(candidates),
        "alternatives": candidates[:limit],
    }


def get_alternative_dashboard() -> dict:
    """全局替代建议看板：找出所有高风险企业并为它们推荐替代。"""
    db = get_db()
    companies = [d["company_name"] for d in db["watchlist"].find()]

    high_risk_companies = []
    for name in companies:
        snap = db["alert_snapshots"].find_one(
            {"company_name": name}, sort=[("checked_at", -1)]
        )
        if snap and snap.get("risk_score", 0) >= 60:
            alt = find_alternatives(name, limit=3)
            high_risk_companies.append({
                "company_name": name,
                "risk_score": snap.get("risk_score", 0),
                "risk_level": snap.get("risk_level", "未知"),
                "alternatives": alt["alternatives"],
            })

    high_risk_companies.sort(key=lambda c: c["risk_score"], reverse=True)

    return {
        "high_risk_count": len(high_risk_companies),
        "total_monitored": len(companies),
        "companies": high_risk_companies,
    }
