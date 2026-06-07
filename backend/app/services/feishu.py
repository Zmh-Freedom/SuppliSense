"""
飞书机器人推送服务。

配置：.env 中设置 FEISHU_WEBHOOK_URL 和 FEISHU_SECRET（签名校验用）
获取方式：飞书群 → 群设置 → 群机器人 → 添加自定义机器人 → 复制 webhook 地址
"""

import base64
import hashlib
import hmac
import json
import os
import time

import httpx

WEBHOOK_URL = os.getenv("FEISHU_WEBHOOK_URL", "")
SECRET = os.getenv("FEISHU_SECRET", "")


def _signed_url() -> str:
    if not SECRET:
        return WEBHOOK_URL
    ts = str(int(time.time()))
    sign = base64.b64encode(
        hmac.new(SECRET.encode(), f"{ts}\n{SECRET}".encode(), hashlib.sha256).digest()
    ).decode()
    sep = "&" if "?" in WEBHOOK_URL else "?"
    return f"{WEBHOOK_URL}{sep}timestamp={ts}&sign={sign}"


def _post(payload: dict) -> bool:
    if not WEBHOOK_URL:
        return False
    try:
        httpx.post(_signed_url(), json=payload, timeout=10)
        return True
    except Exception:
        return False


def send_text(text: str) -> bool:
    """发送纯文本消息"""
    return _post({"msg_type": "text", "content": {"text": text}})


def send_risk_report(report: str) -> bool:
    """发送 Markdown 格式的风险报告"""
    return _post({
        "msg_type": "interactive",
        "card": {
            "config": {"wide_screen_mode": True},
            "header": {
                "title": {"tag": "plain_text", "content": "📊 供应商风险日报"},
                "template": "wathet",
            },
            "elements": [
                {"tag": "markdown", "content": report},
                {"tag": "hr"},
                {"tag": "note", "elements": [{"tag": "plain_text", "content": "🤖 供应商风险分析 Agent · 自动推送"}]},
            ],
        },
    })


def send_alert_card(company_name: str, severity: str, changes: list[dict]) -> bool:
    """发送单条告警卡片"""
    color = "red" if severity == "critical" else "yellow"
    icon = "🔴" if severity == "critical" else "🟡"
    change_lines = "\n".join(
        f"{c['field']}：{c.get('old', '-')} → **{c.get('new', '-')}**" for c in changes[:10]
    )
    return _post({
        "msg_type": "interactive",
        "card": {
            "config": {"wide_screen_mode": True},
            "header": {
                "title": {"tag": "plain_text", "content": f"{icon} 风险告警"},
                "template": color,
            },
            "elements": [
                {"tag": "markdown", "content": f"**{company_name}** 风险发生变化\n\n{change_lines}"},
            ],
        },
    })


def send_daily_digest() -> None:
    """生成并发送每日简报"""
    from app.db.mongo import get_db

    db = get_db()
    companies = [doc["company_name"] for doc in db["watchlist"].find()]
    if not companies:
        send_text("📊 供应商风险日报\n\n暂无监控企业，请在系统中添加。")
        return

    lines = []
    alerts = list(db["alerts"].find().sort("created_at", -1).limit(10))

    # summary
    distribution = {"低风险": 0, "中风险": 0, "高风险": 0, "未知": 0}
    for name in companies:
        snap = db["alert_snapshots"].find_one({"company_name": name}, sort=[("checked_at", -1)])
        level = snap.get("risk_level", "未知") if snap else "未知"
        distribution[level] = distribution.get(level, 0) + 1

    lines.append(f"**监控 {len(companies)} 家供应商**")
    lines.append(
        f"低风险 {distribution['低风险']} · 中风险 {distribution['中风险']} · 高风险 {distribution['高风险']} · 未评估 {distribution['未知']}"
    )
    lines.append("")

    # alert summary
    if alerts:
        lines.append("**最新告警**")
        for a in alerts[:5]:
            ts = a["created_at"].strftime("%m-%d %H:%M") if hasattr(a["created_at"], "strftime") else str(a.get("created_at", ""))[:16]
            changes = a.get("changes", [])
            change_text = " · ".join(f"{c['field']}: {c['old']}→{c['new']}" for c in changes)
            lines.append(f"- {a['company_name']} {change_text} _{ts}_")
    else:
        lines.append("无新告警 ✅")

    # top risk companies
    lines.append("")
    lines.append("**高风险关注**")
    high_risk = []
    for name in companies:
        snap = db["alert_snapshots"].find_one({"company_name": name}, sort=[("checked_at", -1)])
        if snap and snap.get("risk_score", 0) > 30:
            high_risk.append((name, snap["risk_score"], snap["risk_level"]))
    high_risk.sort(key=lambda x: x[1], reverse=True)

    if high_risk:
        for name, score, level in high_risk[:8]:
            lines.append(f"- **{name}** {score}/100 {level}")
    else:
        lines.append("暂无中高风险企业 ✅")

    # sentiment summary
    lines.append("")
    lines.append("**舆情监控**")
    neg_companies = []
    for name in companies:
        sent = db["sentiment_results"].find_one({"company_name": name})
        if sent and sent.get("sentiment_score", 0) < -0.2:
            neg_companies.append((name, sent.get("sentiment_score", 0), sent.get("negative_count", 0)))
    neg_companies.sort(key=lambda x: x[1])

    if neg_companies:
        for name, score, neg_count in neg_companies[:5]:
            lines.append(f"- {name} 负面舆情 {neg_count}条（情感分 {score}）")
    else:
        lines.append("无负面舆情 ✅")

    send_risk_report("\n".join(lines))
