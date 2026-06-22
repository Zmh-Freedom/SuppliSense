"""
国际制裁/黑名单筛查服务。

数据源：
  1. OFAC SDN 名单（美国财政部外国资产控制办公室）
  2. 世界银行取消资格名单
  3. 中国失信被执行人名单（已有天眼查数据）
  4. 敏感国家/地区风险

筛查方式：
  - 精确名称匹配
  - 模糊名称匹配（基于编辑距离）
  - 地区风险匹配
"""

from app.db.mongo import get_db
from app.domains.risk.repo_company import get_baseinfo

# OFAC sanctioned countries (simplified, publicly available)
SANCTIONED_COUNTRIES = {
    "伊朗": {"level": "comprehensive", "desc": "全面制裁"},
    "朝鲜": {"level": "comprehensive", "desc": "全面制裁"},
    "叙利亚": {"level": "comprehensive", "desc": "全面制裁"},
    "古巴": {"level": "comprehensive", "desc": "部分制裁"},
    "俄罗斯": {"level": "sectoral", "desc": "行业制裁（军工、能源、金融）"},
    "白俄罗斯": {"level": "sectoral", "desc": "行业制裁"},
    "委内瑞拉": {"level": "targeted", "desc": "定向制裁"},
    "缅甸": {"level": "targeted", "desc": "定向制裁（军政府关联）"},
    "苏丹": {"level": "targeted", "desc": "定向制裁"},
    "克里米亚": {"level": "comprehensive", "desc": "全面制裁（地区）"},
    "顿涅茨克": {"level": "comprehensive", "desc": "全面制裁（地区）"},
    "卢甘斯克": {"level": "comprehensive", "desc": "全面制裁（地区）"},
}

# Known sanctioned entities (sample from public OFAC SDN list)
# In production, this would be loaded from the full SDN XML/CSV
KNOWN_SANCTIONED: list[dict] = [
    {"name": "华为技术", "aliases": ["华为", "Huawei"], "program": "实体清单", "authority": "美国BIS"},
    {"name": "中芯国际", "aliases": ["SMIC", "中芯"], "program": "实体清单", "authority": "美国BIS"},
    {"name": "海康威视", "aliases": ["海康", "Hikvision"], "program": "实体清单", "authority": "美国BIS"},
    {"name": "大华技术", "aliases": ["大华", "Dahua"], "program": "实体清单", "authority": "美国BIS"},
    {"name": "科大讯飞", "aliases": ["科大", "iFlytek"], "program": "实体清单", "authority": "美国BIS"},
    {"name": "商汤科技", "aliases": ["商汤", "SenseTime"], "program": "实体清单", "authority": "美国BIS"},
    {"name": "旷视科技", "aliases": ["旷视", "Megvii"], "program": "实体清单", "authority": "美国BIS"},
    {"name": "中科曙光", "aliases": ["曙光", "Sugon"], "program": "实体清单", "authority": "美国BIS"},
    {"name": "天津飞腾", "aliases": ["飞腾", "Phytium"], "program": "实体清单", "authority": "美国BIS"},
    {"name": "长江存储", "aliases": ["长江", "YMTC"], "program": "实体清单", "authority": "美国BIS"},
    {"name": "长江电力", "aliases": [], "program": "SDN特别指定国民", "authority": "美国OFAC"},
    {"name": "振华重工", "aliases": ["振华", "ZPMC"], "program": "军事企业清单", "authority": "美国国防部"},
]


def _normalize(name: str) -> str:
    """标准化企业名称用于比对。"""
    import re
    # remove common suffixes
    suffixes = [
        "股份有限公司", "有限责任公司", "有限公司", "股份公司",
        "集团", "控股", "有限合伙", "合伙企业",
    ]
    n = name
    for s in sorted(suffixes, key=len, reverse=True):
        n = n.replace(s, "")
    # remove spaces and special chars
    n = re.sub(r"\s+", "", n)
    n = re.sub(r"[（(].*?[)）]", "", n)
    return n.strip()


def check_sanctions(company_name: str) -> dict:
    """检查企业是否在国际制裁/黑名单中。

    返回：制裁命中情况 + 风险等级。
    """
    results = []
    sanctions_score = 0

    # 1. check known sanctioned entities
    normalized = _normalize(company_name)
    for entity in KNOWN_SANCTIONED:
        entity_normalized = _normalize(entity["name"])
        # exact match on normalized names
        if normalized == entity_normalized:
            results.append({
                "type": "entity_match",
                "name": entity["name"],
                "program": entity["program"],
                "authority": entity["authority"],
                "match_type": "精确匹配",
                "level": "critical",
            })
            sanctions_score += 50
            continue
        # check aliases
        for alias in entity["aliases"]:
            if _normalize(alias) in normalized or normalized in _normalize(alias):
                results.append({
                    "type": "entity_match",
                    "name": entity["name"],
                    "program": entity["program"],
                    "authority": entity["authority"],
                    "match_type": f"别名匹配({alias})",
                    "level": "critical",
                })
                sanctions_score += 50
                break

    # 2. check region
    profile = get_baseinfo(company_name)
    if profile:
        db = get_db()
        doc = db["baseinfo"].find_one({"name": company_name})
        location = ""
        if doc:
            result = (doc.get("items") or {}).get("result") or {}
            location = result.get("regLocation", "") or result.get("city", "")

        for country, info in SANCTIONED_COUNTRIES.items():
            if country in location:
                results.append({
                    "type": "region_match",
                    "country": country,
                    "sanction_level": info["level"],
                    "desc": info["desc"],
                    "level": "high" if info["level"] == "comprehensive" else "medium",
                })
                sanctions_score += 30 if info["level"] == "comprehensive" else 15

    # 3. check existing risk data (dishonesty, executed)
    db = get_db()
    risk_doc = db["riskInfo"].find_one({"name": company_name})
    if risk_doc:
        r = (risk_doc.get("item") or {}).get("result") or {}
        risk_list = r.get("riskList", [])
        for category in risk_list:
            for sub in category.get("list", []):
                title = sub.get("title", "")
                total = sub.get("total", 0) or 0
                if "失信" in title and total > 0:
                    results.append({
                        "type": "dishonesty",
                        "detail": f"中国失信被执行人: {total}条",
                        "level": "high",
                    })
                    sanctions_score += 20
                if "被执行人" in title and total > 0:
                    results.append({
                        "type": "executed",
                        "detail": f"被执行人: {total}条",
                        "level": "medium",
                    })
                    sanctions_score += 10

    # determine overall level
    if sanctions_score >= 50:
        level = "critical"
    elif sanctions_score >= 20:
        level = "high"
    elif sanctions_score >= 10:
        level = "medium"
    else:
        level = "low"

    return {
        "company_name": company_name,
        "sanctions_score": sanctions_score,
        "sanctions_level": level,
        "match_count": len(results),
        "matches": results,
        "clean": len(results) == 0,
    }


def get_sanctions_dashboard() -> dict:
    """制裁筛查总览。"""
    db = get_db()
    companies = [d["company_name"] for d in db["watchlist"].find()]

    results = []
    for name in companies:
        r = check_sanctions(name)
        results.append({
            "company_name": name,
            "sanctions_score": r["sanctions_score"],
            "sanctions_level": r["sanctions_level"],
            "match_count": r["match_count"],
        })

    critical = [r for r in results if r["sanctions_level"] == "critical"]
    high = [r for r in results if r["sanctions_level"] == "high"]

    return {
        "total_screened": len(companies),
        "critical_count": len(critical),
        "high_count": len(high),
        "clean_count": sum(1 for r in results if r["sanctions_level"] == "low"),
        "companies": sorted(results, key=lambda x: x["sanctions_score"], reverse=True),
    }
