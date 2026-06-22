"""批量导入供应商 — 解析 Excel 文件并入库。"""

import io
import uuid

import openpyxl

from app.core.logging import get_logger
from app.db.mongo import get_db
from app.db.postgres import get_cursor
from app.domains.knowledge.embedding import encode_single

logger = get_logger()

# Expected column headers (Chinese)
COLUMN_MAP = {
    "企业全称": "name",
    "公司名称": "name",
    "供应商名称": "name",
    "name": "name",
    "统一社会信用代码": "unified_code",
    "信用代码": "unified_code",
    "unified_code": "unified_code",
    "主营品类": "categories",
    "品类": "categories",
    "主营产品": "categories",
    "categories": "categories",
    "经营地域": "regions",
    "地域": "regions",
    "区域": "regions",
    "regions": "regions",
    "状态": "status",
    "status": "status",
}


def import_suppliers_from_excel(file_content: bytes, filename: str) -> dict:
    """解析 Excel 文件，批量导入供应商。

    预期表格格式：
    - 首行为表头，列名需包含在 COLUMN_MAP 中
    - 每行一个供应商
    - 品类/地域支持逗号分隔

    返回：{"imported": N, "skipped": N, "errors": [...]}
    """
    wb = openpyxl.load_workbook(io.BytesIO(file_content), data_only=True)
    db = get_db()

    imported = 0
    skipped = 0
    errors: list[str] = []

    for sheet_name in wb.sheetnames:
        ws = wb[sheet_name]
        rows = list(ws.iter_rows(values_only=True))
        if not rows:
            continue

        # Parse header
        headers = [str(h).strip() if h else "" for h in rows[0]]
        col_map: dict[str, int] = {}
        for idx, h in enumerate(headers):
            if h in COLUMN_MAP:
                col_map[COLUMN_MAP[h]] = idx

        if "name" not in col_map:
            errors.append(f"Sheet '{sheet_name}' 缺少企业全称列（支持：企业全称/公司名称/供应商名称/name）")
            continue

        # Parse data rows
        for row_idx, row in enumerate(rows[1:], 2):
            try:
                name = _cell(row, col_map.get("name"))
                if not name:
                    skipped += 1
                    continue

                sid = str(uuid.uuid4())
                categories = _cell_list(row, col_map.get("categories"))
                regions = _cell_list(row, col_map.get("regions"))
                unified_code = _cell(row, col_map.get("unified_code"))
                status = _cell(row, col_map.get("status")) or "prospective"

                # Check for duplicate by name
                existing = db["suppliers"].find_one({"name": name})
                if existing:
                    db["suppliers"].update_one(
                        {"name": name},
                        {"$set": {
                            "categories": categories,
                            "regions": regions,
                            "unified_code": unified_code,
                            "status": status,
                            "source": "excel_import",
                            "embedding_dirty": False,
                        }},
                    )
                    skipped += 1
                    continue

                # Insert into MongoDB
                db["suppliers"].insert_one({
                    "_id": sid,
                    "name": name,
                    "unified_code": unified_code or None,
                    "categories": categories,
                    "regions": regions,
                    "status": status,
                    "source": "excel_import",
                    "embedding_dirty": False,
                })

                # Build PG vector
                content_parts = [name] + categories + regions
                embedding = encode_single(" ".join(content_parts))
                vec_str = "[" + ",".join(str(v) for v in embedding) + "]"
                with get_cursor() as (conn, cur):
                    cur.execute(
                        """INSERT INTO supplier_profiles (id, supplier_name, content, embedding, metadata)
                           VALUES (%s, %s, %s, %s::vector, %s)""",
                        (sid, name, " ".join(content_parts), vec_str, "{}"),
                    )

                imported += 1

            except Exception as e:
                errors.append(f"Sheet '{sheet_name}' 第{row_idx}行: {str(e)}")
                logger.error("supplier_import_error", sheet=sheet_name, row=row_idx, error=str(e))

    logger.info("supplier_import_done", filename=filename, imported=imported, skipped=skipped)

    # Enrich with Tianyancha data
    if imported > 0:
        _enrich_imported(db)

    return {"imported": imported, "skipped": skipped, "errors": errors}


def import_from_tianyancha_search(
    keyword: str = "",
    industry: str = "",
    region: str = "",
    max_results: int = 50,
) -> dict:
    """从天眼查搜索企业并批量导入供应商库。

    自动翻页拉取搜索结果，逐个写入 MongoDB + PG 向量表。
    已存在的企业（同名）自动跳过。
    """
    from app.services.tianyancha_client import search_companies

    db = get_db()
    imported = 0
    skipped = 0
    errors: list[str] = []
    page = 1

    while imported < max_results:
        resp = search_companies(
            keyword=keyword,
            industry=industry,
            region=region,
            page_size=min(20, max_results - imported),
            page_num=page,
        )
        if resp is None:
            errors.append("天眼查 API 调用失败，请检查 TOKEN 配置")
            break

        items = resp.get("items", [])
        if not items:
            break

        for item in items:
            name = (item.get("name") or "").strip()
            if not name:
                skipped += 1
                continue

            # Skip if already exists
            if db["suppliers"].find_one({"name": name}):
                skipped += 1
                continue

            try:
                sid = str(uuid.uuid4())
                categories = [industry] if industry else []
                region_str = item.get("base", "") or item.get("regLocation", "")

                # PG vector
                content_parts = [name]
                if categories:
                    content_parts.extend(categories)
                if region_str:
                    content_parts.append(region_str)
                embedding = encode_single(" ".join(content_parts))
                vec_str = "[" + ",".join(str(v) for v in embedding) + "]"
                with get_cursor() as (conn, cur):
                    cur.execute(
                        """INSERT INTO supplier_profiles (id, supplier_name, content, embedding, metadata)
                           VALUES (%s, %s, %s, %s::vector, %s)""",
                        (sid, name, " ".join(content_parts), vec_str, "{}"),
                    )

                # MongoDB
                db["suppliers"].insert_one({
                    "_id": sid,
                    "name": name,
                    "unified_code": item.get("regNumber") or item.get("unifiedSocialCreditCode"),
                    "categories": categories,
                    "regions": [region] if region else [],
                    "status": "prospective",
                    "source": "tianyancha_search",
                    "embedding_dirty": False,
                })

                imported += 1
            except Exception as e:
                errors.append(f"{name}: {str(e)}")
                logger.error("tianyancha_import_error", company=name, error=str(e))

        total = resp.get("total", 0)
        if imported >= max_results or page * 20 >= total:
            break
        page += 1

    logger.info("tianyancha_import_done", keyword=keyword, industry=industry, imported=imported, skipped=skipped)

    if imported > 0:
        _enrich_imported(db)

    return {"imported": imported, "skipped": skipped, "errors": errors}


def _enrich_imported(db) -> None:
    """为新导入的供应商补充天眼查工商信息。"""
    from app.services.tianyancha_client import fetch_company
    # Find recently imported suppliers without unified_code
    cursor = db["suppliers"].find(
        {"source": {"$in": ["excel_import", "tianyancha_search"]}, "unified_code": None},
        {"name": 1},
    ).limit(50)
    names = [doc["name"] for doc in cursor]
    if names:
        logger.info("enriching_suppliers", count=len(names))
        for name in names:
            try:
                fetch_company(name)
            except Exception:
                pass
        # Now write enriched fields back
        for name in names:
            base = db["baseinfo"].find_one({"name": name})
            if base:
                items = base.get("items")
                result = None
                if isinstance(items, dict) and items.get("result"):
                    result = items["result"]
                elif isinstance(base.get("result"), dict):
                    result = base["result"]
                if result:
                    updates = {}
                    if result.get("regNumber"):
                        updates["unified_code"] = str(result["regNumber"])
                    if result.get("legalPersonName"):
                        updates["legal_person"] = result["legalPersonName"]
                    if result.get("regCapital"):
                        updates["registered_capital"] = result["regCapital"]
                    if result.get("estiblishTime"):
                        updates["establish_time"] = str(result["estiblishTime"])
                    if updates:
                        from datetime import datetime
                        updates["updated_at"] = datetime.now()
                        db["suppliers"].update_one({"name": name}, {"$set": updates})
        logger.info("enriching_done", updated=len(names))


def _cell(row, idx: int | None) -> str:
    if idx is None or idx >= len(row):
        return ""
    val = row[idx]
    return str(val).strip() if val is not None else ""


def _cell_list(row, idx: int | None) -> list[str]:
    raw = _cell(row, idx)
    if not raw:
        return []
    return [s.strip() for s in raw.replace("，", ",").split(",") if s.strip()]
