"""补全已有供应商的天眼查工商信息。

Run: cd backend && python3 scripts/enrich_suppliers.py
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.db.mongo import get_db
from app.services.tianyancha_client import fetch_company

db = get_db()

# 找出缺少 unified_code 的供应商
cursor = db["suppliers"].find(
    {"$or": [
        {"unified_code": None},
        {"unified_code": {"$exists": False}},
        {"legal_person": None},
        {"legal_person": {"$exists": False}},
    ]},
    {"name": 1, "_id": 0},
)
names = [doc["name"] for doc in cursor]

if not names:
    print("所有供应商已有完整工商信息")
    sys.exit(0)

print(f"需补全: {len(names)} 家")

updated = 0
failed = 0
for i, name in enumerate(names, 1):
    print(f"[{i}/{len(names)}] 正在拉取 {name}...", end=" ")
    try:
        fetch_company(name)
        base = db["baseinfo"].find_one({"name": name})
        if not base:
            print("❌ 天眼查无此企业")
            failed += 1
            continue
        result = (base.get("items") or {}).get("result") if isinstance(base.get("items"), dict) else None
        if not result:
            print("❌ 返回数据为空")
            failed += 1
            continue
        updates = {}
        if result.get("regNumber"):
            updates["unified_code"] = str(result["regNumber"])
        if result.get("legalPersonName"):
            updates["legal_person"] = result["legalPersonName"]
        if result.get("regCapital"):
            updates["registered_capital"] = result["regCapital"]
        if result.get("estiblishTime"):
            updates["establish_time"] = str(result["estiblishTime"])
        if result.get("regStatus"):
            updates["reg_status"] = result["regStatus"]
        if updates:
            from datetime import datetime, timezone
            updates["updated_at"] = datetime.now(timezone.utc)
            db["suppliers"].update_one({"name": name}, {"$set": updates})
        print(f"✅ {len(updates)} 个字段 ({result.get('legalPersonName','?')}, {result.get('regCapital','?')})")
        updated += 1
    except Exception as e:
        print(f"❌ {e}")
        failed += 1

print(f"\n完成: 更新 {updated} 家, 失败 {failed} 家")
