from dotenv import load_dotenv

load_dotenv()

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.alert import router as alert_router
from app.api.chat import router as chat_router
from app.api.company import router as company_router
from app.api.financial import router as financial_router
from app.api.risk import router as risk_router
from app.api.sentiment import router as sentiment_router
from app.services.scheduler import start_scheduler, stop_scheduler


@asynccontextmanager
async def lifespan(app: FastAPI):
    start_scheduler()
    yield
    stop_scheduler()


app = FastAPI(title="Supplier Risk Analysis Agent", version="0.3.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(alert_router, prefix="/alert", tags=["alert"])
app.include_router(chat_router, prefix="/chat", tags=["chat"])
app.include_router(company_router, prefix="/company", tags=["company"])
app.include_router(financial_router, prefix="/financial", tags=["financial"])
app.include_router(risk_router, prefix="/risk", tags=["risk"])
app.include_router(sentiment_router, prefix="/sentiment", tags=["sentiment"])


@app.get("/health")
async def health_check():
    return {"status": "ok"}
