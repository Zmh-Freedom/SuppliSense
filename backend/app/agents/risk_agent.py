"""
Risk Assessment Agent: Specialized in financial and operational risk analysis.
"""

import asyncio
from typing import Any

from app.agents import BaseAgent
from app.services.agent import TOOLS


class RiskAssessmentAgent(BaseAgent):
    """Agent specialized in risk assessment and financial analysis."""

    def __init__(self):
        super().__init__(
            name="RiskAssessment",
            description="专注于财务风险、经营风险、诉讼风险评估"
        )

    def can_handle(self, query: str) -> bool:
        """Check if query is about risk assessment."""
        risk_keywords = ["风险", "财务", "财报", "诉讼", "被执行", "失信", "评估", "打分", "风险等级"]
        return any(kw in query for kw in risk_keywords)

    async def analyze(self, query: str, context: dict[str, Any]) -> dict[str, Any]:
        """Perform risk assessment analysis."""
        company_name = context.get("company_name")
        if not company_name:
            return {"error": "未指定公司名称"}

        results = {}

        # Step 1: Search company to get full name
        search_result = await self._call_tool("search_company", {"keyword": company_name})
        if search_result.get("count", 0) == 0:
            return {"error": f"未找到公司: {company_name}"}

        full_name = search_result["results"][0]["name"]
        results["company_info"] = search_result

        # Step 2: Assess risk (parallel execution)
        risk_task = self._call_tool("assess_risk", {"company_name": full_name})
        esg_task = self._call_tool("esg_assessment", {"company_name": full_name})
        predict_task = self._call_tool("predict_risk", {"company_name": full_name})

        # Execute in parallel
        risk_result, esg_result, predict_result = await asyncio.gather(
            risk_task, esg_task, predict_task, return_exceptions=True
        )

        results["risk_assessment"] = risk_result if not isinstance(risk_result, Exception) else {"error": str(risk_result)}
        results["esg_assessment"] = esg_result if not isinstance(esg_result, Exception) else {"error": str(esg_result)}
        results["risk_prediction"] = predict_result if not isinstance(predict_result, Exception) else {"error": str(predict_result)}

        # Step 3: Generate summary
        summary = await self._generate_summary(query, results)
        results["summary"] = summary

        return results

    async def _call_tool(self, tool_name: str, args: dict) -> Any:
        """Call a tool function."""
        if tool_name not in TOOLS:
            raise ValueError(f"Unknown tool: {tool_name}")
        fn = TOOLS[tool_name][0]
        return await asyncio.to_thread(fn, **args)

    async def _generate_summary(self, query: str, results: dict) -> str:
        """Generate analysis summary using LLM."""
        from app.services.agent import _get_client, MODEL

        # Prepare context for LLM
        context = f"""
查询: {query}

风险评估结果:
- 公司基本信息: {results.get('company_info', {})}
- 风险评分: {results.get('risk_assessment', {}).get('risk_score', 'N/A')}
- 风险等级: {results.get('risk_assessment', {}).get('risk_level', 'N/A')}
- ESG 评分: {results.get('esg_assessment', {})}
- 风险预测: {results.get('risk_prediction', {})}
"""

        messages = [
            {"role": "system", "content": "你是风险评估专家。基于提供的数据，用简洁的中文总结关键风险点。重点突出高风险项，给出专业建议。200字以内。"},
            {"role": "user", "content": context},
        ]

        resp = await asyncio.to_thread(
            _get_client().chat.completions.create,
            model=MODEL,
            messages=messages,
            temperature=0.3,
        )

        return resp.choices[0].message.content or "无法生成总结"
