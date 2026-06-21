"""
Application configuration.
"""

import os

from dotenv import load_dotenv

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
    PG_POOL_MIN: int = int(os.getenv("PG_POOL_MIN", "2"))
    PG_POOL_MAX: int = int(os.getenv("PG_POOL_MAX", "10"))

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
