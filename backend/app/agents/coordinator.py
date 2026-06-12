"""
Agent Coordinator: Routes queries to appropriate specialized agents.
"""

import asyncio
import json
from typing import Any, AsyncGenerator

from app.agents.risk_agent import RiskAssessmentAgent
from app.agents.sentiment_agent import SentimentAnalysisAgent
from app.agents.compliance_agent import ComplianceCheckAgent
from app.services.agent import _get_client, MODEL


class AgentCoordinator:
    """Coordinates multiple specialized Agents."""

    def __init__(self):
        self.agents = {
            "risk": RiskAssessmentAgent(),
            "sentiment": SentimentAnalysisAgent(),
            "compliance": ComplianceCheckAgent(),
        }

    async def analyze_with_stream(
        self,
        query: str,
        context: dict[str, Any],
    ) -> AsyncGenerator[dict[str, Any], None]:
        """
        Analyze query with streaming output.

        Args:
            query: User's question
            context: Additional context

        Yields:
            Stream events
        """
        # Step 1: Determine which agent(s) to use
        yield {"type": "thinking", "message": "分析查询类型..."}

        agent_selection = await self._select_agents(query)
        selected_agents = agent_selection.get("agents", [])
        reasoning = agent_selection.get("reasoning", "")

        yield {"type": "agent_selection", "agents": selected_agents, "reasoning": reasoning}

        if not selected_agents:
            yield {"type": "error", "message": "无法确定分析类型，请使用通用对话模式"}
            return

        # Step 2: Execute selected agents
        all_results = {}
        for agent_name in selected_agents:
            if agent_name not in self.agents:
                continue

            agent = self.agents[agent_name]
            yield {"type": "agent_start", "agent": agent_name, "description": agent.description}

            try:
                result = await agent.analyze(query, context)
                all_results[agent_name] = result
                yield {"type": "agent_complete", "agent": agent_name, "result_summary": self._summarize_result(result)}
            except Exception as e:
                yield {"type": "agent_error", "agent": agent_name, "error": str(e)}
                all_results[agent_name] = {"error": str(e)}

        # Step 3: Generate final summary
        yield {"type": "thinking", "message": "整合分析结果..."}

        final_summary = await self._generate_final_summary(query, all_results)
        yield {"type": "final_answer", "answer": final_summary, "details": all_results}

    async def _select_agents(self, query: str) -> dict[str, Any]:
        """Use LLM to determine which agents to use."""
        agent_descriptions = "\n".join([
            f"- {name}: {agent.description}"
            for name, agent in self.agents.items()
        ])

        system_prompt = f"""你是一个任务分配专家。根据用户查询，选择合适的分析 Agent。

可用 Agent：
{agent_descriptions}

规则：
1. 分析查询内容，选择最相关的 1-2 个 Agent
2. 如果查询涉及多个领域，可以选择多个 Agent
3. 如果查询是简单问候或无关问题，返回空列表

输出格式（严格 JSON）：
{{"agents": ["agent1", "agent2"], "reasoning": "选择理由"}}
"""

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": query},
        ]

        resp = await asyncio.to_thread(
            _get_client().chat.completions.create,
            model=MODEL,
            messages=messages,
            temperature=0,
        )

        text = resp.choices[0].message.content.strip() or "{}"

        # Parse JSON
        try:
            # Extract JSON from text
            for part in text.split("```"):
                part = part.strip()
                if part.startswith("json"):
                    part = part[4:]
                try:
                    result = json.loads(part)
                    if "agents" in result:
                        return result
                except json.JSONDecodeError:
                    continue

            # Try the whole text
            result = json.loads(text)
            return result
        except json.JSONDecodeError:
            return {"agents": [], "reasoning": "无法解析"}

    def _summarize_result(self, result: dict[str, Any]) -> str:
        """Create a brief summary of agent result."""
        if "error" in result:
            return f"错误: {result['error']}"

        if "summary" in result:
            return result["summary"][:100] + "..."

        return "分析完成"

    async def _generate_final_summary(
        self,
        query: str,
        results: dict[str, dict[str, Any]],
    ) -> str:
        """Generate comprehensive summary from all agent results."""
        # Compile all summaries
        summaries = []
        for agent_name, result in results.items():
            if "summary" in result:
                summaries.append(f"## {self.agents[agent_name].description}\n{result['summary']}")

        combined = "\n\n".join(summaries)

        messages = [
            {"role": "system", "content": "你是综合分析专家。整合多个专业 Agent 的分析结果，生成一份完整的综合报告。重点突出关键发现和建议。300字以内。"},
            {"role": "user", "content": f"用户查询: {query}\n\n各 Agent 分析结果:\n{combined}"},
        ]

        resp = await asyncio.to_thread(
            _get_client().chat.completions.create,
            model=MODEL,
            messages=messages,
            temperature=0.3,
        )

        return resp.choices[0].message.content or "无法生成综合报告"


# Global coordinator instance
coordinator = AgentCoordinator()
