"""工具结果自动映射为图表数据。

在流式输出中，当特定工具返回结构化数据时，自动注入 chart_data SSE 事件，
前端据此渲染内嵌图表。
"""

import json
from typing import Any

from app.core.logging import get_logger

logger = get_logger()

# 需要自动生成图表的工具集合
_AUTO_CHART_TOOLS = frozenset({
    "assess_risk", "analyze_trend", "compare_companies",
    "sentiment_analysis", "query_financials",
})


def _try_auto_chart(tool_name: str, result: str | dict) -> dict[str, Any] | None:
    """根据工具名和返回结果，生成 chart_data dict。

    Args:
        tool_name: 工具名称
        result: 工具返回结果（JSON 字符串或 dict）

    Returns:
        chart_data dict 或 None（不支持的工具/数据不完整时）
    """
    if tool_name not in _AUTO_CHART_TOOLS:
        return None

    try:
        data = json.loads(result) if isinstance(result, str) else result
    except (json.JSONDecodeError, TypeError):
        return None

    if not isinstance(data, dict):
        return None

    # 工具返回 error 时不生成图表
    if "error" in data:
        return None

    try:
        if tool_name == "assess_risk":
            return _chart_assess_risk(data)
        if tool_name == "analyze_trend":
            return _chart_analyze_trend(data)
        if tool_name == "compare_companies":
            return _chart_compare_companies(data)
        if tool_name == "sentiment_analysis":
            return _chart_sentiment_analysis(data)
        if tool_name == "query_financials":
            return _chart_query_financials(data)
    except Exception:
        logger.warning("chart_generation_failed", tool=tool_name, exc_info=True)

    return None


def _chart_assess_risk(data: dict) -> dict[str, Any] | None:
    """assess_risk → radar 图表（四维度评分分布）。"""
    breakdown = data.get("score_breakdown")
    if not isinstance(breakdown, dict):
        return None

    dims = ["财务风险", "司法风险", "经营风险", "软指标"]
    radar_data = []
    for dim in dims:
        dim_info = breakdown.get(dim)
        if isinstance(dim_info, dict) and "归一化" in dim_info:
            score = dim_info["归一化"]
            if isinstance(score, (int, float)):
                radar_data.append({"dim": dim, "score": round(float(score), 1)})

    if len(radar_data) < 2:
        return None

    company = data.get("company_name", "")
    risk_score = data.get("risk_score", 0)
    risk_level = data.get("risk_level", "")
    title = f"{company} 风险维度分布" if company else "风险维度分布"
    if risk_score:
        title += f"（综合 {risk_score} 分 · {risk_level}）"

    return {
        "type": "radar",
        "title": title,
        "data": radar_data,
        "source": "tool",
        "tool": "assess_risk",
    }


def _chart_analyze_trend(data: dict) -> dict[str, Any] | None:
    """analyze_trend → line 图表（风险评分时间序列）。"""
    trend_data = data.get("data")
    if not isinstance(trend_data, list) or len(trend_data) < 2:
        return None

    line_data = []
    for item in trend_data:
        if isinstance(item, dict) and "date" in item and "risk_score" in item:
            line_data.append({
                "date": str(item["date"])[:7],  # 取 YYYY-MM
                "value": int(item["risk_score"]),
            })

    if len(line_data) < 2:
        return None

    company = data.get("company_name", "")
    trend_label = data.get("trend", "")
    title = f"{company} 风险趋势" if company else "风险趋势"
    if trend_label:
        title += f"（{trend_label}）"

    return {
        "type": "line",
        "title": title,
        "data": line_data,
        "source": "tool",
        "tool": "analyze_trend",
    }


def _chart_compare_companies(data: dict) -> dict[str, Any] | None:
    """compare_companies → bar 图表（企业风险评分对比）。"""
    companies = data.get("companies")
    if not isinstance(companies, list) or len(companies) < 2:
        return None

    bar_data = []
    for item in companies:
        if isinstance(item, dict):
            name = item.get("company_name", "")
            score = item.get("risk_score")
            if name and score is not None:
                bar_data.append({"name": name, "value": int(score)})

    if len(bar_data) < 2:
        return None

    return {
        "type": "bar",
        "title": "风险评分对比",
        "data": bar_data,
        "source": "tool",
        "tool": "compare_companies",
    }


def _chart_sentiment_analysis(data: dict) -> dict[str, Any] | None:
    """sentiment_analysis → pie 图表（正面/中性/负面分布）。"""
    pos = data.get("positive_count")
    neu = data.get("neutral_count")
    neg = data.get("negative_count")

    if not all(isinstance(v, (int, float)) for v in [pos, neu, neg]):
        return None
    if pos + neu + neg == 0:
        return None

    company = data.get("company_name", "")
    title = f"{company} 舆情分布" if company else "舆情分布"

    return {
        "type": "pie",
        "title": title,
        "data": [
            {"name": "正面", "value": int(pos)},
            {"name": "中性", "value": int(neu)},
            {"name": "负面", "value": int(neg)},
        ],
        "source": "tool",
        "tool": "sentiment_analysis",
    }


def _chart_query_financials(data: dict) -> dict[str, Any] | None:
    """query_financials → bar 图表（核心财务指标）。"""
    # 选取适合百分比展示的指标
    _METRICS = [
        ("revenue_growth", "营收增长"),
        ("net_profit_growth", "净利增长"),
        ("roe", "ROE"),
        ("net_profit_margin", "净利率"),
    ]

    bar_data = []
    for key, label in _METRICS:
        val = data.get(key)
        if isinstance(val, (int, float)) and val != 0:
            bar_data.append({"name": label, "value": round(float(val) * 100, 1)})

    if len(bar_data) < 2:
        return None

    company = data.get("company_name", "")
    title = f"{company} 核心财务指标（%）" if company else "核心财务指标（%）"

    return {
        "type": "bar",
        "title": title,
        "data": bar_data,
        "source": "tool",
        "tool": "query_financials",
    }
