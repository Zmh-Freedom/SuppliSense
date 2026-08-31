"""商务风险 P0 API。"""

import asyncio

from fastapi import APIRouter, Depends, Query

from app.core.deps import get_current_user
from app.domains.risk.business_risk_service import assess_business_risk_p0

router = APIRouter(dependencies=[Depends(get_current_user)])


@router.get(
    "/business/{supplier_reference}",
    summary="评估供应商商务风险 P0",
    description="默认只基于飞书真实、校验通过的交易月度快照。仅 DEBUG=true 且启用演示开关时可使用合成快照，响应会明确标记为演示结果。",
)
async def business_risk_assess(
    supplier_reference: str,
    category_code: str | None = Query(None),
    purchasing_org_code: str | None = Query(None),
    base: str | None = Query(None),
) -> dict:
    return await asyncio.to_thread(
        assess_business_risk_p0,
        supplier_reference,
        category_code=category_code,
        purchasing_org_code=purchasing_org_code,
        base=base,
    )
