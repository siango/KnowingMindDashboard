-- Add only the missing derived KMCO learning projection to existing km_microops.
-- KEBUNATE_CORE_V1: KNOW -> REMEMBER -> DO, Four-part observed work cases.
-- This is engineering memory, not Buddhist Canon or a self-certified QC receipt.
BEGIN;
DO $$
BEGIN
  IF to_regnamespace('circuit') IS NULL THEN
    RAISE EXCEPTION 'KMCO_EXISTING_CIRCUIT_SCHEMA_REQUIRED';
  END IF;
  IF to_regclass('circuit.registry') IS NULL
     OR to_regclass('circuit.bindings') IS NULL
     OR to_regclass('circuit.relations') IS NULL
     OR to_regclass('circuit.routes') IS NULL
     OR to_regclass('circuit.usage_events') IS NULL THEN
    RAISE EXCEPTION 'KMCO_EXISTING_TABLES_REQUIRED';
  END IF;
  IF to_regclass('circuit.core_memory') IS NOT NULL THEN
    RAISE NOTICE 'KMCO_CORE_MEMORY_ALREADY_EXISTS_VERIFY_CONTRACT';
  END IF;
END $$;
CREATE TABLE IF NOT EXISTS circuit.core_memory (
    case_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    mission_id text NOT NULL CHECK (length(mission_id) BETWEEN 1 AND 128),
    task_id text NOT NULL CHECK (length(task_id) BETWEEN 1 AND 128),
    operation text NOT NULL CHECK (operation LIKE 'kmco.%'),
    operation_ids text[] NOT NULL DEFAULT '{}',
    args_sha256 text NOT NULL CHECK (args_sha256 ~ '^[0-9a-f]{64}$'),
    source_sha text NOT NULL DEFAULT '',
    dukkha jsonb NOT NULL,
    samudaya jsonb NOT NULL,
    nirodha jsonb NOT NULL,
    magga jsonb NOT NULL,
    result_state text NOT NULL CHECK (result_state IN ('EXECUTED_UNVERIFIED','BLOCKED_UNVERIFIED')),
    result_sha256 text,
    attempt_count bigint NOT NULL DEFAULT 1,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (mission_id, task_id, operation, args_sha256)
);
CREATE INDEX IF NOT EXISTS core_memory_lookup_idx
 ON circuit.core_memory (mission_id, task_id, updated_at DESC);
COMMIT;
