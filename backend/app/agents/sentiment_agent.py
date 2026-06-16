"""
Sentiment Analysis Agent: Specialized in news and public opinion analysis.
"""

import asyncio
from typing import Any

from app.agents import BaseAgent
from app.services.agent import TOOLS


class SentimentAnalysisAgent(BaseAgent):
    """Agent specialized in sentiment and news analysis."""

    def __init__(self):
        super().__init__(
            name="SentimentAnalysis",
            description="专注于新闻舆情、社交媒体、舆情趋势分析"
        )

    def can_handle(self, query: str) -> bool:
        """Check if query is about sentiment analysis."""
        sentiment_keywords = ["舆情", "新闻", "舆论", "情感", "负面", "正面", "媒体报道", "口碑"]
        return any(kw in query for kw in sentiment_keywords)

    async def analyze(self, query: str, context: dict[str, Any]) -> dict[str, Any]:
        """Perform sentiment analysis."""
        company_name = context.get("company_name")
        if not company_name:
            return {"error": "未指定公司名称"}

        results = {}

        # Step 1: Search company
        search_result = await self._call_tool("search_company", {"keyword": company_name})
        if search_result.get("count", 0) == 0:
            return {"error": f"未找到公司: {company_name}"}

        full_name = search_result["results"][0]["name"]
        results["company_info"] = search_result

        # Step 2: Analyze sentiment
        sentiment_result = await self._call_tool("sentiment_analysis", {"company_name": full_name})
        results["sentiment_analysis"] = sentiment_result

        # Step 3: Get recent alerts (related to news)
        alert_result = await self._call_tool("check_alert", {"company_name": full_name})
        results["recent_alerts"] = alert_result

        # Step 4: Generate summary
        summary = await self._generate_summary(query, results)
        results["summary"] = summary

        return results

    async def _call_tool(self, tool_name: str, args: dict) -> Any:
        """Call a tool function."""
        if tool_name not in TOOLS:
            raise ValueError(f"Unknown tool: {tool_name}")
        config = TOOLS[tool_name]
        return await asyncio.to_thread(config.callable, **args)

    async def _generate_summary(self, query: str, results: dict) -> str:
        """Generate sentiment analysis summary using LLM."""
        from app.services.agent import _get_client, MODEL

        sentiment = results.get("sentiment_analysis", {})
        sentiment_score = sentiment.get("sentiment_score", "N/A")
        negative_count = sentiment.get("negative_count", 0)
        positive_count = sentiment.get("positive_count", 0)
        summary_text = sentiment.get("summary", "N/A")
        key_concerns = sentiment.get("key_concerns", [])

        context = f"""
查询: {query}

舆情分析结果:
- 情感得分: {sentiment_score} (范围 -1 到 1，负值为负面)
- 负面新闻: {negative_count} 条
- 正面新闻: {positive_count} 条
- 舆情总结: {summary_text}
- 关键关注点: {key_concerns}

近期预警:
- {results.get('recent_alerts', {})}
"""

        messages = [
            {"role": "system", "content": "你是舆情分析专家。基于提供的数据，用简洁的中文总结舆情态势。重点关注负面信息和潜在风险，给出应对建议。200字以内。"},
            {"role": "user", "content": context},
        ]

        resp = await asyncio.to_thread(
            _get_client().chat.completions.create,
            model=MODEL,
            messages=messages,
            temperature=0.3,
        )

        return resp.choices[0].message.content or "无法生成总结"
