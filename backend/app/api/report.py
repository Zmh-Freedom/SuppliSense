from fastapi import APIRouter, HTTPException
from fastapi.responses import HTMLResponse, StreamingResponse
from io import BytesIO

from app.services.report_service import generate_excel, generate_html_report

router = APIRouter()


@router.get("/excel/{company_name}")
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
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{filename}"},
    )


@router.get("/html/{company_name}")
async def export_html(company_name: str):
    try:
        html = generate_html_report(company_name)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"生成报告失败: {e}")

    return HTMLResponse(content=html)
