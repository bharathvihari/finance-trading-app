-- =============================================================================
-- 005_corporate_events.sql
-- Reference table for corporate events (dividends, splits, earnings).
-- Populated by apps/workers/jobs/fetch_events.py (yfinance source).
-- Read by GET /api/v1/events/{symbol} to serve EventMarker data.
-- =============================================================================

CREATE TABLE IF NOT EXISTS market_data.corporate_events (
    id          BIGSERIAL   PRIMARY KEY,
    symbol      TEXT        NOT NULL,
    exchange    TEXT        NOT NULL,
    asset_class TEXT        NOT NULL DEFAULT 'equity',
    event_type  TEXT        NOT NULL CHECK (event_type IN ('dividend', 'split', 'earnings')),
    event_date  DATE        NOT NULL,
    value       DOUBLE PRECISION,       -- dividend amount, split ratio, EPS estimate, etc.
    currency    TEXT,
    description TEXT,
    fetched_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_corporate_events UNIQUE (symbol, exchange, event_type, event_date)
);

CREATE INDEX IF NOT EXISTS idx_corporate_events_lookup
    ON market_data.corporate_events (symbol, exchange, event_date DESC);
