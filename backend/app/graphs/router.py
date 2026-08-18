"""意图路由 — 根据用户消息自动选择最合适的执行模式。

路由策略：关键词匹配（零延迟）→ LLM 分类（兜底）→ 默认模式。
"""

from enum import Enum

from app.graphs.agent_supervisor.planner import is_composite_request
from app.graphs import build_shared_llm

from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger()


class Intent(str, Enum):
    RISK = "langgraph-react"
    PLAN_EXECUTE = "langgraph-plan-execute"
    MULTI_AGENT = "langgraph-multi-agent"
    PARALLEL = "langgraph-parallel"
    SOURCING = "langgraph-sourcing"
    SUPERVISOR = "langgraph-agent-supervisor"


# 关键词 → 意图映射（优先级：先匹配先胜）
_KEYWORD_RULES: list[tuple[list[str], Intent]] = [
    # 监控清单 / 趋势类 → react（单步工具即可完成）
    (
        ["监控清单", "风险变化", "变化趋势", "趋势分析", "本月风险", "风险趋势",
         "监控趋势", "清单趋势", "预警趋势", "预警变化"],
        Intent.RISK,
    ),
    # 复杂规划类 → plan-execute
    (
        ["制定方案", "全面分析", "全面评估", "综合评估", "综合分析", "帮我规划", "分步骤", "制定计划", "深度分析", "深度评估"],
        Intent.PLAN_EXECUTE,
    ),
    # 多维度/多企业 → multi-agent（串行轮询）
    (
        ["多角度", "多维度", "分别分析", "对比分析", "多方评估", "各维度", "多个方面"],
        Intent.MULTI_AGENT,
    ),
    # 并行多维度 → parallel（同时并行执行）
    (
        ["并行分析", "同时分析", "一起分析", "一起评估", "并行评估",
         "同时评估", "一起看看", "全面评估", "综合风险评估"],
        Intent.PARALLEL,
    ),
    # 寻源类
    (
        ["找供应商", "寻源", "智能寻源", "采购寻源", "替代供应商", "推荐供应商",
         "供应商推荐", "采购", "供应商", "推荐供应商", "有什么推荐", "找一家",
         "有没有什么", "有什么合适的", "帮我推荐", "帮我找", "推荐几"],
        Intent.SOURCING,
    ),
]

# 意图分类 system prompt
_CLASSIFY_PROMPT = """你是用户意图分类器。根据用户消息，判断最合适的执行模式。

可用模式：
- react：单次风险评估、查企业信息、看预警、加监控等简单查询
- plan-execute：需要制定计划、分步骤执行的复杂任务（如"帮我全面评估..."、"制定方案..."）
- multi-agent：需要多个专业 Agent 顺序协作的任务（如"从风险、舆情、合规多角度分析..."）
- parallel：需要多个 Agent 并行分析的任务（如"同时评估风险、舆情和合规"、"一起分析"）
- sourcing：采购寻源相关（如"找供应商"、"推荐替代"、"寻源"）
- supervisor：同时包含供应商寻源与风险/合规/舆情分析，需要组合 Agent 协作

只输出模式名称（react / plan-execute / multi-agent / parallel / sourcing / supervisor），不要输出其他内容。"""


# 意图 → 模式映射
_CLASSIFY_TO_INTENT = {
    "react": Intent.RISK,
    "plan-execute": Intent.PLAN_EXECUTE,
    "multi-agent": Intent.MULTI_AGENT,
    "parallel": Intent.PARALLEL,
    "sourcing": Intent.SOURCING,
    "supervisor": Intent.SUPERVISOR,
}

# 寻源子图已就绪，不再降级
_FALLBACK_FROM_SOURCING = Intent.SOURCING


class IntentRouter:
    """意图路由器。"""

    def __init__(self):
        self._llm = None

    def _get_llm(self):
        if self._llm is None:
            self._llm = build_shared_llm()
            # 覆盖为路由专用配置：短超时、不重试（分类失败有兜底）
            self._llm.max_retries = 0
            self._llm.request_timeout = 5
        return self._llm

    def route(
        self,
        message: str,
        execution_context: dict | None = None,
    ) -> Intent:
        """根据消息内容判断执行模式。

        企业目标由 ConversationState/Target Resolver 在调用前解析；路由只
        判断执行意图。优先关键词匹配（零延迟），语义无法由规则判断时才用 LLM。
        """
        del execution_context
        # 1. 组合寻源 + 分析任务（必须优先于通用寻源关键词）
        if is_composite_request(message):
            logger.info("intent_routed_by_composite_keyword", intent=Intent.SUPERVISOR.value, message=message[:50])
            return Intent.SUPERVISOR

        # 2. 关键词匹配
        for keywords, intent in _KEYWORD_RULES:
            if any(kw in message for kw in keywords):
                logger.info("intent_routed_by_keyword", intent=intent.value, message=message[:50])
                return self._resolve_sourcing(intent)

        # 3. LLM 分类兜底
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
        """保留寻源子图路由，其他意图原样返回。"""
        if intent == Intent.SOURCING:
            logger.info("sourcing_intent_downgraded", fallback=_FALLBACK_FROM_SOURCING.value)
            return _FALLBACK_FROM_SOURCING
        return intent


# 全局单例
router = IntentRouter()
