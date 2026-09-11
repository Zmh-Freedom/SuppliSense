"""
风险传染分析服务。

分析维度：
  1. 股权关联：通过天眼查 branch 接口查找分支机构/子公司
  2. 行业关联：同行业企业的风险传导
  3. 自定义依赖：用户定义的供应商-物料依赖关系

输出：风险传染图谱（节点 + 边），可用于前端可视化。
"""

from app.db.mongo import get_db
from app.domains.risk.repo_company import get_baseinfo


def _as_mapping(value: object) -> dict:
    """Normalize provider payload fragments before reading nested fields."""
    return value if isinstance(value, dict) else {}


def get_branches(company_name: str) -> list[dict]:
    """获取企业的分支机构/子公司列表。"""
    db = get_db()
    doc = db["branch"].find_one({"name": company_name})
    if not doc:
        from app.services.tianyancha_client import fetch_branches
        fetch_branches(company_name)
        doc = db["branch"].find_one({"name": company_name})

    if not doc:
        return []

    items = _as_mapping(doc.get("items"))
    nested_result = _as_mapping(items.get("result"))
    # Branch data stored as items.items or items.result.items depending on source
    branch_list = items.get("items") or nested_result.get("items", [])
    if not isinstance(branch_list, list):
        return []

    branches = []
    for b in branch_list:
        if not isinstance(b, dict):
            continue
        branches.append({
            "name": b.get("name", b.get("companyName", "")),
            "type": b.get("type", b.get("branchType", "分支机构")),
            "reg_status": b.get("regStatus", b.get("status", "")),
            "legal_person": b.get("legalPersonName", b.get("legalPerson", "")),
            "invest_ratio": b.get("investRatio", b.get("ratio", "")),
        })

    return branches


def get_supply_dependencies(company_name: str) -> list[dict]:
    """获取用户定义的供应商依赖关系。"""
    db = get_db()
    # dependencies where this company is the supplier
    deps = list(db["supply_deps"].find({
        "$or": [
            {"supplier": company_name},
            {"customer": company_name},
        ]
    }))
    return [{
        "supplier": d["supplier"],
        "customer": d["customer"],
        "material": d.get("material", ""),
        "importance": d.get("importance", "medium"),
    } for d in deps]


def add_dependency(supplier: str, customer: str, material: str = "", importance: str = "medium") -> dict:
    """添加供应链依赖关系。"""
    db = get_db()
    doc = {
        "supplier": supplier,
        "customer": customer,
        "material": material,
        "importance": importance,
    }
    db["supply_deps"].update_one(
        {"supplier": supplier, "customer": customer, "material": material},
        {"$set": doc},
        upsert=True,
    )
    return doc


def remove_dependency(supplier: str, customer: str, material: str = "") -> dict:
    """删除供应链依赖关系。"""
    db = get_db()
    db["supply_deps"].delete_one({
        "supplier": supplier,
        "customer": customer,
        "material": material,
    })
    return {"status": "removed"}


def analyze_contagion(company_name: str) -> dict:
    """分析风险传染：当某公司出现风险时，波及哪些关联方。"""
    db = get_db()

    # 1. get branches / subsidiaries
    branches = get_branches(company_name)

    # 3. get supply chain dependencies
    deps = get_supply_dependencies(company_name)

    # 4. assess risk of each related entity
    related = []

    for b in branches:
        b_name = b.get("name", "")
        if not b_name:
            continue
        in_watchlist = db["watchlist"].find_one({"company_name": b_name}) is not None
        related.append({
            "name": b_name,
            "relation": "branch",
            "relation_type": b.get("type", "分支机构"),
            "in_watchlist": in_watchlist,
        })

    for d in deps:
        if d["supplier"] == company_name:
            related_name = d["customer"]
            rel_type = "下游客户"
        else:
            related_name = d["supplier"]
            rel_type = "上游供应商"

        in_watchlist = db["watchlist"].find_one({"company_name": related_name}) is not None
        related.append({
            "name": related_name,
            "relation": "supply_chain",
            "relation_type": rel_type,
            "material": d.get("material", ""),
            "importance": d.get("importance", "medium"),
            "in_watchlist": in_watchlist,
        })

    # 5. find same-industry companies in watchlist
    profile = get_baseinfo(company_name)
    industry = ""
    if profile:
        # get industry from baseinfo
        base = db["baseinfo"].find_one({"name": company_name})
        if base:
            result = _as_mapping(_as_mapping(base.get("items")).get("result"))
            industry = result.get("industry", "")

    same_industry = []
    if industry:
        all_companies = [d["company_name"] for d in db["watchlist"].find()]
        for other in all_companies:
            if other == company_name:
                continue
            other_base = db["baseinfo"].find_one({"name": other})
            if other_base:
                other_result = _as_mapping(
                    _as_mapping(other_base.get("items")).get("result")
                )
                other_industry = other_result.get("industry", "")
                if other_industry == industry:
                    same_industry.append(other)

    for si in same_industry:
        # avoid duplicates
        if not any(r["name"] == si for r in related):
            related.append({
                "name": si,
                "relation": "same_industry",
                "relation_type": f"同行业({industry})",
                "in_watchlist": True,
            })

    # aggregate risk scores for related entities in watchlist
    high_risk_related = 0
    for r in related:
        if r["in_watchlist"]:
            snap = db["alert_snapshots"].find_one({"company_name": r["name"]}, sort=[("checked_at", -1)])
            if snap and snap.get("risk_score", 100) < 40:
                high_risk_related += 1

    return {
        "company_name": company_name,
        "related_count": len(related),
        "branch_count": len(branches),
        "dependency_count": len(deps),
        "same_industry_count": len(same_industry),
        "high_risk_related_count": high_risk_related,
        "related_entities": related,
    }


def get_graph_data(company_name: str) -> dict:
    """Return nodes+edges format for graph visualization."""
    data = analyze_contagion(company_name)
    db = get_db()

    # Center node
    nodes = []
    edges = []

    # Get center node risk score
    center_snap = db["alert_snapshots"].find_one(
        {"company_name": company_name}, sort=[("checked_at", -1)]
    )
    center_risk = center_snap.get("risk_score", 0) if center_snap else 0

    nodes.append({
        "id": company_name,
        "label": company_name[:20],
        "type": "center",
        "risk_score": center_risk,
    })

    for i, r in enumerate(data["related_entities"]):
        node_id = r["name"]
        # Avoid duplicate nodes
        if not any(n["id"] == node_id for n in nodes):
            risk_score = 0
            if r.get("in_watchlist"):
                snap = db["alert_snapshots"].find_one(
                    {"company_name": node_id}, sort=[("checked_at", -1)]
                )
                risk_score = snap.get("risk_score", 0) if snap else 0
            # Show distinguishing part: strip common parent prefix
            label = node_id
            if node_id.startswith(company_name):
                label = node_id[len(company_name):] or node_id
            nodes.append({
                "id": node_id,
                "label": label[:16],
                "type": r["relation"],
                "risk_score": risk_score,
            })

        edges.append({
            "id": f"e{i}-{company_name}-{node_id}",
            "source": company_name,
            "target": node_id,
            "relation": r["relation"],
            "label": r.get("relation_type", r["relation"]),
        })

    return {
        "company_name": company_name,
        "nodes": nodes,
        "edges": edges,
    }


def get_contagion_dashboard() -> dict:
    """全局风险传染看板。"""
    db = get_db()
    companies = [doc["company_name"] for doc in db["watchlist"].find()]

    all_deps = list(db["supply_deps"].find())
    all_branches_count = 0

    company_graph = []
    for name in companies:
        c = analyze_contagion(name)
        all_branches_count += c["branch_count"]
        company_graph.append({
            "company_name": name,
            "related_count": c["related_count"],
            "high_risk_related_count": c["high_risk_related_count"],
            "branch_count": c["branch_count"],
            "dependency_count": c["dependency_count"],
        })

    company_graph.sort(key=lambda x: x["high_risk_related_count"], reverse=True)

    return {
        "total_companies": len(companies),
        "total_dependencies": len(all_deps),
        "total_branches": all_branches_count,
        "high_contagion_risk": sum(1 for c in company_graph if c["high_risk_related_count"] > 0),
        "companies": company_graph,
    }
