"""
情景模拟服务。

模拟供应商出现问题时的影响：
  1. 倒闭/破产 → 供应中断
  2. 重大诉讼 → 经营不稳定
  3. 质量事故 → 产品召回

输出：影响等级 + 受影响方 + 应对建议。
"""

from app.db.mongo import get_db
from app.repositories.company_repo import get_baseinfo
from app.services.contagion import get_supply_dependencies, analyze_contagion


def simulate(company_name: str, scenario: str = "bankruptcy") -> dict:
    """模拟供应商在特定情景下的影响。

    scenarios:
      - bankruptcy: 供应商倒闭
      - lawsuit: 重大法律诉讼
      - disruption: 供应链中断
      - quality: 质量事故
    """
    db = get_db()

    # 1. get company profile
    profile = get_baseinfo(company_name)
    if not profile:
        return {"error": "未找到企业数据"}

    # 2. get current risk
    snap = db["alert_snapshots"].find_one(
        {"company_name": company_name}, sort=[("checked_at", -1)]
    )
    risk_score = snap.get("risk_score", 50) if snap else 50
    risk_level = snap.get("risk_level", "未知") if snap else "未知"

    # 3. get dependencies
    deps = get_supply_dependencies(company_name)
    contagion = analyze_contagion(company_name)

    # 4. calculate impact
    dependent_count = len(deps)
    branch_count = contagion.get("branch_count", 0)
    related_count = contagion.get("related_count", 0)

    # impact scoring
    impact_score = 0
    impact_factors: list[dict] = []

    # factor 1: dependency breadth
    if dependent_count >= 5:
        impact_score += 30
        impact_factors.append({"factor": "供应链依赖广度", "detail": f"{dependent_count}个依赖关系", "score": 30, "level": "high"})
    elif dependent_count >= 2:
        impact_score += 15
        impact_factors.append({"factor": "供应链依赖广度", "detail": f"{dependent_count}个依赖关系", "score": 15, "level": "medium"})
    elif dependent_count > 0:
        impact_score += 5
        impact_factors.append({"factor": "供应链依赖广度", "detail": f"{dependent_count}个依赖关系", "score": 5, "level": "low"})
    else:
        impact_factors.append({"factor": "供应链依赖广度", "detail": "无已知依赖", "score": 0, "level": "low"})

    # factor 2: organizational complexity (branches)
    if branch_count >= 10:
        impact_score += 20
        impact_factors.append({"factor": "组织复杂度", "detail": f"{branch_count}个分支机构", "score": 20, "level": "high"})
    elif branch_count >= 3:
        impact_score += 10
        impact_factors.append({"factor": "组织复杂度", "detail": f"{branch_count}个分支机构", "score": 10, "level": "medium"})
    else:
        impact_factors.append({"factor": "组织复杂度", "detail": "少或无分支机构", "score": 0, "level": "low"})

    # factor 3: current risk level
    if risk_score >= 80:
        impact_score += 30
        impact_factors.append({"factor": "当前风险水平", "detail": f"{risk_level} {risk_score}/100", "score": 30, "level": "high"})
    elif risk_score >= 60:
        impact_score += 20
        impact_factors.append({"factor": "当前风险水平", "detail": f"{risk_level} {risk_score}/100", "score": 20, "level": "medium"})
    elif risk_score >= 30:
        impact_score += 10
        impact_factors.append({"factor": "当前风险水平", "detail": f"{risk_level} {risk_score}/100", "score": 10, "level": "low"})
    else:
        impact_factors.append({"factor": "当前风险水平", "detail": f"{risk_level} {risk_score}/100", "score": 0, "level": "low"})

    # factor 4: scenario-specific
    scenario_modifiers = {
        "bankruptcy": {"name": "供应商倒闭", "multiplier": 1.5, "desc": "供应完全中断，需紧急寻找替代"},
        "lawsuit": {"name": "重大法律诉讼", "multiplier": 1.0, "desc": "经营受限，交付可能延迟"},
        "disruption": {"name": "供应链中断", "multiplier": 1.2, "desc": "短期断供，需备选方案"},
        "quality": {"name": "质量事故", "multiplier": 0.8, "desc": "产品召回，品牌受损"},
    }
    sc = scenario_modifiers.get(scenario, scenario_modifiers["bankruptcy"])
    impact_score = min(int(impact_score * sc["multiplier"]), 100)

    # impact level
    if impact_score >= 60:
        impact_level = "严重"
    elif impact_score >= 30:
        impact_level = "中等"
    else:
        impact_level = "轻微"

    # 5. affected parties
    affected = []
    for d in deps:
        if d["supplier"] == company_name:
            affected.append({"name": d["customer"], "role": "下游客户", "material": d.get("material", ""), "importance": d.get("importance", "medium")})
        else:
            affected.append({"name": d["supplier"], "role": "上游供应商", "material": d.get("material", ""), "importance": d.get("importance", "medium")})

    # 6. suggested actions
    actions: list[str] = []
    if impact_score >= 60:
        actions.append("🚨 立即启动应急采购流程")
        actions.append("📋 通知所有受影响的下游客户")
        actions.append("🔍 从替代清单中选取 Top 3 候选供应商")
        if dependent_count > 0:
            actions.append("📦 检查安全库存，评估可维持天数")
    elif impact_score >= 30:
        actions.append("⚠️ 关注事态发展，准备备选方案")
        actions.append("📊 评估替代供应商的供货能力")
    else:
        actions.append("✅ 当前影响可控，持续监控即可")
        actions.append("📝 建议完善供应链依赖数据，提高评估精度")

    return {
        "company_name": company_name,
        "scenario": sc["name"],
        "scenario_desc": sc["desc"],
        "impact_score": impact_score,
        "impact_level": impact_level,
        "risk_score": risk_score,
        "risk_level": risk_level,
        "dependent_count": dependent_count,
        "branch_count": branch_count,
        "related_count": related_count,
        "impact_factors": impact_factors,
        "affected_parties": affected[:10],
        "suggested_actions": actions,
    }
