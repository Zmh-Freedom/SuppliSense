from urllib.parse import quote
from io import BytesIO

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import HTMLResponse, StreamingResponse

from app.core.deps import get_current_user
from app.services.report_service import generate_excel, generate_html_report

router = APIRouter(dependencies=[Depends(get_current_user)])


@router.get(
    "/excel/{company_name}",
    summary="导出 Excel 风险评估报告",
    description="为指定企业生成 Excel 格式的风险评估报告文件，包含风险评分、财务数据和分析详情。",
    responses={
        400: {"description": "请求参数错误"},
        500: {"description": "生成报告失败"},
    },
)
async def export_excel(company_name: str):
    try:
        data = generate_excel(company_name)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"生成报告失败: {e}")

    filename = f"{company_name}_风险评估报告.xlsx"
    return StreamingResponse(
        BytesIO(data),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}"},
    )


@router.get(
    "/html/{company_name}",
    summary="生成 HTML 风险评估报告",
    description="为指定企业生成 HTML 格式的风险评估报告页面。",
    responses={
        400: {"description": "请求参数错误"},
        500: {"description": "生成报告失败"},
    },
)
async def export_html(company_name: str):
    try:
        html = generate_html_report(company_name)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"生成报告失败: {e}")

    return HTMLResponse(content=html)
