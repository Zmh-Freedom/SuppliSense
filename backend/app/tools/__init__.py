"""LangGraph 工具定义 — 从各领域导入，统一注册。"""

from app.domains.risk.tools_search import search_company
from app.domains.risk.tools_risk import assess_risk, predict_risk, macro_risk, scenario_simulate
from app.domains.risk.tools_analysis import (
    esg_assessment,
    contagion_analysis,
    sentiment_analysis,
    check_sanctions,
    find_alternatives,
    compare_companies,
    analyze_trend,
    query_financials,
)
from app.domains.risk.tools_report import generate_report, manage_scheduled_report
from app.domains.alert.tools import check_alert, get_watchlist, add_to_watchlist, remove_from_watchlist, analyze_watchlist_trend
from app.domains.knowledge.tools import knowledge_search
from app.domains.sourcing.tools import (
    create_sourcing_request,
    search_suppliers,
    select_sourcing_result,
    expand_supplier_library,
)

TOOLS_LIST = [
    search_company,
    assess_risk,
    check_alert,
    get_watchlist,
    analyze_watchlist_trend,
    add_to_watchlist,
    remove_from_watchlist,
    esg_assessment,
    contagion_analysis,
    sentiment_analysis,
    predict_risk,
    macro_risk,
    find_alternatives,
    scenario_simulate,
    check_sanctions,
    knowledge_search,
    generate_report,
    analyze_trend,
    compare_companies,
    query_financials,
    manage_scheduled_report,
    create_sourcing_request,
    search_suppliers,
    select_sourcing_result,
    expand_supplier_library,
]
