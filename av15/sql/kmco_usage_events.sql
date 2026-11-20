-- KMCO usage event journal in the existing km_microops database.
-- Apply only to km_microops after reviewing existing objects and owner approval.
-- Does not create a database, role, extension, or embedding index.
BEGIN;
CREATE SCHEMA IF NOT EXISTS circuit;
CREATE TABLE IF NOT EXISTS circuit.usage_events (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    recorded_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    operation text NOT NULL,
    operation_ids text[] NOT NULL DEFAULT '{}',
    mission_id text,
    task_id text,
    source_sha text,
    args_sha256 text NOT NULL CHECK (args_sha256 ~ '^[0-9a-f]{64}$'),
    result_sha256 text CHECK (result_sha256 IS NULL OR result_sha256 ~ '^[0-9a-f]{64}$'),
    success boolean NOT NULL,
    error_class text,
    executor text NOT NULL DEFAULT 'KMCW.ToolRegistry',
    verification_state text NOT NULL DEFAULT 'UNVERIFIED',
    CHECK (verification_state IN ('UNVERIFIED', 'INDEPENDENT_VERIFIED')),
    CHECK (operation LIKE 'kmco.%'),
    CHECK (NOT success OR error_class IS NULL)
);
CREATE INDEX IF NOT EXISTS usage_events_operation_time_idx
    ON circuit.usage_events (operation, recorded_at DESC);
CREATE INDEX IF NOT EXISTS usage_events_mission_task_idx
    ON circuit.usage_events (mission_id, task_id, recorded_at DESC);
COMMIT;
