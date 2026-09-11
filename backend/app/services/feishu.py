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

from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger()

_tenant_access_token: str | None = None
_tenant_access_token_expires_at = 0.0


def _webhook_config() -> tuple[str, str]:
    """Read current settings so tests and runtime config changes are honored."""
    webhook = settings.FEISHU_WEBHOOK_URL or os.getenv("FEISHU_WEBHOOK_URL", "")
    secret = settings.FEISHU_SECRET or os.getenv("FEISHU_SECRET", "")
    return webhook, secret


def _signed_webhook_url(webhook_url: str, secret: str) -> str:
    """Build a Feishu webhook URL with the documented Base64 HMAC-SHA256 sign."""
    if not secret:
        return webhook_url
    ts = str(int(time.time()))
    sign = base64.b64encode(
        hmac.new(secret.encode(), f"{ts}\n{secret}".encode(), hashlib.sha256).digest()
    ).decode()
    sep = "&" if "?" in webhook_url else "?"
    return f"{webhook_url}{sep}timestamp={ts}&sign={sign}"


def _signed_url() -> str:
    webhook, secret = _webhook_config()
    return _signed_webhook_url(webhook, secret)


def _post(payload: dict) -> bool:
    webhook, _ = _webhook_config()
    if not webhook:
        return False
    try:
        r = httpx.post(_signed_url(), json=payload, timeout=10)
        if not r.is_success:
            return False
        body = r.json()
        return not isinstance(body, dict) or body.get("code", 0) == 0
    except Exception:
        return False


def _get_tenant_access_token() -> str | None:
    """Get an app tenant token for the Feishu user-message API."""
    global _tenant_access_token, _tenant_access_token_expires_at
    if not settings.FEISHU_USER_MESSAGE_ENABLED:
        return None
    now = time.time()
    if _tenant_access_token and now < _tenant_access_token_expires_at - 60:
        return _tenant_access_token
    if not settings.FEISHU_APP_ID or not settings.FEISHU_APP_SECRET:
        return None
    try:
        response = httpx.post(
            f"{settings.FEISHU_BITABLE_BASE_URL.rstrip('/')}/open-apis/auth/v3/tenant_access_token/internal",
            json={"app_id": settings.FEISHU_APP_ID, "app_secret": settings.FEISHU_APP_SECRET},
            headers={"Content-Type": "application/json; charset=utf-8"},
            timeout=settings.FEISHU_BITABLE_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        payload = response.json()
        data = (
            payload.get("data")
            if isinstance(payload, dict) and isinstance(payload.get("data"), dict)
            else payload
        )
        token = data.get("tenant_access_token") if isinstance(data, dict) else None
        if not isinstance(token, str) or not token:
            return None
        _tenant_access_token = token
        _tenant_access_token_expires_at = now + int(data.get("expire", 7200))
        return token
    except Exception as exc:
        logger.warning("feishu_tenant_token_failed", error=str(exc)[:200])
        return None


def send_user_message(open_id: str, text: str) -> bool:
    """Send a text message to one Feishu user through the self-built app."""
    if not open_id:
        return False
    token = _get_tenant_access_token()
    if not token:
        return False
    try:
        response = httpx.post(
            f"{settings.FEISHU_BITABLE_BASE_URL.rstrip('/')}/open-apis/im/v1/messages",
            params={"receive_id_type": "open_id"},
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json; charset=utf-8",
            },
            json={
                "receive_id": open_id,
                "msg_type": "text",
                "content": json.dumps({"text": text}, ensure_ascii=False),
            },
            timeout=settings.FEISHU_BITABLE_TIMEOUT_SECONDS,
        )
        if not response.is_success:
            return False
        payload = response.json()
        return isinstance(payload, dict) and payload.get("code", 0) == 0
    except Exception as exc:
        logger.warning("feishu_user_message_failed", open_id=open_id, error=str(exc)[:200])
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
    """按采购员和科室经理分别生成每日风险简报。"""
    from app.db.mongo import get_db
    from app.domains.alert.service import get_watchlist_targets
    from app.domains.alert.notifier import _create_and_deliver, get_target_recipients

    db = get_db()
    targets = get_watchlist_targets()
    if not targets:
        return
    groups: dict[tuple[str, str, str, str], list[dict]] = {}
    for target in targets:
        for recipient in get_target_recipients(target):
            key = (
                str(recipient.get("recipient_type") or ""),
                str(recipient.get("recipient_open_id") or ""),
                "" if recipient.get("recipient_type") == "manager" else str(recipient.get("purchaser_open_id") or ""),
                str(recipient.get("department_name") or ""),
            )
            groups.setdefault(key, []).append({"target": target, "recipient": recipient})

    for (recipient_type, _, _, department_name), items in groups.items():
        lines = ["📊 供应商风险日报"]
        if recipient_type == "manager":
            lines.append(f"所属科室：{department_name or '未标注'}")
        lines.append(f"负责范围：{len(items)} 家供应商")
        high_risk: list[tuple[str, object, object]] = []
        target_ids: list[str] = []
        for item in items:
            target = item["target"]
            target_id = target.get("monitor_target_id")
            if target_id:
                target_ids.append(str(target_id))
            query = {"monitor_target_id": target_id} if target_id else {"company_name": target.get("company_name", "")}
            snapshot = db["alert_snapshots"].find_one(query, sort=[("checked_at", -1)])
            if snapshot and snapshot.get("risk_score", 0) > 30:
                high_risk.append((target.get("company_name", ""), snapshot.get("risk_score"), snapshot.get("risk_level", "未知")))
        if high_risk:
            lines.append("重点关注：")
            for name, score, level in sorted(high_risk, key=lambda value: float(value[1] or 0), reverse=True)[:8]:
                lines.append(f"- {name} {score}/100 {level}")
        else:
            lines.append("重点关注：暂无中高风险企业 ✅")
        if target_ids:
            recent_alerts = list(db["alerts"].find({"monitor_target_id": {"$in": target_ids}}).sort("created_at", -1).limit(5))
            if recent_alerts:
                lines.append("最新变化：")
                for alert in recent_alerts:
                    changes = "；".join(f"{c.get('field', '')}: {c.get('old', '-')}→{c.get('new', '-')}" for c in alert.get("changes", []))
                    lines.append(f"- {alert.get('company_name', '')} {changes}")
        text = "\n".join(lines)
        representative = items[0]
        _create_and_deliver(
            db,
            representative["target"],
            representative["recipient"],
            notification_type="daily_digest",
            text=text,
            digest_scope="department" if recipient_type == "manager" else "purchaser",
            digest_target_ids=target_ids,
        )
