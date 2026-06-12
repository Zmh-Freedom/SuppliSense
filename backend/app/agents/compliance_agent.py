"""
Compliance Check Agent: Specialized in legal compliance and sanctions screening.
"""

import asyncio
from typing import Any

from app.agents import BaseAgent
from app.services.agent import TOOLS


class ComplianceCheckAgent(BaseAgent):
    """Agent specialized in compliance and sanctions screening."""

    def __init__(self):
        super().__init__(
            name="ComplianceCheck",
            description="专注于法律合规、制裁筛查、黑名单检查"
        )

    def can_handle(self, query: str) -> bool:
        """Check if query is about compliance."""
        compliance_keywords = ["合规", "制裁", "黑名单", "OFAC", "失信", "法律", "违规", "清单"]
        return any(kw in query for kw in compliance_keywords)

    async def analyze(self, query: str, context: dict[str, Any]) -> dict[str, Any]:
        """Perform compliance check."""
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

        # Step 2: Check sanctions (parallel execution)
        sanctions_task = self._call_tool("check_sanctions", {"company_name": full_name})
        risk_task = self._call_tool("assess_risk", {"company_name": full_name})

        sanctions_result, risk_result = await asyncio.gather(
            sanctions_task, risk_task, return_exceptions=True
        )

        results["sanctions_check"] = sanctions_result if not isinstance(sanctions_result, Exception) else {"error": str(sanctions_result)}
        results["risk_details"] = risk_result if not isinstance(risk_result, Exception) else {"error": str(risk_result)}

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
        """Generate compliance check summary using LLM."""
        from app.services.agent import _get_client, MODEL

        sanctions = results.get("sanctions_check", {})
        risk = results.get("risk_details", {})

        context = f"""
查询: {query}

合规检查结果:
- 制裁筛查: {sanctions}
- 风险详情: 风险评分 {risk.get('risk_score', 'N/A')}, 风险等级 {risk.get('risk_level', 'N/A')}
- 法律诉讼: {risk.get('risk_detail', {}).get('lawsuit_count', 0)} 条
- 被执行信息: {risk.get('risk_detail', {}).get('executed_count', 0)} 条
- 失信信息: {risk.get('risk_detail', {}).get('dishonesty_count', 0)} 条
"""

        messages = [
            {"role": "system", "content": "你是合规检查专家。基于提供的数据，用简洁的中文总结合规风险。重点标注制裁、失信、法律诉讼等高风险项，给出合规建议。200字以内。"},
            {"role": "user", "content": context},
        ]

        resp = await asyncio.to_thread(
            _get_client().chat.completions.create,
            model=MODEL,
            messages=messages,
            temperature=0.3,
        )

        return resp.choices[0].message.content or "无法生成总结"
