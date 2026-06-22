"""LangGraph 工具定义 — 按领域拆分，统一注册。"""

from app.tools.search import search_company, tianyancha_query
from app.tools.risk import assess_risk, predict_risk, macro_risk, scenario_simulate
from app.tools.analysis import (
    esg_assessment,
    contagion_analysis,
    sentiment_analysis,
    check_sanctions,
    find_alternatives,
    compare_companies,
    analyze_trend,
    query_financials,
)
from app.tools.alert_tools import check_alert, get_watchlist, add_to_watchlist, remove_from_watchlist
from app.tools.report_tools import generate_report, manage_scheduled_report
from app.tools.knowledge_tools import knowledge_search
from app.domains.sourcing.tools import (
    create_sourcing_request,
    search_suppliers,
    select_sourcing_result,
    expand_supplier_library,
)

TOOLS_LIST = [
    tianyancha_query,
    search_company,
    assess_risk,
    check_alert,
    get_watchlist,
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
