"""Additive assessment-ledger schema; historical IDs and scores are preserved."""

ASSESSMENT_DDL = [
    """CREATE TABLE IF NOT EXISTS assessment_policies (
        policy_hash TEXT PRIMARY KEY, policy_id TEXT NOT NULL, version TEXT NOT NULL,
        scope_key TEXT NOT NULL, snapshot JSONB NOT NULL, created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        UNIQUE(policy_id,version,scope_key))""",
    "ALTER TABLE assessment_history ADD COLUMN IF NOT EXISTS actor_id TEXT",
    "ALTER TABLE assessment_history ADD COLUMN IF NOT EXISTS actor_scope TEXT",
    "ALTER TABLE assessment_history ADD COLUMN IF NOT EXISTS company_id UUID REFERENCES companies(id)",
    "ALTER TABLE assessment_history ADD COLUMN IF NOT EXISTS monitor_target_id TEXT",
    "ALTER TABLE assessment_history ADD COLUMN IF NOT EXISTS run_id UUID REFERENCES agent_runs(id) ON DELETE SET NULL",
    "ALTER TABLE assessment_history ADD COLUMN IF NOT EXISTS logical_key TEXT",
    "ALTER TABLE assessment_history ADD COLUMN IF NOT EXISTS request_key TEXT",
    "ALTER TABLE assessment_history ADD COLUMN IF NOT EXISTS request_hash TEXT",
    "ALTER TABLE assessment_history ADD COLUMN IF NOT EXISTS scope_key TEXT",
    "ALTER TABLE assessment_history ADD COLUMN IF NOT EXISTS identity JSONB NOT NULL DEFAULT '{}'::jsonb",
    "ALTER TABLE assessment_history ADD COLUMN IF NOT EXISTS assessment_status TEXT NOT NULL DEFAULT 'legacy_unverified'",
    "ALTER TABLE assessment_history ADD COLUMN IF NOT EXISTS quality TEXT NOT NULL DEFAULT 'legacy_unverified'",
    "ALTER TABLE assessment_history ADD COLUMN IF NOT EXISTS policy_hash TEXT REFERENCES assessment_policies(policy_hash)",
    "ALTER TABLE assessment_history ADD COLUMN IF NOT EXISTS policy_snapshot JSONB NOT NULL DEFAULT '{}'::jsonb",
    "ALTER TABLE assessment_history ADD COLUMN IF NOT EXISTS provenance JSONB NOT NULL DEFAULT '{}'::jsonb",
    "ALTER TABLE assessment_history ADD COLUMN IF NOT EXISTS input_snapshot JSONB NOT NULL DEFAULT '{}'::jsonb",
    "ALTER TABLE assessment_history ADD COLUMN IF NOT EXISTS input_digest TEXT",
    "ALTER TABLE assessment_history ADD COLUMN IF NOT EXISTS evidence_digest TEXT",
    "ALTER TABLE assessment_history ADD COLUMN IF NOT EXISTS evidence_frozen_at TIMESTAMPTZ",
    "ALTER TABLE assessment_history ADD COLUMN IF NOT EXISTS assessed_at TIMESTAMPTZ",
    "ALTER TABLE assessment_history ADD COLUMN IF NOT EXISTS completed_at TIMESTAMPTZ",
    "ALTER TABLE assessment_history ADD COLUMN IF NOT EXISTS assessment_version BIGINT",
    "ALTER TABLE assessment_history ADD COLUMN IF NOT EXISTS previous_assessment_id UUID",
    "ALTER TABLE assessment_history ADD COLUMN IF NOT EXISTS linked_assessment_id UUID",
    "ALTER TABLE assessment_history ADD COLUMN IF NOT EXISTS collector_token UUID",
    "ALTER TABLE assessment_history ADD COLUMN IF NOT EXISTS execution_fencing_token BIGINT NOT NULL DEFAULT 0",
    "ALTER TABLE assessment_history ADD COLUMN IF NOT EXISTS collection_lease_until TIMESTAMPTZ",
    "ALTER TABLE assessment_history ADD COLUMN IF NOT EXISTS result JSONB",
    """CREATE UNIQUE INDEX IF NOT EXISTS idx_assessment_run_logical_key
       ON assessment_history(run_id,logical_key) WHERE run_id IS NOT NULL AND logical_key IS NOT NULL""",
    """CREATE UNIQUE INDEX IF NOT EXISTS idx_assessment_request_key
       ON assessment_history(actor_scope,request_key) WHERE run_id IS NULL AND request_key IS NOT NULL""",
    "CREATE INDEX IF NOT EXISTS idx_assessment_subject ON assessment_history(company_id,scope_key,policy_hash,assessment_version DESC)",
    """CREATE TABLE IF NOT EXISTS assessment_sequences (
        sequence_key TEXT PRIMARY KEY, version BIGINT NOT NULL DEFAULT 0,
        last_assessment_id UUID)""",
    """CREATE TABLE IF NOT EXISTS assessment_evidence (
        assessment_id UUID NOT NULL REFERENCES assessment_history(id) ON DELETE CASCADE,
        evidence_id TEXT NOT NULL, manifest JSONB NOT NULL, PRIMARY KEY(assessment_id,evidence_id))""",
    """CREATE TABLE IF NOT EXISTS assessment_payload_staging (
        assessment_id UUID NOT NULL REFERENCES assessment_history(id) ON DELETE CASCADE,
        raw_payload_ref TEXT NOT NULL, staging_owner TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'pending', detail JSONB NOT NULL DEFAULT '{}'::jsonb,
        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(), PRIMARY KEY(assessment_id,raw_payload_ref))""",
    """CREATE TABLE IF NOT EXISTS assessment_reviews (
        id UUID PRIMARY KEY, assessment_id UUID NOT NULL REFERENCES assessment_history(id) ON DELETE CASCADE,
        actor_id TEXT NOT NULL, reason TEXT NOT NULL, decision TEXT NOT NULL,
        idempotency_key TEXT NOT NULL, request_hash TEXT NOT NULL, receipt JSONB,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(), UNIQUE(assessment_id,actor_id,idempotency_key))""",
]
