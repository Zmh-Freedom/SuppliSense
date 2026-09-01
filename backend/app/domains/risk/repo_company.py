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
        industry=result.get("industry", ""),
    )


def get_risk_info(company_name: str) -> RiskInfo | None:
    db = get_db()
    base = db["baseinfo"].find_one({"name": company_name})
    if not base:
        return None

    def _total(doc, field="result") -> int:
        if not doc:
            return 0
        items = doc.get("items") or {}
        result = items.get(field) or {}
        if isinstance(result, dict) and isinstance(result.get("total"), (int, float)):
            return int(result["total"])
        # Tianyancha lawSuit 3.0 uses items.total, while most endpoints use
        # items.result.total.  Keep the reader compatible with both snapshots.
        if isinstance(items.get("total"), (int, float)):
            return int(items["total"])
        return 0

    # Always read individual collections as ground-truth supplement
    lawsuit = db["lawSuit"].find_one({"name": company_name})
    court_register = db["courtRegister"].find_one({"name": company_name})
    abnormal = db["abnormal"].find_one({"name": company_name})
    punishment = db["punishmentInfo"].find_one({"name": company_name})
    executed = db["executedPerson"].find_one({"name": company_name})

    # 1. Try new riskInfo collection (from /services/open/risk/riskInfo/2.0)
    risk_doc = db["riskInfo"].find_one({"name": company_name})
    if risk_doc:
        item = risk_doc.get("item") or risk_doc.get("items") or {}
        result = item.get("result") or {}
        risk_list = result.get("riskList", [])
        if risk_list:
            lawsuit_count = 0
            abnormal_count = 0
            penalty_count = 0
            for cat in risk_list:
                for sub in cat.get("list", []):
                    title = sub.get("title", "")
                    total = sub.get("total", 0) or 0
                    if title in ("裁判文书", "开庭公告", "立案信息"):
                        lawsuit_count += total
                    if "经营异常" in title:
                        abnormal_count += total
                    if "行政处罚" in title:
                        penalty_count += total
            # Supplement zero counts from individual collections
            if lawsuit_count == 0:
                lawsuit_count = _total(lawsuit) + _total(court_register)
            if abnormal_count == 0:
                abnormal_count = _total(abnormal)
            if penalty_count == 0:
                penalty_count = _total(punishment)
            return RiskInfo(
                lawsuit_count=lawsuit_count,
                executed_count=_total(executed),
                abnormal_operation_count=abnormal_count,
                administrative_penalty_count=penalty_count,
            )

    # 2. Fallback: old individual collections
    return RiskInfo(
        lawsuit_count=_total(lawsuit) + _total(court_register),
        executed_count=_total(executed),
        abnormal_operation_count=_total(abnormal),
        administrative_penalty_count=_total(punishment),
    )


def get_risk_indicators(company_name: str) -> dict:
    """Return extra risk indicators from riskInfo + individual collections."""
    db = get_db()

    def _total(coll: str) -> int:
        doc = db[coll].find_one({"name": company_name})
        if not doc:
            return 0
        items = doc.get("items") or {}
        r = items.get("result") or {}
        if isinstance(r, dict) and isinstance(r.get("total"), (int, float)):
            return int(r["total"])
        if isinstance(items.get("total"), (int, float)):
            return int(items["total"])
        return 0

    indicators = _empty_indicators()

    # Read from riskInfo if available
    doc = db["riskInfo"].find_one({"name": company_name})
    if doc:
        item = doc.get("item") or {}
        result = item.get("result") or {}
        risk_list = result.get("riskList", [])

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

    # Supplement zero counts from individual collections (only those with dedicated endpoints)
    if indicators["lawsuit_count"] == 0:
        indicators["lawsuit_count"] = (
            _total("lawSuit") + _total("courtRegister")
        )
    if indicators["executed_count"] == 0:
        indicators["executed_count"] = _total("executedPerson")
    if indicators["dishonesty_count"] == 0:
        indicators["dishonesty_count"] = _total("dishonesty")
    if indicators["pledge_count"] == 0:
        indicators["pledge_count"] = _total("equityPledge")
    # major_lawsuit: also check individual lawsuit collection
    if not indicators["major_lawsuit"]:
        indicators["major_lawsuit"] = _total("lawSuit") >= 3

    return indicators


def normalize_company_name(name: str) -> str:
    """
    将短名规范化为全称。防止同一企业因名称不一致导致数据缺失。

    规则：如果 name 是某个已知全称的真子串（如"海康威视"⊂"杭州海康威视..."），
    且全称有完整风险数据，则返回全称。
    """
    if not name or len(name) < 3:
        return name

    db = get_db()
    # 精确匹配优先
    if db["baseinfo"].find_one({"name": name}):
        risk_doc = db["riskInfo"].find_one({"name": name})
        if _has_risk_data(risk_doc):
            return name

    # 模糊匹配：查找包含 name 的全称
    candidates = list(db["baseinfo"].find(
        {"name": {"$regex": name, "$options": "i"}},
        {"name": 1},
    ).limit(20))

    for doc in candidates:
        full_name = doc["name"]
        if full_name == name:
            continue
        if name not in full_name:
            continue
        # 只有全称有风险数据时才替换
        if _has_risk_data(db["riskInfo"].find_one({"name": full_name})):
            return full_name

    return name


def _has_risk_data(doc: dict | None) -> bool:
    """检查风险文档是否包含实际数据。"""
    if not doc:
        return False
    item = doc.get("item", {}) or {}
    result = item.get("result", {}) or {}
    for cat in result.get("riskList", []):
        for sub in cat.get("list", []):
            if sub.get("total", 0) > 0:
                return True
    return False


def search_companies(keyword: str, limit: int = 20) -> list[str]:
    db = get_db()
    regex = {"$regex": keyword, "$options": "i"}
    cursor = db["baseinfo"].find({"name": regex}).limit(50)
    names = [_clean_company_name(doc["name"]) for doc in cursor]

    if len(keyword) >= 2:
        fuzzy = ".*".join(keyword)
        cursor2 = db["baseinfo"].find({"name": {"$regex": fuzzy, "$options": "i"}}).limit(50)
        for doc in cursor2:
            cleaned = _clean_company_name(doc["name"])
            if cleaned not in names:
                names.append(cleaned)

    return _rank_and_dedupe(names, keyword)[:limit]


def _clean_company_name(name: str) -> str:
    """去除天眼查返回的标记前缀（× 注销, ※ 异常等）。"""
    return name.lstrip("×※*#").strip()


_listed_cache: tuple[set[str], float] | None = None


def _get_listed_companies() -> set[str]:
    global _listed_cache
    import time
    now = time.time()
    if _listed_cache is None or (now - _listed_cache[1]) > 300:  # 5 min TTL
        db = get_db()
        _listed_cache = (
            {doc["name"] for doc in db["baseinfo"].find(
                {"items.result.bondNum": {"$nin": [None, ""]}},
                {"name": 1},
            )},
            now,
        )
    return _listed_cache[0]


def _rank_and_dedupe(names: list[str], keyword: str) -> list[str]:
    listed = _get_listed_companies()

    # 先去重完全相同的名称（保留首次出现顺序）
    seen: set[str] = set()
    unique: list[str] = []
    for n in names:
        if n not in seen:
            seen.add(n)
            unique.append(n)
    names = unique

    names = sorted(names, key=lambda n: (
        n in listed,
        keyword in n,
        n.startswith(keyword),
        any(k in n for k in ["有限公司", "股份", "集团", "有限责任"]),
        len(n),
    ), reverse=True)

    result = []
    for name in names:
        if not any(name != other and name in other for other in names):
            result.append(name)
    return result


def get_recent_lawsuits(company_name: str, years: int = 3) -> int:
    """
    从 lawSuit_detail 获取近 N 年裁判文书数量（按 judgeTime 过滤）。

    lawSuit_detail 由 tianyancha_client 翻页拉取全量后存入。
    """
    from datetime import datetime, timedelta, timezone
    db = get_db()
    doc = db["lawSuit_detail"].find_one({"name": company_name})
    if not doc:
        return 0
    cutoff = datetime.now(timezone.utc) - timedelta(days=years * 365)
    count = 0
    for item in doc.get("items", []):
        jt = item.get("judgeTime", "")
        if jt:
            try:
                jd = datetime.strptime(jt, "%Y-%m-%d").replace(tzinfo=timezone.utc)
                if jd >= cutoff:
                    count += 1
            except ValueError:
                pass
    return count


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
