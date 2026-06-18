"""
Application configuration.
"""

import os

from dotenv import load_dotenv

load_dotenv()

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # Application
    APP_NAME: str = "Supplier Risk Analysis Agent"
    APP_VERSION: str = "0.3.0"
    DEBUG: bool = os.getenv("DEBUG", "false").lower() == "true"

    # JWT Auth
    SECRET_KEY: str = os.getenv("SECRET_KEY", "")
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = int(os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", "1440"))  # 24 hours
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

    # Redis
    REDIS_PASSWORD: str = os.getenv("REDIS_PASSWORD", "")
    REDIS_URL: str = os.getenv("REDIS_URL", "redis://localhost:6379")

    # Rate Limiting
    RATE_LIMIT_GLOBAL: str = "60/minute"
    RATE_LIMIT_AUTH: str = "5/minute"

    # CORS
    CORS_ORIGINS: list[str] = ["http://localhost:5173", "http://localhost:3000"]

    # Cookie
    COOKIE_SECURE: bool = os.getenv("COOKIE_SECURE", "true").lower() == "true"


settings = Settings()
