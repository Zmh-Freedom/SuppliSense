"""补全已有供应商的天眼查工商信息。

1. 先精确名称拉取
2. 失败则用短名称搜索，找到精确名称后重新拉取
3. 写入 supplier 文档

Run: cd backend && python3 scripts/enrich_suppliers.py
"""

import sys
import os
import time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.db.mongo import get_db
from app.services.tianyancha_client import fetch_company, _call

db = get_db()

cursor = db["suppliers"].find(
    {"$or": [
        {"unified_code": None},
        {"unified_code": {"$exists": False}},
        {"legal_person": None},
        {"legal_person": {"$exists": False}},
    ]},
    {"name": 1, "_id": 1},
)
entries = [(doc["_id"], doc["name"]) for doc in cursor]

# 手动映射——这些企业名称格式特殊，自动推测不出
_MANUAL_MAP = {
    "中芯国际集成电路制造有限公司": "中芯国际集成电路制造(上海)有限公司",
    "深圳市立创电子有限公司": "深圳市立创电子商务有限公司",
    "东莞晶导微电子股份有限公司": "山东晶导微电子股份有限公司",
    "江苏永冠新材料科技股份有限公司": "上海永冠众诚新材料科技(集团)股份有限公司",
    "固安捷工业品有限公司": "固安捷贸易有限公司",
}

if not entries:
    print("所有供应商已有完整工商信息")
    sys.exit(0)

print(f"需补全: {len(entries)} 家\n")

updated = 0
failed = 0

for i, (sid, name) in enumerate(entries, 1):
    print(f"[{i}/{len(entries)}] {name}")

    try:
        lookup_name = name
        base = None

        # Step 0: 手动映射
        if name in _MANUAL_MAP:
            candidate = _MANUAL_MAP[name]
            print(f"  → 手动映射: {candidate}")
            fetch_company(candidate)
            base = db["baseinfo"].find_one({"name": candidate})
            if base:
                lookup_name = candidate

        # Step 1: 精确匹配
        if not base:
            fetch_company(name)
            base = db["baseinfo"].find_one({"name": name})

        # Step 2: 尝试常见城市前缀（天眼查要求全称）
        if not base or not base.get("items") or not (base.get("items") or {}).get("result"):
            # 提取核心名称
            core = name
            for suffix in ["股份有限公司", "有限公司", "有限责任公司"]:
                core = core.replace(suffix, "")
            core = core.strip()

            # 常见城市前缀
            prefixes = [
                "杭州", "深圳", "广州", "上海", "北京", "苏州", "南京", "东莞",
                "武汉", "成都", "重庆", "天津", "西安", "长沙", "青岛", "厦门",
                "宁波", "无锡", "佛山", "合肥", "郑州", "济南", "沈阳", "大连",
                "浙江", "广东", "江苏", "山东", "福建",
            ]
            for prefix in prefixes:
                if core.startswith(prefix):
                    continue  # 已有前缀
                candidate = f"{prefix}{core}"
                if "有限公" in name:
                    candidate = f"{prefix}{core}有限公司"
                if "股份" in name:
                    candidate = f"{prefix}{core}股份有限公司"
                # 先轻量查询
                resp = _call("/services/open/ic/baseinfo/normal", candidate)
                if resp and resp.get("error_code") == 0 and resp.get("result"):
                    print(f"  → 匹配: {candidate}")
                    fetch_company(candidate)
                    base = db["baseinfo"].find_one({"name": candidate})
                    if base and base.get("items"):
                        lookup_name = candidate
                        break
                time.sleep(0.3)  # 避免限流

        if not base or not base.get("items"):
            print("  ❌ 天眼查无此企业")
            failed += 1
            continue

        # 天眼查两种返回格式：1) items.result 嵌套  2) result 直接平铺
        items = base.get("items")
        if isinstance(items, dict) and items.get("result"):
            result = items["result"]
        elif isinstance(base.get("result"), dict):
            result = base["result"]
        else:
            result = None
        if not result:
            print("  ❌ 返回数据为空")
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

        if lookup_name != name:
            updates["name"] = lookup_name  # 更正供应商名称

        if updates:
            from datetime import datetime, timezone
            updates["updated_at"] = datetime.now(timezone.utc)
            db["suppliers"].update_one({"_id": sid}, {"$set": updates})
            print(f"  ✅ {len(updates)} 个字段 | 法人:{result.get('legalPersonName','?')} | {result.get('regCapital','?')}")
            updated += 1
        else:
            print("  ⚠️ 无新字段")
            updated += 1

    except Exception as e:
        print(f"  ❌ {e}")
        failed += 1

print(f"\n完成: 更新 {updated} 家, 失败 {failed} 家")
