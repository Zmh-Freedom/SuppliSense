"""
Application configuration.
"""

import hashlib
import os
from typing import Literal

from dotenv import load_dotenv
from pydantic import Field

load_dotenv()

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # Application
    APP_NAME: str = "SuppliSense"
    APP_VERSION: str = "0.3.0"
    DEBUG: bool = os.getenv("DEBUG", "false").lower() == "true"

    # JWT Auth
    SECRET_KEY: str = os.getenv("SECRET_KEY", "")
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = int(os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", "30"))
    REFRESH_TOKEN_EXPIRE_DAYS: int = int(os.getenv("REFRESH_TOKEN_EXPIRE_DAYS", "7"))

    # MongoDB
    MONGO_HOST: str = os.getenv("MONGO_HOST", "localhost")
    MONGO_PORT: int = int(os.getenv("MONGO_PORT", "27017"))
    MONGO_USER: str = os.getenv("MONGO_USER", "root")
    MONGO_PASSWORD: str = os.getenv("MONGO_PASSWORD", "")
    MONGO_AUTH_SOURCE: str = os.getenv("MONGO_AUTH_SOURCE", "admin")
    MONGO_DB: str = os.getenv("MONGO_DB", "tianyancha")

    # Tianyancha
    TIANYANCHA_TOKEN: str = os.getenv("TIANYANCHA_TOKEN", "")
    TIANYANCHA_BASE_URL: str = os.getenv("TIANYANCHA_BASE_URL", "https://open.api.tianyancha.com")

    # 联网供应商发现（只读候选发现，不自动入库）
    SUPPLIER_DISCOVERY_WEB_ENABLED: bool = os.getenv(
        "SUPPLIER_DISCOVERY_WEB_ENABLED", "true"
    ).lower() == "true"
    SUPPLIER_DISCOVERY_WEB_MAX_RESULTS: int = int(
        os.getenv("SUPPLIER_DISCOVERY_WEB_MAX_RESULTS", "10")
    )
    SUPPLIER_DISCOVERY_CONTACT_ENRICHMENT_MAX_CANDIDATES: int = int(
        os.getenv("SUPPLIER_DISCOVERY_CONTACT_ENRICHMENT_MAX_CANDIDATES", "5")
    )
    SUPPLIER_DISCOVERY_CONTACT_FETCH_TIMEOUT_SECONDS: float = float(
        os.getenv("SUPPLIER_DISCOVERY_CONTACT_FETCH_TIMEOUT_SECONDS", "8")
    )

    # LLM
    LLM_API_KEY: str = os.getenv("LLM_API_KEY", "")
    LLM_BASE_URL: str = os.getenv("LLM_BASE_URL", "https://api.deepseek.com/v1")
    LLM_MODEL: str = os.getenv("LLM_MODEL", "deepseek-chat")

    # Feishu
    FEISHU_WEBHOOK_URL: str = os.getenv("FEISHU_WEBHOOK_URL", "")
    FEISHU_SECRET: str = os.getenv("FEISHU_SECRET", "")

    # PostgreSQL
    PG_HOST: str = os.getenv("PG_HOST", "localhost")
    PG_PORT: int = int(os.getenv("PG_PORT", "5432"))
    PG_USER: str = os.getenv("PG_USER", "sra")
    PG_PASSWORD: str = os.getenv("PG_PASSWORD", "")
    PG_DB: str = os.getenv("PG_DB", "sra")
    PG_POOL_MIN: int = int(os.getenv("PG_POOL_MIN", "4"))
    PG_POOL_MAX: int = int(os.getenv("PG_POOL_MAX", "20"))

    # Agent Run V2 LangGraph checkpoints (managed with a dedicated psycopg3 connection)
    AGENT_RUN_V2_ENABLED: bool = os.getenv("AGENT_RUN_V2_ENABLED", "false").lower() == "true"
    AGENT_RUN_V2_ROLLOUT: Literal["shadow", "internal", "canary", "default"] = os.getenv(
        "AGENT_RUN_V2_ROLLOUT", "shadow"
    )
    AGENT_RUN_V2_ROLLOUT_STATE: Literal["active", "rollback_frozen"] = os.getenv(
        "AGENT_RUN_V2_ROLLOUT_STATE", "active"
    )
    AGENT_RUN_V2_CANARY_PERCENT: int = Field(
        default=int(os.getenv("AGENT_RUN_V2_CANARY_PERCENT", "0")), ge=0, le=100
    )
    AGENT_RUN_CHECKPOINT_SCHEMA: str = os.getenv("AGENT_RUN_CHECKPOINT_SCHEMA", "agent_checkpoint")

    # Transactional Outbox worker
    OUTBOX_WORKER_ENABLED: bool = True
    OUTBOX_POLL_SECONDS: int = Field(default=5, ge=1)
    OUTBOX_BATCH_SIZE: int = Field(default=50, ge=1)
    OUTBOX_MAX_ATTEMPTS: int = Field(default=8, ge=1)
    OUTBOX_LEASE_SECONDS: int = Field(default=60, ge=1)

    # PG 用户存储（默认启用，MongoDB 路径已废弃）
    USE_PG_USERS: bool = os.getenv("USE_PG_USERS", "true").lower() == "true"
    USE_PGVECTOR: bool = os.getenv("USE_PGVECTOR", "true").lower() == "true"

    # ChromaDB (deprecated, kept for migration period)
    CHROMA_PATH: str = os.getenv("CHROMA_PATH", "./data/chroma")

    # Redis
    REDIS_PASSWORD: str = os.getenv("REDIS_PASSWORD", "")
    REDIS_URL: str = os.getenv("REDIS_URL", "redis://localhost:6379")

    # Rate Limiting
    RATE_LIMIT_GLOBAL: str = "60/minute"
    RATE_LIMIT_AUTH: str = "5/minute"

    # CORS
    CORS_ORIGINS: list[str] = os.getenv("CORS_ORIGINS", "http://localhost:5173,http://localhost:3000").split(",")

    # Cookie
    COOKIE_SECURE: bool = os.getenv("COOKIE_SECURE", "true").lower() == "true"


settings = Settings()

AgentRunRoute = Literal["legacy", "shadow", "v2"]


def agent_run_v2_route(
    user_id: str,
    user_role: str,
    config: Settings = settings,
    *,
    rollout: str | None = None,
    rollout_state: str | None = None,
) -> AgentRunRoute:
    """Return the safe routing decision for a V2 request.

    The feature must be explicitly enabled. Shadow still allows execution and
    persistence by the caller, but its response remains on the legacy route.
    Canary assignment is deterministic so retries and reconnects do not move a
    user between routes.
    """
    active_rollout = rollout or config.AGENT_RUN_V2_ROLLOUT
    active_state = rollout_state or config.AGENT_RUN_V2_ROLLOUT_STATE
    if not config.AGENT_RUN_V2_ENABLED or active_state == "rollback_frozen":
        return "legacy"
    if active_rollout == "shadow":
        return "shadow"
    if active_rollout == "internal":
        return "v2" if user_role in {"admin", "analyst"} else "legacy"
    if active_rollout == "canary":
        bucket = int(hashlib.sha256(user_id.encode("utf-8")).hexdigest()[:8], 16) % 100
        return "v2" if bucket < config.AGENT_RUN_V2_CANARY_PERCENT else "legacy"
    return "v2"
