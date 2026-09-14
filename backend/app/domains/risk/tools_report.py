"""报告工具。"""
from langchain_core.tools import tool


@tool
def generate_report(company_name: str, report_type: str = "excel") -> dict:
    """生成企业风险评估报告（Excel 或 HTML 格式）。

    Args:
        company_name: 企业全称
        report_type: 报告格式，可选值: excel, html
    """
    from app.domains.risk.report_service import generate_excel, generate_html_report
    from app.tools.evidence import attach_tool_evidence

    if report_type == "html":
        content = generate_html_report(company_name)
        result = {"company_name": company_name, "format": "html", "length": len(content), "content": content}
    else:
        content = generate_excel(company_name)
        result = {"company_name": company_name, "format": "excel", "size_bytes": len(content), "message": "Excel 报告已生成"}
    return attach_tool_evidence(
        result,
        tool_name="generate_report",
        entity_id=f"entity:{company_name}",
        dimension="report",
        claim_fields=["format", "length", "size_bytes"],
    )


@tool
def manage_scheduled_report(action: str, company_names: list[str] | None = None, cron: str = "weekly", report_type: str = "excel") -> dict:
    """管理定时报告任务（创建/查看/删除）。

    Args:
        action: 操作类型，可选值: create(需确认), list, delete(需确认)
        company_names: 监控企业列表（create 时必填）
        cron: 定时表达式，如 weekly, daily（create 时使用）
        report_type: 报告格式，可选值: excel, html
    """
    from app.graphs.approval import needs_approval, request_approval

    if needs_approval("manage_scheduled_report", {"action": action, "company_names": company_names or []}):
        try:
            approved = request_approval("manage_scheduled_report", {"action": action, "company_names": company_names or []})
        except RuntimeError:
            return {"success": False, "error": "approval_context_required", "message": "定时报告写操作必须在支持人工审批的 Agent 会话中执行"}
        if not approved:
            return {"cancelled": True, "message": f"用户取消了定时报告{action}操作"}

    from app.domains.risk.scheduled_report import (
        create_scheduled_report,
        list_scheduled_reports,
        delete_scheduled_report,
    )

    if action == "create":
        if not company_names:
            return {"error": "创建定时报告需要指定企业列表"}
        return create_scheduled_report(company_names, cron, report_type)
    elif action == "list":
        return {"reports": list_scheduled_reports()}
    elif action == "delete":
        if not company_names:
            return {"error": "删除定时报告需要指定 task_id（通过 company_names 传入）"}
        return delete_scheduled_report(company_names[0])
    else:
        return {"error": f"未知操作: {action}，可选值: create, list, delete"}
