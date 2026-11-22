-- Additive migration on EXISTING kebunate-control-runtime D1 only.
-- Source-only; never create a duplicate database or modify production without schema readback.
CREATE TABLE IF NOT EXISTS kmcp_stripe_event_inbox (
 event_id TEXT PRIMARY KEY,
 event_type TEXT NOT NULL, object_id TEXT NOT NULL,
 body_sha256 TEXT NOT NULL CHECK(length(body_sha256)=64),
 stripe_account_id TEXT NOT NULL,livemode INTEGER NOT NULL CHECK(livemode IN (0,1)),
 event_created INTEGER NOT NULL,processing_state TEXT NOT NULL
 CHECK(processing_state IN ('PENDING_RECONCILIATION','IGNORED_NOT_SUBSCRIBED','RECONCILED','REVIEW_REQUIRED')),
 received_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_kmcp_stripe_inbox_state ON kmcp_stripe_event_inbox(processing_state,received_at);
CREATE TABLE IF NOT EXISTS kmcp_stripe_event_outbox (
 event_id TEXT PRIMARY KEY REFERENCES kmcp_stripe_event_inbox(event_id),
 route TEXT NOT NULL CHECK(route='KMCO_OBSERVE_ONLY'),
 status TEXT NOT NULL CHECK(status IN ('PENDING','CLAIMED','OBSERVED','REVIEW_REQUIRED')),
 created_at TEXT NOT NULL
);
-- Billing usage still company_os_usage_ledger, with unique idempotency_key.
-- Paid entitlements are NEVER inferred from webhook delivery alone.
