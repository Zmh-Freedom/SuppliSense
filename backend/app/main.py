from dotenv import load_dotenv

load_dotenv()

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.alert import router as alert_router
from app.api.auth import router as auth_router
from app.api.chat import router as chat_router
from app.api.company import router as company_router
from app.api.financial import router as financial_router
from app.api.risk import router as risk_router
from app.api.sentiment import router as sentiment_router
from app.api.p2 import router as p2_router
from app.api.macro import router as macro_router
from app.api.scenario import router as scenario_router
from app.core.config import settings
from app.db.mongo import close_db, ensure_indexes
from app.services.scheduler import start_scheduler, stop_scheduler

logger = logging.getLogger(__name__)


def create_default_admin():
    """Create default admin user if no users exist."""
    from app.services.auth import create_user, list_users
    from app.schemas.user import UserCreate, UserRole

    users = list_users()
    if not users:
        try:
            create_user(UserCreate(
                username="admin",
                email="admin@example.com",
                password="admin123",
                role=UserRole.ADMIN,
            ))
            logger.info("Default admin user created (username: admin, password: admin123)")
        except ValueError:
            pass  # User already exists


@asynccontextmanager
async def lifespan(app: FastAPI):
    ensure_indexes()
    create_default_admin()
    start_scheduler()
    yield
    stop_scheduler()
    close_db()


app = FastAPI(
    title=settings.APP_NAME,
    version=settings.APP_VERSION,
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_methods=["*"],
    allow_headers=["*"],
    allow_credentials=True,
)

# Auth router (no prefix, already has /auth prefix)
app.include_router(auth_router)

# Business routers
app.include_router(alert_router, prefix="/alert", tags=["alert"])
app.include_router(chat_router, prefix="/chat", tags=["chat"])
app.include_router(company_router, prefix="/company", tags=["company"])
app.include_router(financial_router, prefix="/financial", tags=["financial"])
app.include_router(risk_router, prefix="/risk", tags=["risk"])
app.include_router(sentiment_router, prefix="/sentiment", tags=["sentiment"])
app.include_router(p2_router, prefix="/p2", tags=["p2"])
app.include_router(macro_router, prefix="/analysis", tags=["analysis"])
app.include_router(scenario_router, prefix="/analysis", tags=["analysis"])


@app.get("/health")
async def health_check():
    return {"status": "ok"}
