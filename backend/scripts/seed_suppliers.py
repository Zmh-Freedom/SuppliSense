"""
Seed typical supplier data into MongoDB.
Run: cd backend && python3 scripts/seed_suppliers.py
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.db.mongo import get_db
from datetime import datetime
import uuid

SUPPLIERS = [
    # ---- 安防设备 ----
    {
        "name": "海康威视数字技术股份有限公司",
        "categories": ["安防设备", "视频监控", "智能硬件"],
        "regions": ["浙江", "华东"],
        "scale": "大型",
        "description": "全球领先的安防产品及解决方案提供商，主营摄像头、NVR、门禁系统",
    },
    {
        "name": "浙江大华技术股份有限公司",
        "categories": ["安防设备", "视频监控", "智慧城市"],
        "regions": ["浙江", "华东"],
        "scale": "大型",
        "description": "安防监控设备龙头企业，主营IP摄像头、存储设备、智能交通",
    },
    {
        "name": "深圳英飞拓科技股份有限公司",
        "categories": ["安防设备", "智能安防", "人脸识别"],
        "regions": ["广东", "华南"],
        "scale": "中型",
        "description": "智能安防解决方案商，主营AI摄像头、门禁控制、周界报警",
    },
    {
        "name": "苏州科达科技股份有限公司",
        "categories": ["安防设备", "视频会议", "执法记录"],
        "regions": ["江苏", "华东"],
        "scale": "中型",
        "description": "视频应用解决方案商，主营安防摄像头、视频会议终端、执法记录仪",
    },

    # ---- 电子元器件 ----
    {
        "name": "深圳市汇顶科技股份有限公司",
        "categories": ["电子元器件", "芯片设计", "传感器"],
        "regions": ["广东", "华南"],
        "scale": "大型",
        "description": "人机交互芯片设计公司，主营触控芯片、指纹识别芯片、传感器",
    },
    {
        "name": "中芯国际集成电路制造有限公司",
        "categories": ["电子元器件", "晶圆代工", "半导体"],
        "regions": ["上海", "华东"],
        "scale": "大型",
        "description": "中国大陆规模最大的集成电路芯片制造企业",
    },
    {
        "name": "深圳市立创电子有限公司",
        "categories": ["电子元器件", "PCB", "连接器"],
        "regions": ["广东", "华南"],
        "scale": "中型",
        "description": "电子元器件一站式采购平台，主营PCB打样、元器件配单、连接器",
    },
    {
        "name": "东莞晶导微电子股份有限公司",
        "categories": ["电子元器件", "分立器件", "功率器件"],
        "regions": ["广东", "华南"],
        "scale": "中型",
        "description": "功率半导体器件制造商，主营MOSFET、二极管、整流桥",
    },

    # ---- 工业自动化 ----
    {
        "name": "汇川技术股份有限公司",
        "categories": ["工业自动化", "伺服系统", "PLC"],
        "regions": ["广东", "华南"],
        "scale": "大型",
        "description": "工业自动化控制龙头，主营伺服驱动器、PLC、变频器、机器人",
    },
    {
        "name": "深圳市英威腾电气股份有限公司",
        "categories": ["工业自动化", "变频器", "UPS"],
        "regions": ["广东", "华南"],
        "scale": "中型",
        "description": "工业自动化产品供应商，主营变频器、UPS电源、伺服系统",
    },
    {
        "name": "南京埃斯顿自动化股份有限公司",
        "categories": ["工业自动化", "机器人", "运动控制"],
        "regions": ["江苏", "华东"],
        "scale": "中型",
        "description": "国产工业机器人及运动控制系统领军企业",
    },

    # ---- 包装材料 ----
    {
        "name": "深圳市裕同包装科技股份有限公司",
        "categories": ["包装材料", "精品包装", "环保包装"],
        "regions": ["广东", "华南"],
        "scale": "大型",
        "description": "高端品牌包装整体解决方案提供商，主营彩盒、纸箱、说明书",
    },
    {
        "name": "山鹰国际控股股份公司",
        "categories": ["包装材料", "造纸", "瓦楞纸箱"],
        "regions": ["安徽", "华东"],
        "scale": "大型",
        "description": "造纸及包装行业龙头，主营瓦楞纸板、纸箱、再生纤维",
    },
    {
        "name": "江苏永冠新材料科技股份有限公司",
        "categories": ["包装材料", "胶粘制品", "保护膜"],
        "regions": ["江苏", "华东"],
        "scale": "中型",
        "description": "胶粘制品制造商，主营工业胶带、保护膜、包装胶带",
    },

    # ---- 物流服务 ----
    {
        "name": "顺丰速运有限公司",
        "categories": ["物流服务", "快递", "供应链"],
        "regions": ["广东", "全国"],
        "scale": "大型",
        "description": "国内领先的综合物流服务商，提供快递、冷链、供应链管理",
    },
    {
        "name": "德邦物流股份有限公司",
        "categories": ["物流服务", "零担快运", "大件物流"],
        "regions": ["上海", "全国"],
        "scale": "大型",
        "description": "以大件物流为核心的综合性物流供应商",
    },
    {
        "name": "上海荣庆物流有限公司",
        "categories": ["物流服务", "冷链物流", "仓储管理"],
        "regions": ["上海", "华东"],
        "scale": "中型",
        "description": "第三方物流服务商，主营仓储管理、冷链运输、城市配送",
    },

    # ---- MRO 工业品 ----
    {
        "name": "固安捷工业品有限公司",
        "categories": ["MRO工业品", "劳保用品", "五金工具"],
        "regions": ["上海", "全国"],
        "scale": "大型",
        "description": "MRO工业品一站式采购平台，主营劳保、工具、清洁、安防",
    },
    {
        "name": "西域智慧供应链有限公司",
        "categories": ["MRO工业品", "供应链管理", "五金机电"],
        "regions": ["上海", "全国"],
        "scale": "中型",
        "description": "数字化的MRO工业品采购及供应链服务平台",
    },
]


def seed():
    db = get_db()

    for s in SUPPLIERS:
        name = s["name"]
        sid = str(uuid.uuid4())

        # Upsert into MongoDB suppliers
        existing = db["suppliers"].find_one({"name": name})
        if existing:
            db["suppliers"].update_one(
                {"name": name},
                {"$set": {
                    "categories": s["categories"],
                    "regions": s["regions"],
                    "scale": s["scale"],
                    "description": s.get("description", ""),
                    "updated_at": datetime.now(),
                }}
            )
            print(f"  [updated] {name}")
        else:
            db["suppliers"].insert_one({
                "_id": sid,
                "name": name,
                "categories": s["categories"],
                "regions": s["regions"],
                "scale": s["scale"],
                "description": s.get("description", ""),
                "status": "prospective",
                "source": "seed",
                "created_at": datetime.now(),
            })
            print(f"  [inserted] {name}")

    print(f"\nDone. {len(SUPPLIERS)} suppliers seeded.")


if __name__ == "__main__":
    seed()
