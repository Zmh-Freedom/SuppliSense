"""
宏观风险叠加服务。

维度：
  1. 行业景气：PMI、行业营收均值（AkShare）
  2. 地区风险：省份信用环境、区域经济指标
  3. 政策风险：行业政策标签（环保限产、出口管制、产能过剩等）
"""

from datetime import datetime, timezone

from app.db.mongo import get_db
from app.repositories.company_repo import get_baseinfo

# ---- Policy risk tag rules ----

POLICY_RULES: list[dict] = [
    {
        "tag": "环保限产",
        "keywords": ["钢铁", "水泥", "玻璃", "焦化", "电解铝", "铸造", "化工", "石化", "煤"],
        "level": "high",
        "desc": "高耗能行业，受环保限产政策直接影响",
    },
    {
        "tag": "出口管制",
        "keywords": ["半导体", "芯片", "集成电路", "光刻", "EDA", "先进制造", "稀土", "无人机", "AI", "人工智能"],
        "level": "high",
        "desc": "涉及技术出口管制风险",
    },
    {
        "tag": "产能过剩",
        "keywords": ["钢铁", "水泥", "平板玻璃", "电解铝", "船舶", "光伏", "多晶硅", "锂电池"],
        "level": "medium",
        "desc": "行业存在产能过剩，面临去产能政策压力",
    },
    {
        "tag": "房地产依赖",
        "keywords": ["房地产", "建筑施工", "建材", "装修", "家具", "家纺"],
        "level": "medium",
        "desc": "与房地产行业关联度高，受行业下行影响",
    },
    {
        "tag": "数据安全",
        "keywords": ["互联网", "平台", "数据", "个人信息", "征信", "金融科技"],
        "level": "medium",
        "desc": "涉及数据安全和个人信息保护合规",
    },
    {
        "tag": "双碳政策",
        "keywords": ["电力", "发电", "石化", "化工", "建材", "钢铁", "有色", "造纸", "航空", "航运"],
        "level": "medium",
        "desc": "高碳排放行业，受双碳政策约束",
    },
    {
        "tag": "地方债务",
        "keywords": ["城投", "基建", "市政", "水务", "公交", "地铁", "高速公路"],
        "level": "medium",
        "desc": "与地方财政关联度高，受化债政策影响",
    },
]

# ---- Regional risk data ----

# province credit risk assessment (simplified model)
# based on: GDP rank, debt ratio, credit events frequency
REGIONAL_RISK: dict[str, dict] = {
    "广东": {"score": 5, "level": "低风险", "gdp_rank": 1, "label": "经济强省"},
    "江苏": {"score": 5, "level": "低风险", "gdp_rank": 2, "label": "经济强省"},
    "山东": {"score": 8, "level": "低风险", "gdp_rank": 3, "label": "工业大省"},
    "浙江": {"score": 5, "level": "低风险", "gdp_rank": 4, "label": "民营经济活跃"},
    "北京": {"score": 5, "level": "低风险", "gdp_rank": None, "label": "首都"},
    "上海": {"score": 5, "level": "低风险", "gdp_rank": None, "label": "金融中心"},
    "天津": {"score": 20, "level": "中风险", "gdp_rank": None, "label": "债务压力较大"},
    "重庆": {"score": 15, "level": "中风险", "gdp_rank": None, "label": "债务压力较大"},
    "贵州": {"score": 25, "level": "中风险", "gdp_rank": None, "label": "债务压力突出"},
    "云南": {"score": 22, "level": "中风险", "gdp_rank": None, "label": "债务压力突出"},
    "广西": {"score": 20, "level": "中风险", "gdp_rank": None, "label": "债务压力较大"},
    "甘肃": {"score": 25, "level": "中风险", "gdp_rank": None, "label": "经济较弱"},
    "青海": {"score": 28, "level": "高风险", "gdp_rank": None, "label": "经济薄弱"},
    "宁夏": {"score": 22, "level": "中风险", "gdp_rank": None, "label": "经济较弱"},
    "西藏": {"score": 25, "level": "中风险", "gdp_rank": None, "label": "经济薄弱"},
    "新疆": {"score": 20, "level": "中风险", "gdp_rank": None, "label": "偏远地区"},
    "内蒙古": {"score": 22, "level": "中风险", "gdp_rank": None, "label": "资源依赖"},
    "黑龙江": {"score": 22, "level": "中风险", "gdp_rank": None, "label": "人口外流"},
    "吉林": {"score": 22, "level": "中风险", "gdp_rank": None, "label": "人口外流"},
    "辽宁": {"score": 18, "level": "中风险", "gdp_rank": None, "label": "老工业基地"},
    "山西": {"score": 18, "level": "中风险", "gdp_rank": None, "label": "资源依赖"},
    "海南": {"score": 15, "level": "中风险", "gdp_rank": None, "label": "房地产依赖"},
}


def _get_province(company_name: str) -> str:
    """从企业注册地提取省份。"""
    db = get_db()
    doc = db["baseinfo"].find_one({"name": company_name})
    if not doc:
        return ""
    result = (doc.get("items") or {}).get("result") or {}
    location = result.get("regLocation", "")
    if not location:
        city = result.get("city", "")
        if city:
            location = city
    # match province name
    for prov in REGIONAL_RISK:
        if prov in location:
            return prov
    return ""


def _get_industry(company_name: str) -> str:
    """获取企业行业分类。"""
    db = get_db()
    doc = db["baseinfo"].find_one({"name": company_name})
    if not doc:
        return ""
    result = (doc.get("items") or {}).get("result") or {}
    return result.get("industry", "") or result.get("industryAll", "")


def get_policy_risks(company_name: str) -> list[dict]:
    """分析企业面临的政策风险标签。"""
    industry = _get_industry(company_name)

    # also check business scope
    db = get_db()
    doc = db["baseinfo"].find_one({"name": company_name})
    scope = ""
    if doc:
        result = (doc.get("items") or {}).get("result") or {}
        scope = result.get("businessScope", "")

    text = industry + " " + scope
    risks = []

    for rule in POLICY_RULES:
        for kw in rule["keywords"]:
            if kw in text:
                risks.append({
                    "tag": rule["tag"],
                    "level": rule["level"],
                    "desc": rule["desc"],
                })
                break

    return risks


def get_regional_risk(company_name: str) -> dict | None:
    """获取企业所在地区的风险评级。"""
    province = _get_province(company_name)
    if not province or province not in REGIONAL_RISK:
        return None
    info = REGIONAL_RISK[province]
    return {
        "province": province,
        "score": info["score"],
        "level": info["level"],
        "label": info["label"],
    }


def get_industry_pmi() -> dict | None:
    """获取最新 PMI 数据（AkShare）。"""
    try:
        import akshare as ak
        df = ak.macro_china_pmi()
        if df is not None and len(df) > 0:
            latest = df.iloc[-1]
            # handle column name variations
            date = str(latest.get("月份", latest.get("日期", "")))
            mfg = float(latest.get("制造业-指数", latest.get("制造业", 50)))
            non_mfg = float(latest.get("非制造业-指数", latest.get("非制造业", 50)))
            return {
                "date": date,
                "manufacturing_pmi": mfg,
                "non_manufacturing_pmi": non_mfg,
            }
    except Exception:
        pass
    return None


# Industry → PMI sensitivity mapping
INDUSTRY_PMI_MAP: dict[str, str] = {
    "钢铁": "manufacturing",
    "水泥": "manufacturing",
    "建材": "manufacturing",
    "化工": "manufacturing",
    "机械": "manufacturing",
    "汽车": "manufacturing",
    "电子": "manufacturing",
    "半导体": "manufacturing",
    "纺织": "manufacturing",
    "食品": "manufacturing",
    "医药": "manufacturing",
    "房地产": "construction",
    "建筑": "construction",
    "基建": "construction",
    "软件": "service",
    "互联网": "service",
    "金融": "service",
    "物流": "service",
    "零售": "service",
    "批发": "service",
}


def get_industry_risk(company_name: str) -> dict | None:
    """评估企业所在行业的景气风险。"""
    industry = _get_industry(company_name)
    pmi = get_industry_pmi()

    if not industry or not pmi:
        return None

    # determine industry type
    industry_type = "other"
    for kw, itype in INDUSTRY_PMI_MAP.items():
        if kw in industry:
            industry_type = itype
            break

    if industry_type == "manufacturing":
        pmi_val = pmi["manufacturing_pmi"]
        pmi_label = "制造业PMI"
    elif industry_type == "construction":
        pmi_val = pmi["non_manufacturing_pmi"]
        pmi_label = "非制造业PMI"
    else:
        pmi_val = (pmi["manufacturing_pmi"] + pmi["non_manufacturing_pmi"]) / 2
        pmi_label = "综合PMI"

    # risk assessment
    if pmi_val >= 52:
        pmi_risk_level = "低风险"
        pmi_risk_score = 5
    elif pmi_val >= 50:
        pmi_risk_level = "低风险"
        pmi_risk_score = 10
    elif pmi_val >= 48:
        pmi_risk_level = "中风险"
        pmi_risk_score = 20
    else:
        pmi_risk_level = "高风险"
        pmi_risk_score = 30

    return {
        "industry": industry,
        "industry_type": industry_type,
        "pmi_label": pmi_label,
        "pmi_value": pmi_val,
        "pmi_date": pmi.get("date", ""),
        "risk_level": pmi_risk_level,
        "risk_score": pmi_risk_score,
    }


def assess_macro_risk(company_name: str) -> dict:
    """综合宏观风险评估：政策 + 地区 + 行业。"""
    profile = get_baseinfo(company_name)

    # 1. policy risks
    policy_risks = get_policy_risks(company_name)
    policy_score = sum(
        20 if r["level"] == "high" else 10
        for r in policy_risks
    )

    # 2. regional risk
    regional = get_regional_risk(company_name)
    regional_score = regional["score"] if regional else 10

    # 3. industry risk
    industry = get_industry_risk(company_name)
    industry_score = industry["risk_score"] if industry else 10

    total = policy_score + regional_score + industry_score
    if total >= 50:
        level = "高风险"
    elif total >= 30:
        level = "中风险"
    else:
        level = "低风险"

    return {
        "company_name": company_name,
        "assessed_at": datetime.now(timezone.utc).isoformat(),
        "total_score": total,
        "total_level": level,
        "policy_risks": {
            "score": policy_score,
            "count": len(policy_risks),
            "tags": policy_risks,
        },
        "regional_risk": regional or {"province": "未知", "score": 0, "level": "未知"},
        "industry_risk": industry or {"industry": "未知", "risk_score": 0, "risk_level": "未知"},
    }
