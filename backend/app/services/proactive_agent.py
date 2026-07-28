"""主动监控 Agent — 定时扫描监控清单，用 LLM 生成自然语言风险分析并推送。

与现有 notifier.py 的区别：
- notifier.py 基于规则（评分变化 >= 10 或等级升级）触发
- proactive_agent 使用 LLM 生成自然语言分析，解释变化原因和趋势

运行频率：默认每 2 小时一次（PROACTIVE_AGENT_CRON 环境变量配置）
"""

import json
import os

from app.core.logging import get_logger
from app.domains.alert.service import (
    detect_changes,
    get_latest_snapshot,
    get_watchlist,
)
from app.graphs import build_shared_llm

logger = get_logger()

ANALYSIS_PROMPT = """你是采购风险分析专家。请根据以下供应商的监控数据，生成简洁的风险变化分析。

要求：
1. 一句话总结该供应商的风险状态
2. 如果风险有变化，指出变化原因和趋势
3. 给出可操作建议（继续监控 / 需要关注 / 建议替换）
4. 控制每条分析在 100 字以内

供应商数据：
{context}

请输出 JSON 数组：
[{"company": "企业名", "status": "稳定|上升|下降", "analysis": "分析文本", "suggestion": "继续监控|需要关注|建议替换"}]"""


def _build_company_context(company_name: str) -> str:
    """构建单个公司的分析上下文。"""
    snapshot = get_latest_snapshot(company_name)
    if not snapshot:
        return f"企业：{company_name}\n无快照数据"

    changes = detect_changes(company_name)

    lines = [
        f"企业：{company_name}",
        f"风险评分：{snapshot.get('risk_score', 'N/A')}",
        f"风险等级：{snapshot.get('risk_level', 'N/A')}",
    ]

    if changes:
        lines.append(f"风险变化：{json.dumps(changes, ensure_ascii=False, default=str)}")

    return "\n".join(lines)


def run_proactive_analysis() -> dict:
    """执行一次主动分析：扫描监控清单，生成 LLM 分析，推送结果。

    Returns:
        {"analyzed": int, "results": list[dict], "pushed": bool}
    """
    companies = get_watchlist()
    if not companies:
        logger.info("proactive_agent: empty watchlist, skipping")
        return {"analyzed": 0, "results": [], "pushed": False}

    # 只分析有快照数据的公司（已在监控中）
    contexts: list[str] = []
    valid_companies: list[str] = []
    for name in companies:
        ctx = _build_company_context(name)
        if "无快照数据" not in ctx:
            contexts.append(ctx)
            valid_companies.append(name)

    if not valid_companies:
        logger.info("proactive_agent: no companies with snapshots")
        return {"analyzed": 0, "results": [], "pushed": False}

    # 批量分析（每批最多 5 家，避免 prompt 过长）
    batch_size = 5
    all_results: list[dict] = []

    for i in range(0, len(contexts), batch_size):
        batch = contexts[i : i + batch_size]
        context = "\n\n---\n\n".join(batch)

        try:
            llm = build_shared_llm()
            response = llm.invoke(
                ANALYSIS_PROMPT.format(context=context)
            )
            text = response.content.strip()

            # 解析 JSON
            results = _parse_analysis_response(text)
            all_results.extend(results)

        except Exception as e:
            logger.error(
                "proactive_agent_llm_error",
                batch_start=i,
                error=str(e)[:200],
            )
            # 降级：为这批公司生成规则化摘要
            for name in valid_companies[i : i + batch_size]:
                all_results.append({
                    "company": name,
                    "status": "unknown",
                    "analysis": f"LLM 分析失败: {str(e)[:100]}",
                    "suggestion": "需要关注",
                })

    # 推送结果
    pushed = False
    if all_results:
        pushed = _push_results(all_results)

    logger.info(
        "proactive_agent_completed",
        analyzed=len(all_results),
        pushed=pushed,
    )

    return {"analyzed": len(all_results), "results": all_results, "pushed": pushed}


def _parse_analysis_response(text: str) -> list[dict]:
    """解析 LLM 返回的 JSON 分析结果。"""
    # 尝试直接解析
    try:
        results = json.loads(text)
        if isinstance(results, list):
            return results
    except json.JSONDecodeError:
        pass

    # 尝试提取 ```json 围栏
    if "```" in text:
        parts = text.split("```")
        for part in parts:
            part = part.strip()
            if part.startswith("json"):
                part = part[4:]
            try:
                results = json.loads(part.strip())
                if isinstance(results, list):
                    return results
            except json.JSONDecodeError:
                continue

    logger.warning("proactive_agent_parse_failed", text=text[:200])
    return []


def _push_results(results: list[dict]) -> bool:
    """推送分析结果到飞书 + WebSocket。"""
    pushed_feishu = False
    pushed_ws = False

    # 推送到飞书
    try:
        lines = ["## 🤖 主动监控分析报告\n"]
        for r in results:
            emoji = {"稳定": "🟢", "上升": "🔴", "下降": "🟡"}.get(
                r.get("status", ""), "⚪"
            )
            lines.append(
                f"{emoji} **{r['company']}** — {r.get('status', 'unknown')}\n"
                f"> {r.get('analysis', '')}\n"
                f"> 建议：{r.get('suggestion', '需要关注')}\n"
            )
        _send_markdown("\n".join(lines))
        pushed_feishu = True
    except Exception as e:
        logger.error("proactive_agent_feishu_error", error=str(e)[:200])

    # 推送到 WebSocket
    try:
        from app.services.ws_manager import ws_manager

        ws_manager.broadcast("proactive_analysis", {
            "results": results,
            "count": len(results),
        })
        pushed_ws = True
    except Exception as e:
        logger.error("proactive_agent_ws_error", error=str(e)[:200])

    return pushed_feishu or pushed_ws


# ---- 飞书 markdown 推送辅助 ----

def _send_markdown(content: str) -> None:
    """发送飞书 markdown 消息。"""
    import hashlib
    import hmac
    import time

    import requests
    from app.core.config import settings

    webhook = settings.FEISHU_WEBHOOK_URL
    secret = settings.FEISHU_SECRET

    if not webhook:
        logger.warning("feishu_webhook_not_configured")
        return

    timestamp = str(int(time.time()))
    if secret:
        sign = hmac.new(
            secret.encode(), f"{timestamp}\n{secret}".encode(), hashlib.sha256
        ).digest()
        sign_encoded = sign.hex()
    else:
        sign_encoded = ""

    payload = {
        "timestamp": timestamp,
        "sign": sign_encoded,
        "msg_type": "interactive",
        "card": {
            "header": {
                "title": {"tag": "plain_text", "content": "SuppliSense 主动监控"},
                "template": "blue",
            },
            "elements": [
                {
                    "tag": "markdown",
                    "content": content,
                }
            ],
        },
    }

    try:
        resp = requests.post(webhook, json=payload, timeout=10)
        if resp.status_code == 200:
            logger.info("proactive_agent_feishu_sent")
        else:
            logger.warning(
                "proactive_agent_feishu_failed",
                status=resp.status_code,
                body=resp.text[:200],
            )
    except Exception as e:
        logger.error("proactive_agent_feishu_error", error=str(e)[:200])
