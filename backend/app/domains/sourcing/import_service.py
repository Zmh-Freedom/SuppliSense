"""批量导入供应商 — 解析 Excel 文件并入库。"""

import io
import uuid

# GB/T 4754-2017 制造业（门类C）31个大类代码 → 名称
_INDUSTRY_CODES: dict[str, str] = {
    "131": "谷物磨制", "132": "饲料加工", "133": "植物油加工", "134": "制糖业",
    "135": "屠宰及肉类加工", "136": "水产品加工", "137": "蔬菜菌类水果坚果加工", "139": "其他农副食品加工",
    "141": "焙烤食品", "142": "糖果巧克力及蜜饯", "143": "方便食品", "144": "乳制品",
    "145": "罐头食品", "146": "调味品及发酵制品", "149": "其他食品制造",
    "151": "酒的制造", "152": "饮料制造", "153": "精制茶加工",
    "161": "烟叶复烤", "162": "卷烟制造", "169": "其他烟草制品",
    "171": "棉纺织及印染", "172": "毛纺织及染整", "173": "麻纺织及染整",
    "174": "丝绢纺织及印染", "175": "化纤织造及印染", "176": "针织或钩针编织物",
    "177": "家用纺织制成品", "178": "产业用纺织制成品",
    "181": "机织服装", "182": "针织或钩针编织服装", "183": "服饰制造",
    "191": "皮革鞣制加工", "192": "皮革制品", "193": "毛皮鞣制及制品", "194": "羽毛加工及制品",
    "195": "制鞋业",
    "201": "木材加工", "202": "人造板制造", "203": "木质制品", "204": "竹藤棕草制品",
    "211": "家具制造", "212": "竹藤家具", "213": "金属家具", "214": "塑料家具", "219": "其他家具",
    "221": "纸浆制造", "222": "造纸", "223": "纸制品制造",
    "231": "印刷", "232": "装订及印刷相关服务",
    "241": "文教办公用品", "242": "乐器制造", "243": "工艺美术及礼仪用品",
    "244": "体育用品", "245": "玩具制造", "246": "游艺器材及娱乐用品",
    "251": "精炼石油产品", "252": "煤炭加工", "253": "核燃料加工",
    "261": "基础化学原料", "262": "肥料制造", "263": "农药制造",
    "264": "涂料油墨颜料及类似产品", "265": "合成材料", "266": "专用化学产品",
    "267": "炸药火工及焰火产品", "268": "日用化学产品",
    "271": "化学药品原料药", "272": "化学药品制剂", "273": "中药饮片加工", "274": "中成药生产",
    "275": "兽用药品", "276": "生物药品制品", "277": "卫生材料及医药用品",
    "278": "药用辅料及包装材料",
    "281": "纤维素纤维原料及纤维", "282": "合成纤维",
    "291": "橡胶制品", "292": "塑料制品",
    "301": "水泥石灰和石膏", "302": "石膏水泥制品及类似制品",
    "303": "砖瓦石材等建筑材料", "304": "玻璃制造", "305": "玻璃制品",
    "306": "玻璃纤维和玻璃纤维增强塑料", "307": "陶瓷制品",
    "308": "耐火材料制品", "309": "石墨及其他非金属矿物制品",
    "311": "炼铁", "312": "炼钢", "313": "钢压延加工", "314": "铁合金冶炼",
    "321": "常用有色金属冶炼", "322": "贵金属冶炼", "323": "稀有稀土金属冶炼",
    "324": "有色金属合金", "325": "有色金属压延加工",
    "331": "结构性金属制品", "332": "金属工具制造", "333": "集装箱及金属包装容器",
    "334": "金属丝绳及其制品", "335": "建筑安全用金属制品", "336": "金属表面处理及热处理",
    "337": "搪瓷制品", "338": "金属制日用品", "339": "铸造及其他金属制品",
    "341": "锅炉及原动设备", "342": "金属加工机械", "343": "物料搬运设备",
    "344": "泵阀门压缩机及类似机械", "345": "轴承齿轮和传动部件",
    "346": "烘炉风机包装等设备", "347": "文化办公用机械", "348": "通用零部件",
    "349": "其他通用设备",
    "351": "采矿冶金建筑专用设备", "352": "化工木材非金属加工专用设备",
    "353": "食品饮料烟草及饲料生产专用设备", "354": "印刷制药日化及日用品生产专用设备",
    "355": "纺织服装和皮革加工专用设备", "356": "电子和电工机械专用设备",
    "357": "农林牧渔专用机械", "358": "医疗仪器设备及器械", "359": "环保邮政社会公共服务专用设备",
    "361": "汽车整车", "362": "汽车用发动机制造", "363": "改装汽车", "364": "低速汽车",
    "365": "电车制造", "366": "汽车车身挂车", "367": "汽车零部件及配件",
    "371": "铁路运输设备", "372": "城市轨道交通设备", "373": "船舶及相关装置",
    "374": "航空装备", "375": "航天器及运载火箭", "376": "海洋工程装备",
    "377": "摩托车", "378": "自行车和残疾人座车", "379": "非公路休闲车及零配件",
    "381": "电机制造", "382": "输配电及控制设备", "383": "电线电缆光缆及电工器材",
    "384": "电池制造", "385": "家用电力器具", "386": "非电力家用器具",
    "387": "照明器具", "389": "其他电气机械及器材",
    "391": "计算机", "392": "通信设备", "393": "广播电视设备", "394": "雷达及配套设备",
    "395": "非专业视听设备", "396": "智能消费设备", "397": "电子器件",
    "398": "电子元件", "399": "其他电子设备",
    "401": "通用仪器仪表", "402": "专用仪器仪表", "403": "钟表与计时仪器",
    "404": "光学仪器", "405": "衡器", "409": "其他仪器仪表",
    "411": "日用杂品", "412": "煤制品", "413": "核辐射加工",
    "419": "其他未列明制造业",
    "421": "金属废料和碎屑加工处理", "422": "非金属废料和碎屑加工处理",
}

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
                categories = [_INDUSTRY_CODES.get(industry, industry)] if industry else []
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
