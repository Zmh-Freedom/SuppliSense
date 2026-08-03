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

    # Company identity
    """
    CREATE TABLE IF NOT EXISTS companies (
        id UUID PRIMARY KEY,
        legal_name VARCHAR(255) NOT NULL,
        normalized_name VARCHAR(255) NOT NULL,
        unified_social_credit_code VARCHAR(18),
        registration_status VARCHAR(32),
        verification_status VARCHAR(24) NOT NULL,
        identity_source VARCHAR(32) NOT NULL,
        source_reference VARCHAR(255),
        identity_version INTEGER NOT NULL DEFAULT 1,
        merged_into_id UUID REFERENCES companies(id),
        created_by UUID REFERENCES users(id) ON DELETE SET NULL,
        verified_by UUID REFERENCES users(id) ON DELETE SET NULL,
        verified_at TIMESTAMPTZ,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        CONSTRAINT companies_unified_social_credit_code_key
            UNIQUE (unified_social_credit_code),
        CONSTRAINT companies_verification_status_check
            CHECK (verification_status IN ('verified', 'pending_verification')),
        CONSTRAINT companies_identity_version_check CHECK (identity_version > 0),
        CONSTRAINT companies_merged_into_id_check
            CHECK (merged_into_id IS NULL OR merged_into_id <> id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS company_aliases (
        id UUID PRIMARY KEY,
        company_id UUID NOT NULL REFERENCES companies(id),
        alias_name VARCHAR(255) NOT NULL,
        normalized_alias VARCHAR(255) NOT NULL,
        alias_type VARCHAR(24) NOT NULL,
        source VARCHAR(32) NOT NULL,
        confidence NUMERIC(3, 2) NOT NULL DEFAULT 1.0,
        created_by UUID REFERENCES users(id) ON DELETE SET NULL,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        CONSTRAINT company_aliases_company_normalized_alias_key
            UNIQUE (company_id, normalized_alias),
        CONSTRAINT company_aliases_alias_type_check
            CHECK (alias_type IN ('short_name', 'former_name', 'english_name', 'source_name')),
        CONSTRAINT company_aliases_confidence_check
            CHECK (confidence >= 0 AND confidence <= 1)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS company_merge_log (
        id UUID PRIMARY KEY,
        source_company_id UUID NOT NULL REFERENCES companies(id),
        target_company_id UUID NOT NULL REFERENCES companies(id),
        reason TEXT NOT NULL,
        operator_id UUID REFERENCES users(id) ON DELETE SET NULL,
        source_version INTEGER NOT NULL,
        target_version INTEGER NOT NULL,
        compensation_snapshot JSONB NOT NULL,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        CONSTRAINT company_merge_log_source_version_check CHECK (source_version > 0),
        CONSTRAINT company_merge_log_target_version_check CHECK (target_version > 0),
        CONSTRAINT company_merge_log_different_companies_check
            CHECK (source_company_id <> target_company_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS outbox_events (
        event_id UUID PRIMARY KEY,
        event_type VARCHAR(64) NOT NULL,
        aggregate_type VARCHAR(64) NOT NULL,
        aggregate_id UUID NOT NULL,
        schema_version INTEGER NOT NULL DEFAULT 1,
        payload JSONB NOT NULL,
        occurred_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        published_at TIMESTAMPTZ,
        attempt_count INTEGER NOT NULL DEFAULT 0,
        last_error TEXT,
        next_attempt_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        locked_by VARCHAR(128),
        locked_until TIMESTAMPTZ,
        dead_lettered_at TIMESTAMPTZ,
        CONSTRAINT outbox_events_schema_version_check CHECK (schema_version > 0),
        CONSTRAINT outbox_events_attempt_count_check CHECK (attempt_count >= 0)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS outbox_consumptions (
        event_id UUID NOT NULL REFERENCES outbox_events(event_id) ON DELETE CASCADE,
        consumer_name VARCHAR(128) NOT NULL,
        processed_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        PRIMARY KEY (event_id, consumer_name)
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
    "CREATE INDEX IF NOT EXISTS idx_companies_normalized_name ON companies (normalized_name)",
    "CREATE INDEX IF NOT EXISTS idx_companies_merged_into_id ON companies (merged_into_id)",
    "CREATE INDEX IF NOT EXISTS idx_company_aliases_normalized_alias ON company_aliases (normalized_alias)",
    "CREATE INDEX IF NOT EXISTS idx_company_merge_log_source_company ON company_merge_log (source_company_id)",
    "CREATE INDEX IF NOT EXISTS idx_company_merge_log_target_company ON company_merge_log (target_company_id)",
    "CREATE INDEX IF NOT EXISTS idx_outbox_pending ON outbox_events (next_attempt_at, occurred_at) "
    "WHERE published_at IS NULL AND dead_lettered_at IS NULL",
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
