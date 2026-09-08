"""
Idempotent PostgreSQL schema initialization.
Creates extensions and tables if they do not exist.
"""

import logging

from app.db.postgres import get_cursor

logger = logging.getLogger(__name__)

DDL_STATEMENTS = [
    # Extensions
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
            CHECK (merged_into_id IS NULL OR merged_into_id <> id),
        CONSTRAINT companies_verified_evidence_check CHECK (
            verification_status <> 'verified'
            OR unified_social_credit_code IS NOT NULL
            OR (
                identity_source IN ('tianyancha', 'import', 'admin_verified')
                AND source_reference IS NOT NULL
                AND BTRIM(source_reference) <> ''
            )
        )
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

    # Agent Harness control plane
    """
    CREATE TABLE IF NOT EXISTS agent_sessions (
        id UUID PRIMARY KEY,
        user_id UUID REFERENCES users(id) ON DELETE SET NULL,
        status VARCHAR(32) NOT NULL DEFAULT 'active',
        version INTEGER NOT NULL DEFAULT 1 CHECK (version > 0),
        state JSONB NOT NULL DEFAULT '{}',
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS agent_chat_interrupts (
        session_id UUID PRIMARY KEY,
        config JSONB NOT NULL DEFAULT '{}',
        mode VARCHAR(64) NOT NULL,
        user_message TEXT NOT NULL,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )
    """,
    "ALTER TABLE agent_chat_interrupts ADD COLUMN IF NOT EXISTS user_id UUID REFERENCES users(id) ON DELETE CASCADE",
    "ALTER TABLE agent_chat_interrupts ADD COLUMN IF NOT EXISTS status VARCHAR(32) NOT NULL DEFAULT 'pending'",
    "ALTER TABLE agent_chat_interrupts ADD COLUMN IF NOT EXISTS claimed_at TIMESTAMPTZ",
    "ALTER TABLE agent_chat_interrupts ADD COLUMN IF NOT EXISTS claim_token UUID",
    "CREATE INDEX IF NOT EXISTS idx_agent_chat_interrupts_status_updated ON agent_chat_interrupts(status, updated_at)",

    # Agent run V2 / Harness run
    """
    CREATE TABLE IF NOT EXISTS agent_runs (
        id UUID PRIMARY KEY,
        session_id UUID REFERENCES agent_sessions(id) ON DELETE CASCADE,
        run_type VARCHAR(32) NOT NULL CHECK (run_type IN ('sourcing_risk_v2', 'agent_harness')),
        user_id UUID REFERENCES users(id) ON DELETE SET NULL,
        status VARCHAR(32) NOT NULL,
        version INTEGER NOT NULL DEFAULT 1 CHECK (version > 0),
        requirement JSONB NOT NULL DEFAULT '{}',
        policy_snapshot_id UUID,
        decision_id UUID,
        error_code VARCHAR(64),
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        completed_at TIMESTAMPTZ
    )
    """,
    "ALTER TABLE agent_runs ADD COLUMN IF NOT EXISTS session_id UUID",
    """
    DO $$ BEGIN
        IF NOT EXISTS (
            SELECT 1 FROM pg_constraint
            WHERE conname = 'agent_runs_session_id_fkey'
              AND conrelid = 'agent_runs'::regclass
        ) THEN
            ALTER TABLE agent_runs
            ADD CONSTRAINT agent_runs_session_id_fkey
            FOREIGN KEY (session_id) REFERENCES agent_sessions(id) ON DELETE CASCADE;
        END IF;
    END $$
    """,
    """
    CREATE TABLE IF NOT EXISTS agent_turns (
        id UUID PRIMARY KEY,
        session_id UUID NOT NULL REFERENCES agent_sessions(id) ON DELETE CASCADE,
        turn_number INTEGER NOT NULL CHECK (turn_number > 0),
        user_message TEXT NOT NULL,
        assistant_message TEXT,
        status VARCHAR(32) NOT NULL DEFAULT 'received',
        request JSONB NOT NULL DEFAULT '{}',
        response JSONB,
        run_id UUID REFERENCES agent_runs(id) ON DELETE SET NULL,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        completed_at TIMESTAMPTZ,
        UNIQUE (session_id, turn_number)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS agent_tasks (
        id UUID PRIMARY KEY,
        session_id UUID NOT NULL REFERENCES agent_sessions(id) ON DELETE CASCADE,
        turn_id UUID REFERENCES agent_turns(id) ON DELETE SET NULL,
        run_id UUID REFERENCES agent_runs(id) ON DELETE CASCADE,
        task_type VARCHAR(64) NOT NULL,
        status VARCHAR(32) NOT NULL DEFAULT 'pending',
        version INTEGER NOT NULL DEFAULT 1 CHECK (version > 0),
        payload JSONB NOT NULL DEFAULT '{}',
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS agent_entities (
        id UUID PRIMARY KEY,
        session_id UUID NOT NULL REFERENCES agent_sessions(id) ON DELETE CASCADE,
        entity_key VARCHAR(255) NOT NULL,
        entity_type VARCHAR(64) NOT NULL,
        display_name VARCHAR(255) NOT NULL,
        canonical_id VARCHAR(255),
        status VARCHAR(32) NOT NULL DEFAULT 'pending_verification',
        mention_count INTEGER NOT NULL DEFAULT 1 CHECK (mention_count > 0),
        focus_rank INTEGER CHECK (focus_rank IS NULL OR focus_rank > 0),
        attributes JSONB NOT NULL DEFAULT '{}',
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        UNIQUE (session_id, entity_key)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS agent_tool_calls (
        id UUID PRIMARY KEY,
        session_id UUID NOT NULL REFERENCES agent_sessions(id) ON DELETE CASCADE,
        run_id UUID REFERENCES agent_runs(id) ON DELETE CASCADE,
        task_id UUID REFERENCES agent_tasks(id) ON DELETE SET NULL,
        tool_name VARCHAR(128) NOT NULL,
        status VARCHAR(32) NOT NULL DEFAULT 'pending',
        attempt_count INTEGER NOT NULL DEFAULT 0 CHECK (attempt_count >= 0),
        input JSONB NOT NULL DEFAULT '{}',
        output JSONB,
        error JSONB,
        idempotency_key VARCHAR(255) UNIQUE,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        completed_at TIMESTAMPTZ
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS agent_rollout_control (
        control_key VARCHAR(64) PRIMARY KEY,
        state VARCHAR(32) NOT NULL CHECK (state IN ('active', 'rollback_frozen')),
        stage VARCHAR(16) NOT NULL CHECK (stage IN ('shadow', 'internal', 'canary', 'default')),
        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )
    """,
    "INSERT INTO agent_rollout_control (control_key, state, stage) VALUES ('agent_run_v2', 'active', 'shadow') ON CONFLICT (control_key) DO NOTHING",
    """
    CREATE TABLE IF NOT EXISTS agent_run_events (
        run_id UUID NOT NULL REFERENCES agent_runs(id) ON DELETE CASCADE,
        event_id BIGINT NOT NULL,
        version INTEGER NOT NULL,
        event_type VARCHAR(48) NOT NULL,
        payload JSONB NOT NULL,
        occurred_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        PRIMARY KEY (run_id, event_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS agent_raw_payload_compensations (
        run_id UUID NOT NULL REFERENCES agent_runs(id) ON DELETE CASCADE,
        raw_payload_ref VARCHAR(255) NOT NULL,
        company_id UUID,
        staging_owner VARCHAR(255) NOT NULL,
        status VARCHAR(32) NOT NULL DEFAULT 'pending_compensation',
        attempt_count INTEGER NOT NULL DEFAULT 0 CHECK (attempt_count >= 0),
        last_error TEXT,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        PRIMARY KEY (run_id, raw_payload_ref)
    )
    """,
    "ALTER TABLE agent_raw_payload_compensations ADD COLUMN IF NOT EXISTS staging_owner VARCHAR(255)",
    "UPDATE agent_raw_payload_compensations SET staging_owner = 'legacy-recovery' WHERE staging_owner IS NULL",
    "ALTER TABLE agent_raw_payload_compensations ALTER COLUMN staging_owner SET NOT NULL",
    """
    CREATE TABLE IF NOT EXISTS sourcing_policy_templates (
        id UUID PRIMARY KEY,
        name VARCHAR(128) NOT NULL,
        version INTEGER NOT NULL CHECK (version > 0),
        policy JSONB NOT NULL,
        created_by UUID REFERENCES users(id) ON DELETE SET NULL,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        UNIQUE (name, version)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS sourcing_policy_snapshots (
        id UUID PRIMARY KEY,
        run_id UUID NOT NULL UNIQUE REFERENCES agent_runs(id) ON DELETE CASCADE,
        template_id UUID REFERENCES sourcing_policy_templates(id) ON DELETE SET NULL,
        policy JSONB NOT NULL,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS agent_run_candidates (
        id UUID PRIMARY KEY,
        run_id UUID NOT NULL REFERENCES agent_runs(id) ON DELETE CASCADE,
        company_id UUID REFERENCES companies(id) ON DELETE SET NULL,
        source VARCHAR(32) NOT NULL,
        status VARCHAR(32) NOT NULL,
        candidate_snapshot JSONB NOT NULL,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS agent_evidence (
        id UUID PRIMARY KEY,
        run_id UUID NOT NULL REFERENCES agent_runs(id) ON DELETE CASCADE,
        company_id UUID NOT NULL REFERENCES companies(id) ON DELETE RESTRICT,
        candidate_id UUID REFERENCES agent_run_candidates(id) ON DELETE CASCADE,
        monitor_target_id TEXT,
        evidence_type VARCHAR(64) NOT NULL,
        source VARCHAR(64) NOT NULL,
        source_reference VARCHAR(512),
        evidence_snapshot JSONB NOT NULL,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )
    """,
    """
    ALTER TABLE agent_evidence ADD COLUMN IF NOT EXISTS monitor_target_id TEXT
    """,
    """
    CREATE TABLE IF NOT EXISTS agent_evidence_reviews (
        run_id UUID NOT NULL REFERENCES agent_runs(id) ON DELETE CASCADE,
        company_id UUID NOT NULL REFERENCES companies(id) ON DELETE RESTRICT,
        review_snapshot JSONB NOT NULL,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        PRIMARY KEY (run_id, company_id)
    )
    """,
    """
    ALTER TABLE agent_evidence ADD COLUMN IF NOT EXISTS company_id UUID
    """,
    """
    UPDATE agent_evidence evidence
    SET company_id = candidate.company_id
    FROM agent_run_candidates candidate
    WHERE evidence.company_id IS NULL
      AND evidence.candidate_id = candidate.id
      AND candidate.company_id IS NOT NULL
    """,
    """
    DO $$ BEGIN
        IF EXISTS (SELECT 1 FROM agent_evidence WHERE company_id IS NULL) THEN
            RAISE EXCEPTION 'agent_evidence.company_id cannot be backfilled safely';
        END IF;
        ALTER TABLE agent_evidence ALTER COLUMN company_id SET NOT NULL;
        ALTER TABLE agent_evidence
            ADD CONSTRAINT agent_evidence_company_id_fkey
            FOREIGN KEY (company_id) REFERENCES companies(id) ON DELETE RESTRICT;
    EXCEPTION WHEN duplicate_object THEN NULL;
    END $$
    """,
    """
    CREATE TABLE IF NOT EXISTS candidate_decisions (
        id UUID PRIMARY KEY,
        run_id UUID NOT NULL REFERENCES agent_runs(id) ON DELETE CASCADE,
        candidate_id UUID REFERENCES agent_run_candidates(id) ON DELETE CASCADE,
        decision VARCHAR(32) NOT NULL,
        score_snapshot JSONB NOT NULL DEFAULT '{}',
        reason_snapshot JSONB NOT NULL DEFAULT '{}',
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS agent_action_proposals (
        id UUID PRIMARY KEY,
        run_id UUID NOT NULL REFERENCES agent_runs(id) ON DELETE CASCADE,
        candidate_id UUID REFERENCES agent_run_candidates(id) ON DELETE SET NULL,
        action_type VARCHAR(64) NOT NULL,
        status VARCHAR(32) NOT NULL,
        execution_state VARCHAR(32) NOT NULL DEFAULT 'pending',
        payload JSONB NOT NULL,
        idempotency_key VARCHAR(255) NOT NULL UNIQUE,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        UNIQUE (run_id, id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS agent_approval_decisions (
        id UUID PRIMARY KEY,
        run_id UUID NOT NULL REFERENCES agent_runs(id) ON DELETE CASCADE,
        proposal_id UUID NOT NULL,
        user_id UUID REFERENCES users(id) ON DELETE SET NULL,
        decision VARCHAR(32) NOT NULL,
        comment TEXT,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        FOREIGN KEY (run_id, proposal_id)
            REFERENCES agent_action_proposals (run_id, id) ON DELETE CASCADE
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS monitor_review_tasks (
        id UUID PRIMARY KEY,
        monitor_target_id TEXT NOT NULL,
        task_type VARCHAR(64) NOT NULL,
        status VARCHAR(32) NOT NULL,
        version INTEGER NOT NULL DEFAULT 1 CHECK (version > 0),
        requested_by UUID REFERENCES users(id) ON DELETE SET NULL,
        approved_by UUID REFERENCES users(id) ON DELETE SET NULL,
        approval_comment TEXT,
        agent_run_id UUID REFERENCES agent_runs(id) ON DELETE SET NULL,
        proposal_id UUID,
        payload JSONB NOT NULL DEFAULT '{}',
        result JSONB,
        evidence_refs JSONB NOT NULL DEFAULT '[]',
        error_code VARCHAR(64),
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        approved_at TIMESTAMPTZ,
        started_at TIMESTAMPTZ,
        completed_at TIMESTAMPTZ,
        CHECK (status IN ('pending_approval', 'approved', 'rejected', 'executing', 'completed', 'needs_review', 'failed', 'cancelled'))
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS monitor_review_task_events (
        id UUID PRIMARY KEY,
        task_id UUID NOT NULL REFERENCES monitor_review_tasks(id) ON DELETE CASCADE,
        actor_id UUID REFERENCES users(id) ON DELETE SET NULL,
        event_type VARCHAR(64) NOT NULL,
        from_status VARCHAR(32),
        to_status VARCHAR(32),
        payload JSONB NOT NULL DEFAULT '{}',
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS monitor_review_task_evidence (
        id UUID PRIMARY KEY,
        task_id UUID NOT NULL REFERENCES monitor_review_tasks(id) ON DELETE CASCADE,
        monitor_target_id TEXT NOT NULL,
        evidence_id VARCHAR(255) NOT NULL,
        evidence JSONB NOT NULL,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        UNIQUE (task_id, evidence_id)
    )
    """,
]

VERIFIED_EVIDENCE_CONDITION = """
    verification_status <> 'verified'
    OR unified_social_credit_code IS NOT NULL
    OR (
        identity_source IN ('tianyancha', 'import', 'admin_verified')
        AND source_reference IS NOT NULL
        AND BTRIM(source_reference) <> ''
    )
"""


def _ensure_verified_evidence_constraint(cur: object) -> None:
    """Require legacy verified rows to be remediated before enforcing the check.

    A previous migration added this constraint ``NOT VALID``.  Leaving it in
    that state permits historical rows that contradict the production identity
    invariant.  We never alter those rows here: an operator must add evidence
    or explicitly return the company to pending verification.
    """
    cur.execute(
        """
        SELECT convalidated
        FROM pg_constraint
        WHERE conrelid = 'companies'::regclass
          AND conname = 'companies_verified_evidence_check'
        """
    )
    constraint = cur.fetchone()
    cur.execute(
        f"""
        SELECT id::text
        FROM companies
        WHERE NOT ({VERIFIED_EVIDENCE_CONDITION})
        ORDER BY id
        LIMIT 20
        """
    )
    invalid_ids = [row[0] for row in cur.fetchall()]
    if invalid_ids:
        examples = ", ".join(invalid_ids)
        raise RuntimeError(
            "companies 存在缺少核验凭据的 verified 企业（ID: "
            f"{examples}）。请补充统一社会信用代码或可信来源引用，"
            "或将其改为 pending_verification 后重试。"
        )

    if constraint is None:
        cur.execute(
            f"""
            ALTER TABLE companies
            ADD CONSTRAINT companies_verified_evidence_check
            CHECK ({VERIFIED_EVIDENCE_CONDITION})
            """
        )
    elif not constraint[0]:
        cur.execute(
            "ALTER TABLE companies VALIDATE CONSTRAINT companies_verified_evidence_check"
        )


def _ensure_agent_run_constraints(cur: object) -> None:
    """Add Harness-compatible constraints when upgrading an existing database."""
    cur.execute(
        """
        SELECT pg_get_constraintdef(oid)
        FROM pg_constraint
        WHERE conrelid = 'agent_runs'::regclass
          AND conname = 'agent_runs_run_type_check'
        """
    )
    run_type_constraint = cur.fetchone()
    if run_type_constraint is None or "agent_harness" not in str(run_type_constraint[0]):
        cur.execute("ALTER TABLE agent_runs DROP CONSTRAINT IF EXISTS agent_runs_run_type_check")
        cur.execute(
            """
            ALTER TABLE agent_runs
            ADD CONSTRAINT agent_runs_run_type_check
            CHECK (run_type IN ('sourcing_risk_v2', 'agent_harness'))
            """
        )

    cur.execute(
        """
        SELECT EXISTS (
            SELECT 1
            FROM pg_constraint constraint_record
            WHERE constraint_record.conrelid = 'agent_action_proposals'::regclass
              AND constraint_record.contype IN ('u', 'p')
              AND constraint_record.conkey = ARRAY[
                  (SELECT attnum FROM pg_attribute
                   WHERE attrelid = 'agent_action_proposals'::regclass AND attname = 'run_id'),
                  (SELECT attnum FROM pg_attribute
                   WHERE attrelid = 'agent_action_proposals'::regclass AND attname = 'id')
              ]::smallint[]
        )
        """
    )
    if not cur.fetchone()[0]:
        cur.execute(
            """
            ALTER TABLE agent_action_proposals
            ADD CONSTRAINT agent_action_proposals_run_id_id_compat_key UNIQUE (run_id, id)
            """
        )

    cur.execute(
        """
        SELECT EXISTS (
            SELECT 1
            FROM pg_constraint constraint_record
            WHERE constraint_record.conrelid = 'agent_approval_decisions'::regclass
              AND constraint_record.confrelid = 'agent_action_proposals'::regclass
              AND constraint_record.contype = 'f'
              AND constraint_record.conkey = ARRAY[
                  (SELECT attnum FROM pg_attribute
                   WHERE attrelid = 'agent_approval_decisions'::regclass AND attname = 'run_id'),
                  (SELECT attnum FROM pg_attribute
                   WHERE attrelid = 'agent_approval_decisions'::regclass AND attname = 'proposal_id')
              ]::smallint[]
              AND constraint_record.confkey = ARRAY[
                  (SELECT attnum FROM pg_attribute
                   WHERE attrelid = 'agent_action_proposals'::regclass AND attname = 'run_id'),
                  (SELECT attnum FROM pg_attribute
                   WHERE attrelid = 'agent_action_proposals'::regclass AND attname = 'id')
              ]::smallint[]
        )
        """
    )
    if not cur.fetchone()[0]:
        cur.execute(
            """
            ALTER TABLE agent_approval_decisions
            DROP CONSTRAINT IF EXISTS agent_approval_decisions_proposal_id_fkey
            """
        )
        cur.execute(
            """
            ALTER TABLE agent_approval_decisions
            ADD CONSTRAINT agent_approval_decisions_run_id_proposal_id_fkey
            FOREIGN KEY (run_id, proposal_id)
            REFERENCES agent_action_proposals (run_id, id) ON DELETE CASCADE
            """
        )

INDEX_STATEMENTS = [
    "CREATE INDEX IF NOT EXISTS idx_users_username ON users (username)",
    "CREATE INDEX IF NOT EXISTS idx_users_email ON users (email)",
    "CREATE INDEX IF NOT EXISTS idx_users_is_active ON users (is_active)",
    "CREATE INDEX IF NOT EXISTS idx_audit_logs_created_at ON audit_logs (created_at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_audit_logs_user_action ON audit_logs (user_id, created_at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_companies_normalized_name ON companies (normalized_name)",
    "CREATE INDEX IF NOT EXISTS idx_companies_normalized_name_pattern ON companies (normalized_name text_pattern_ops)",
    "CREATE INDEX IF NOT EXISTS idx_companies_merged_into_id ON companies (merged_into_id)",
    "CREATE INDEX IF NOT EXISTS idx_company_aliases_normalized_alias ON company_aliases (normalized_alias)",
    "CREATE INDEX IF NOT EXISTS idx_company_merge_log_source_company ON company_merge_log (source_company_id)",
    "CREATE INDEX IF NOT EXISTS idx_company_merge_log_target_company ON company_merge_log (target_company_id)",
    "CREATE INDEX IF NOT EXISTS idx_outbox_pending ON outbox_events (next_attempt_at, occurred_at) "
    "WHERE published_at IS NULL AND dead_lettered_at IS NULL",
    "CREATE INDEX IF NOT EXISTS idx_assessment_history_company ON assessment_history (company_name, created_at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_assessment_history_user ON assessment_history (user_id, created_at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_agent_sessions_user_updated ON agent_sessions (user_id, updated_at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_agent_turns_session_created ON agent_turns (session_id, turn_number)",
    "CREATE INDEX IF NOT EXISTS idx_agent_runs_user_created ON agent_runs (user_id, created_at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_agent_runs_session_created ON agent_runs (session_id, created_at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_agent_run_events_run_event ON agent_run_events (run_id, event_id)",
    "CREATE INDEX IF NOT EXISTS idx_agent_tasks_run_status ON agent_tasks (run_id, status, created_at)",
    "CREATE INDEX IF NOT EXISTS idx_agent_entities_session_focus ON agent_entities (session_id, focus_rank)",
    "CREATE INDEX IF NOT EXISTS idx_agent_tool_calls_run_created ON agent_tool_calls (run_id, created_at)",
    "CREATE INDEX IF NOT EXISTS idx_agent_run_candidates_run_status ON agent_run_candidates (run_id, status)",
    "CREATE INDEX IF NOT EXISTS idx_agent_evidence_run ON agent_evidence (run_id)",
    "CREATE INDEX IF NOT EXISTS idx_agent_evidence_company ON agent_evidence (company_id)",
    "CREATE INDEX IF NOT EXISTS idx_agent_evidence_monitor_target ON agent_evidence (monitor_target_id)",
    "CREATE INDEX IF NOT EXISTS idx_agent_evidence_reviews_run ON agent_evidence_reviews (run_id)",
    "CREATE INDEX IF NOT EXISTS idx_candidate_decisions_run ON candidate_decisions (run_id)",
    "CREATE INDEX IF NOT EXISTS idx_agent_action_proposals_pending_execution "
    "ON agent_action_proposals (execution_state, created_at) WHERE execution_state = 'pending'",
    "CREATE INDEX IF NOT EXISTS idx_agent_approval_decisions_run ON agent_approval_decisions (run_id)",
    "CREATE INDEX IF NOT EXISTS idx_monitor_review_tasks_target_created ON monitor_review_tasks (monitor_target_id, created_at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_monitor_review_tasks_status_updated ON monitor_review_tasks (status, updated_at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_monitor_review_task_events_task_created ON monitor_review_task_events (task_id, created_at)",
    "CREATE INDEX IF NOT EXISTS idx_monitor_review_task_evidence_task_created ON monitor_review_task_evidence (task_id, created_at)",
]


def ensure_pg_schema() -> None:
    try:
        with get_cursor() as (conn, cur):
            for stmt in DDL_STATEMENTS:
                cur.execute(stmt)
            _ensure_verified_evidence_constraint(cur)
            _ensure_agent_run_constraints(cur)
            for stmt in INDEX_STATEMENTS:
                cur.execute(stmt)
        logger.info("pg_schema_ready")
    except Exception as e:
        logger.exception("PostgreSQL schema initialization failed: %s", e)
        raise RuntimeError(f"PostgreSQL schema 初始化失败: {e}") from e
