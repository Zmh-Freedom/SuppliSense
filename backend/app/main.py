from dotenv import load_dotenv

load_dotenv()

import os
import time
from contextlib import asynccontextmanager

from fastapi import APIRouter, FastAPI, Request, Response, WebSocket, WebSocketDisconnect
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import PlainTextResponse
from slowapi.errors import RateLimitExceeded
from slowapi import _rate_limit_exceeded_handler
from slowapi.middleware import SlowAPIMiddleware
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.api.alert import router as alert_router
from app.api.async_tasks import router as async_tasks_router
from app.api.auth import router as auth_router
from app.api.chat import router as chat_router
from app.api.company import router as company_router
from app.api.compare import router as compare_router
from app.api.financial import router as financial_router
from app.api.health import router as health_router
from app.api.knowledge import router as knowledge_router
from app.api.notifications import router as notifications_router
from app.api.risk import router as risk_router
from app.api.sentiment import router as sentiment_router
from app.api.p2 import router as p2_router
from app.api.report import router as report_router
from app.api.macro import router as macro_router
from app.api.scenario import router as scenario_router
from app.api.trend import router as trend_router
from app.api.upload import router as upload_router
from app.core.config import settings
from app.core.errors import (
    http_exception_handler,
    unhandled_exception_handler,
    validation_exception_handler,
)
from app.core.logging import setup_logging, get_logger
from app.core.rate_limit import limiter
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


def _validate_config():
    """启动前校验关键配置。"""
    errors = []
    if not settings.SECRET_KEY:
        errors.append("SECRET_KEY 未设置。生成命令：python -c \"import secrets; print(secrets.token_urlsafe(48))\"")
    if not settings.MONGO_PASSWORD:
        errors.append("MONGO_PASSWORD 未设置。")
    if settings.SECRET_KEY in (
        "your-secret-key-change-in-production-1234567890",
        "change-this-to-a-random-secret-in-production",
        "CHANGE_ME_生成一个64位随机字符串",
    ):
        errors.append("SECRET_KEY 仍为占位符，请设置真实的随机密钥。")
    if errors:
        for e in errors:
            logger.error("config_validation_failed", error=e)
        raise SystemExit(1)


def create_default_admin():
    """Create default admin user if no users exist."""
    import secrets
    import string

    from app.services.auth import create_user, list_users
    from app.schemas.user import UserCreate, UserRole

    users = list_users()
    if not users:
        password = os.getenv("INITIAL_ADMIN_PASSWORD")
        if not password:
            alphabet = string.ascii_letters + string.digits + "!@#$%&*"
            password = "".join(secrets.choice(alphabet) for _ in range(16))
            logger.warning(
                "default_admin_created_with_random_password",
                username="admin",
                password=password,
                hint="请保存此密码！设置 INITIAL_ADMIN_PASSWORD 环境变量可自定义。",
            )
        try:
            create_user(UserCreate(
                username="admin",
                email="admin@example.com",
                password=password,
                role=UserRole.ADMIN,
            ))
            logger.info("default_admin_created", username="admin")
        except ValueError:
            pass


@asynccontextmanager
async def lifespan(app: FastAPI):
    _validate_config()
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
    description="供应商风险分析智能体 — 企业风险评估、舆情监控、关系图谱、智能对话",
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_tags=[
        {"name": "auth", "description": "用户认证与权限管理"},
        {"name": "risk", "description": "企业风险评估（13维度评分体系）"},
        {"name": "company", "description": "企业信息查询"},
        {"name": "financial", "description": "财务指标分析（15项指标）"},
        {"name": "sentiment", "description": "舆情情感分析"},
        {"name": "alert", "description": "风险预警与监控"},
        {"name": "chat", "description": "AI 智能对话（ReAct/Plan-Execute/Multi-Agent）"},
        {"name": "p2", "description": "P2 高级功能 (ESG/风险传染/供应链依赖)"},
        {"name": "analysis", "description": "宏观风险与场景模拟分析"},
        {"name": "report", "description": "报告导出 (Excel/HTML)"},
        {"name": "upload", "description": "文件上传与解析"},
        {"name": "knowledge", "description": "知识库 RAG 检索"},
        {"name": "trend", "description": "风险评分趋势与告警频率统计"},
        {"name": "compare", "description": "多企业横向对比"},
        {"name": "notifications", "description": "用户通知中心"},
        {"name": "async", "description": "异步任务管理"},
    ],
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["Content-Type", "Authorization", "X-Requested-With"],
    allow_credentials=True,
)

# Rate limiting
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
app.add_middleware(SlowAPIMiddleware)

# Unified error handlers
app.add_exception_handler(StarletteHTTPException, http_exception_handler)
app.add_exception_handler(RequestValidationError, validation_exception_handler)
app.add_exception_handler(Exception, unhandled_exception_handler)


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


# API v1 master router
api_v1 = APIRouter(prefix="/api/v1")

api_v1.include_router(auth_router)
api_v1.include_router(async_tasks_router)
api_v1.include_router(alert_router, prefix="/alert", tags=["alert"])
api_v1.include_router(chat_router, prefix="/chat", tags=["chat"])
api_v1.include_router(company_router, prefix="/company", tags=["company"])
api_v1.include_router(financial_router, prefix="/financial", tags=["financial"])
api_v1.include_router(knowledge_router, prefix="/knowledge", tags=["knowledge"])
api_v1.include_router(risk_router, prefix="/risk", tags=["risk"])
api_v1.include_router(sentiment_router, prefix="/sentiment", tags=["sentiment"])
api_v1.include_router(p2_router, prefix="/p2", tags=["p2"])
api_v1.include_router(macro_router, prefix="/analysis", tags=["analysis"])
api_v1.include_router(scenario_router, prefix="/analysis", tags=["analysis"])
api_v1.include_router(upload_router, prefix="/upload", tags=["upload"])
api_v1.include_router(report_router, prefix="/report", tags=["report"])
api_v1.include_router(trend_router)
api_v1.include_router(compare_router)
api_v1.include_router(notifications_router)

app.include_router(api_v1)
app.include_router(health_router)


@app.get("/health")
async def health_check():
    return {"status": "ok"}


@app.get("/metrics")
async def metrics_endpoint():
    """Prometheus metrics endpoint."""
    metrics_text, content_type = get_metrics()
    return PlainTextResponse(metrics_text, media_type=content_type)


@app.websocket("/ws")
@limiter.exempt
async def websocket_endpoint(websocket: WebSocket):
    from app.services.ws_manager import ws_manager

    await websocket.accept()
    client_id = f"{id(websocket)}"
    await ws_manager.connect(websocket, client_id)

    try:
        while True:
            # Keep connection alive, handle client messages if needed
            data = await websocket.receive_text()
            # Client can send ping or subscription messages
            if data == "ping":
                await websocket.send_text('{"event":"pong","data":{}}')
    except WebSocketDisconnect:
        pass
    except Exception:
        pass
    finally:
        ws_manager.disconnect(client_id)
