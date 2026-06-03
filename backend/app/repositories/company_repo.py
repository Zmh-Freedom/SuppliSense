from datetime import datetime, timezone

from app.db.mongo import get_db
from app.schemas.company import CompanyProfile
from app.schemas.risk import RiskInfo


def _ts_to_date(ts_ms: int | None) -> str:
    if not ts_ms:
        return ""
    return datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d")


def get_baseinfo(company_name: str) -> CompanyProfile | None:
    db = get_db()
    doc = db["baseinfo"].find_one({"name": company_name})
    if not doc:
        return None
    items = doc.get("items") or {}
    result = items.get("result") or {}
    return CompanyProfile(
        company_name=doc.get("name", ""),
        legal_person=result.get("legalPersonName", ""),
        registered_capital=result.get("regCapital", ""),
        establish_time=_ts_to_date(result.get("estiblishTime")),
        is_listed=bool(result.get("bondNum") or result.get("bondName")),
    )


def get_risk_info(company_name: str) -> RiskInfo | None:
    db = get_db()
    base = db["baseinfo"].find_one({"name": company_name})
    if not base:
        return None

    lawsuit = db["lawSuit"].find_one({"name": company_name})
    abnormal = db["abnormal"].find_one({"name": company_name})
    punishment = db["punishmentInfo"].find_one({"name": company_name})

    def _total(doc, field="result") -> int:
        if not doc:
            return 0
        items = doc.get("items") or {}
        r = items.get(field) or {}
        return r.get("total", 0) if isinstance(r, dict) else 0

    return RiskInfo(
        lawsuit_count=_total(lawsuit),
        executed_count=0,
        abnormal_operation_count=_total(abnormal),
        administrative_penalty_count=_total(punishment),
    )


def get_risk_indicators(company_name: str) -> dict:
    """Return extra risk indicators from riskInfo collection."""
    db = get_db()
    doc = db["riskInfo"].find_one({"name": company_name})
    if not doc:
        return _empty_indicators()

    item = doc.get("item") or {}
    result = item.get("result") or {}
    risk_list = result.get("riskList", [])

    indicators = _empty_indicators()

    for category in risk_list:
        for sub in category.get("list", []):
            title = sub.get("title", "")
            total = sub.get("total", 0) or 0

            if "被执行人" in title:
                indicators["executed_count"] += total
            if "失信" in title:
                indicators["dishonesty_count"] += total
            if title in ("裁判文书", "开庭公告", "立案信息"):
                indicators["lawsuit_count"] += total
            if "法定代表人变更" in title:
                indicators["legal_person_change_frequent"] = True
            if "对外担保" in title:
                indicators["guarantee_count"] += total
            if "股权质押" in title:
                indicators["pledge_count"] += total
            if title in ("破产案件", "清算信息", "注销备案"):
                indicators["bankruptcy_count"] += total
            if "环保处罚" in title:
                indicators["env_penalty_count"] += total

    # major_lawsuit: 裁判文书 count >= 3
    for category in risk_list:
        for sub in category.get("list", []):
            if sub.get("title") == "裁判文书":
                indicators["major_lawsuit"] = (sub.get("total", 0) or 0) >= 3

    return indicators


def search_companies(keyword: str, limit: int = 20) -> list[str]:
    db = get_db()
    regex = {"$regex": keyword, "$options": "i"}
    cursor = db["baseinfo"].find({"name": regex}).limit(50)
    names = [doc["name"] for doc in cursor]

    # non-contiguous fallback: "宝钢" → regex "宝.*钢" → matches "宝山钢铁"
    if len(keyword) >= 2:
        fuzzy = ".*".join(keyword)
        cursor2 = db["baseinfo"].find({"name": {"$regex": fuzzy, "$options": "i"}}).limit(50)
        for doc in cursor2:
            if doc["name"] not in names:
                names.append(doc["name"])

    return _rank_and_dedupe(names, keyword)[:limit]


def _rank_and_dedupe(names: list[str], keyword: str) -> list[str]:
    # pre-load listed company names for ranking boost
    db = get_db()
    listed = {doc["name"] for doc in db["baseinfo"].find(
        {"items.result.bondNum": {"$nin": [None, ""]}},
        {"name": 1},
    )}

    # rank: listed > contiguous > prefix > formal > longer
    names = sorted(names, key=lambda n: (
        n in listed,
        keyword in n,
        n.startswith(keyword),
        any(k in n for k in ["有限公司", "股份", "集团", "有限责任"]),
        len(n),
    ), reverse=True)

    # dedupe: if short name is substring of a longer name, remove it
    result = []
    for name in names:
        if not any(name != other and name in other for other in names):
            result.append(name)
    return result


def _empty_indicators() -> dict:
    return {
        "executed_count": 0,
        "dishonesty_count": 0,
        "lawsuit_count": 0,
        "major_lawsuit": False,
        "legal_person_change_frequent": False,
        "guarantee_count": 0,
        "pledge_count": 0,
        "bankruptcy_count": 0,
        "env_penalty_count": 0,
    }
