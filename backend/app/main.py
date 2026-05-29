from dotenv import load_dotenv

load_dotenv()

from fastapi import FastAPI

from app.api.company import router as company_router
from app.api.financial import router as financial_router
from app.api.risk import router as risk_router

app = FastAPI(title="Supplier Risk Analysis Agent", version="0.1.0")

app.include_router(company_router, prefix="/company", tags=["company"])
app.include_router(financial_router, prefix="/financial", tags=["financial"])
app.include_router(risk_router, prefix="/risk", tags=["risk"])


@app.get("/health")
async def health_check():
    return {"status": "ok"}
