"""意图路由 — 根据用户消息自动选择最合适的执行模式。

路由策略：关键词匹配（零延迟）→ LLM 分类（兜底）→ 默认模式。
"""

from enum import Enum

from langchain_openai import ChatOpenAI

from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger()


class Intent(str, Enum):
    RISK = "langgraph-react"
    PLAN_EXECUTE = "langgraph-plan-execute"
    MULTI_AGENT = "langgraph-multi-agent"
    SOURCING = "langgraph-sourcing"  # 预留，寻源子图就绪后启用


# 关键词 → 意图映射（优先级：先匹配先胜）
_KEYWORD_RULES: list[tuple[list[str], Intent]] = [
    # 复杂规划类 → plan-execute
    (
        ["制定方案", "全面分析", "全面评估", "综合评估", "综合分析", "帮我规划", "分步骤", "制定计划", "深度分析", "深度评估"],
        Intent.PLAN_EXECUTE,
    ),
    # 多维度/多企业 → multi-agent
    (
        ["多角度", "多维度", "分别分析", "同时分析", "对比分析", "多方评估", "各维度", "多个方面"],
        Intent.MULTI_AGENT,
    ),
    # 寻源类（预留）
    (
        ["找供应商", "寻源", "智能寻源", "采购寻源", "替代供应商", "推荐供应商"],
        Intent.SOURCING,
    ),
]

# 意图分类 system prompt
_CLASSIFY_PROMPT = """你是用户意图分类器。根据用户消息，判断最合适的执行模式。

可用模式：
- react：单次风险评估、查企业信息、看预警、加监控等简单查询
- plan-execute：需要制定计划、分步骤执行的复杂任务（如"帮我全面评估..."、"制定方案..."）
- multi-agent：需要多个专业 Agent 协作的任务（如"从风险、舆情、合规多角度分析..."）
- sourcing：采购寻源相关（如"找供应商"、"推荐替代"、"寻源"）

只输出模式名称（react / plan-execute / multi-agent / sourcing），不要输出其他内容。"""


# 意图 → 模式映射
_CLASSIFY_TO_INTENT = {
    "react": Intent.RISK,
    "plan-execute": Intent.PLAN_EXECUTE,
    "multi-agent": Intent.MULTI_AGENT,
    "sourcing": Intent.SOURCING,
}

# 寻源子图已就绪，不再降级
_FALLBACK_FROM_SOURCING = Intent.SOURCING


class IntentRouter:
    """意图路由器。"""

    def __init__(self):
        self._llm: ChatOpenAI | None = None

    def _get_llm(self) -> ChatOpenAI:
        if self._llm is None:
            self._llm = ChatOpenAI(
                base_url=settings.LLM_BASE_URL,
                api_key=settings.LLM_API_KEY,
                model=settings.LLM_MODEL,
                temperature=0,
            )
        return self._llm

    def route(self, message: str) -> Intent:
        """根据消息内容判断执行模式。

        优先关键词匹配（零延迟），无命中时用 LLM 分类。
        """
        # 1. 关键词匹配
        for keywords, intent in _KEYWORD_RULES:
            if any(kw in message for kw in keywords):
                logger.info("intent_routed_by_keyword", intent=intent.value, message=message[:50])
                return self._resolve_sourcing(intent)

        # 2. LLM 分类兜底
        intent = self._classify_by_llm(message)
        logger.info("intent_routed_by_llm", intent=intent.value, message=message[:50])
        return self._resolve_sourcing(intent)

    def _classify_by_llm(self, message: str) -> Intent:
        """用 LLM 对消息进行意图分类。"""
        try:
            llm = self._get_llm()
            from langchain_core.messages import HumanMessage, SystemMessage

            response = llm.invoke([
                SystemMessage(content=_CLASSIFY_PROMPT),
                HumanMessage(content=message),
            ])
            result = response.content.strip().lower()

            intent = _CLASSIFY_TO_INTENT.get(result, Intent.RISK)
            return intent
        except Exception as e:
            logger.warning("intent_classification_failed", error=str(e))
            return Intent.RISK

    def _resolve_sourcing(self, intent: Intent) -> Intent:
        """寻源子图尚未实现时降级到 react。"""
        if intent == Intent.SOURCING:
            logger.info("sourcing_intent_downgraded", fallback=_FALLBACK_FROM_SOURCING.value)
            return _FALLBACK_FROM_SOURCING
        return intent


# 全局单例
router = IntentRouter()
