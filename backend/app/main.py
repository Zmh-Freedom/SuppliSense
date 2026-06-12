from dotenv import load_dotenv

load_dotenv()

import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import PlainTextResponse

from app.api.alert import router as alert_router
from app.api.async_tasks import router as async_tasks_router
from app.api.auth import router as auth_router
from app.api.chat import router as chat_router
from app.api.company import router as company_router
from app.api.financial import router as financial_router
from app.api.knowledge import router as knowledge_router
from app.api.risk import router as risk_router
from app.api.sentiment import router as sentiment_router
from app.api.p2 import router as p2_router
from app.api.macro import router as macro_router
from app.api.scenario import router as scenario_router
from app.api.upload import router as upload_router
from app.core.config import settings
from app.core.logging import setup_logging, get_logger
from app.core.metrics import (
    HTTP_REQUESTS_TOTAL,
    HTTP_REQUEST_DURATION,
    get_metrics,
)
from app.core.sentry import init_sentry
from app.db.mongo import close_db, ensure_indexes
from app.services.scheduler import start_scheduler, stop_scheduler

# Setup logging
setup_logging()
logger = get_logger(__name__)

# Initialize Sentry
init_sentry()


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
            logger.info("default_admin_created", username="admin")
        except ValueError:
            pass  # User already exists


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("application_starting", version=settings.APP_VERSION)
    ensure_indexes()
    create_default_admin()
    start_scheduler()
    logger.info("application_started")
    yield
    logger.info("application_shutting_down")
    stop_scheduler()
    close_db()
    logger.info("application_stopped")


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


# Metrics middleware
@app.middleware("http")
async def metrics_middleware(request: Request, call_next):
    start_time = time.time()

    response = await call_next(request)

    duration = time.time() - start_time
    endpoint = request.url.path

    HTTP_REQUESTS_TOTAL.labels(
        method=request.method,
        endpoint=endpoint,
        status=response.status_code,
    ).inc()

    HTTP_REQUEST_DURATION.labels(
        method=request.method,
        endpoint=endpoint,
    ).observe(duration)

    return response


# Auth router (no prefix, already has /auth prefix)
app.include_router(auth_router)

# Async tasks router
app.include_router(async_tasks_router)

# Business routers
app.include_router(alert_router, prefix="/alert", tags=["alert"])
app.include_router(chat_router, prefix="/chat", tags=["chat"])
app.include_router(company_router, prefix="/company", tags=["company"])
app.include_router(financial_router, prefix="/financial", tags=["financial"])
app.include_router(knowledge_router, prefix="/knowledge", tags=["knowledge"])
app.include_router(risk_router, prefix="/risk", tags=["risk"])
app.include_router(sentiment_router, prefix="/sentiment", tags=["sentiment"])
app.include_router(p2_router, prefix="/p2", tags=["p2"])
app.include_router(macro_router, prefix="/analysis", tags=["analysis"])
app.include_router(scenario_router, prefix="/analysis", tags=["analysis"])
app.include_router(upload_router, prefix="/upload", tags=["upload"])


@app.get("/health")
async def health_check():
    return {"status": "ok"}


@app.get("/metrics")
async def metrics_endpoint():
    """Prometheus metrics endpoint."""
    metrics_text, content_type = get_metrics()
    return PlainTextResponse(metrics_text, media_type=content_type)
