"""
Idempotent PostgreSQL schema initialization.
Creates extensions and tables if they do not exist.
"""

import logging

from app.db.postgres import get_cursor

logger = logging.getLogger(__name__)

DDL_STATEMENTS = [
    # Extensions
    "CREATE EXTENSION IF NOT EXISTS vector",
    "CREATE EXTENSION IF NOT EXISTS \"uuid-ossp\"",

    # Users
    """
    DO $$ BEGIN
        CREATE TYPE user_role AS ENUM ('admin', 'analyst', 'viewer');
    EXCEPTION WHEN duplicate_object THEN NULL;
    END $$
    """,
    """
    CREATE TABLE IF NOT EXISTS users (
        id UUID PRIMARY KEY,
        username VARCHAR(64) UNIQUE NOT NULL,
        email VARCHAR(255) UNIQUE NOT NULL,
        password_hash VARCHAR(128) NOT NULL,
        role user_role NOT NULL DEFAULT 'viewer',
        is_active BOOLEAN NOT NULL DEFAULT TRUE,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )
    """,

    # Documents (replaces ChromaDB)
    """
    CREATE TABLE IF NOT EXISTS documents (
        id VARCHAR(64) PRIMARY KEY,
        source VARCHAR(512) NOT NULL,
        content TEXT NOT NULL,
        embedding VECTOR(384) NOT NULL,
        metadata JSONB NOT NULL DEFAULT '{}',
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )
    """,

    # Audit logs
    """
    CREATE TABLE IF NOT EXISTS audit_logs (
        id UUID PRIMARY KEY,
        user_id UUID REFERENCES users(id) ON DELETE SET NULL,
        action VARCHAR(64) NOT NULL,
        resource_type VARCHAR(64),
        resource_id VARCHAR(255),
        details JSONB,
        ip_address INET,
        user_agent VARCHAR(512),
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )
    """,

    # Supplier profiles (vector search for sourcing)
    """
    CREATE TABLE IF NOT EXISTS supplier_profiles (
        id UUID PRIMARY KEY,
        supplier_name VARCHAR(255) NOT NULL,
        content TEXT NOT NULL,
        embedding VECTOR(384) NOT NULL,
        metadata JSONB DEFAULT '{}',
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )
    """,

    # Assessment history
    """
    CREATE TABLE IF NOT EXISTS assessment_history (
        id UUID PRIMARY KEY,
        user_id UUID REFERENCES users(id) ON DELETE SET NULL,
        company_name VARCHAR(255) NOT NULL,
        risk_score INTEGER,
        risk_level VARCHAR(16),
        score_breakdown JSONB,
        financial_data JSONB,
        risk_detail JSONB,
        scoring_version VARCHAR(16) NOT NULL DEFAULT 'unknown',
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )
    """,
]

INDEX_STATEMENTS = [
    "CREATE INDEX IF NOT EXISTS idx_users_username ON users (username)",
    "CREATE INDEX IF NOT EXISTS idx_users_email ON users (email)",
    "CREATE INDEX IF NOT EXISTS idx_users_is_active ON users (is_active)",
    "CREATE INDEX IF NOT EXISTS idx_documents_source ON documents (source)",
    "CREATE INDEX IF NOT EXISTS idx_audit_logs_created_at ON audit_logs (created_at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_audit_logs_user_action ON audit_logs (user_id, created_at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_assessment_history_company ON assessment_history (company_name, created_at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_assessment_history_user ON assessment_history (user_id, created_at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_supplier_embedding ON supplier_profiles USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100)",
    "CREATE INDEX IF NOT EXISTS idx_supplier_name ON supplier_profiles (supplier_name)",
]


def ensure_pg_schema() -> None:
    try:
        with get_cursor() as (conn, cur):
            for stmt in DDL_STATEMENTS:
                cur.execute(stmt)
            for stmt in INDEX_STATEMENTS:
                cur.execute(stmt)
            # Create ivfflat index for vector search (after data exists)
            cur.execute(
                "CREATE INDEX IF NOT EXISTS idx_documents_embedding "
                "ON documents USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100)"
            )
        logger.info("pg_schema_ready")
    except Exception as e:
        logger.exception("PostgreSQL schema initialization failed: %s", e)
        raise RuntimeError(f"PostgreSQL schema 初始化失败: {e}") from e
