-- =============================================================================
-- 006_rolling_metrics.sql
-- Pre-aggregated performance metrics per symbol, computed nightly by
-- apps/workers/jobs/precompute_metrics.py and read by the API instead of
-- computing on every request. Keeps p95 latency < 50 ms for popular symbols.
--
-- window_days:  252 = 1 year, 504 = 2 years, 756 = 3 years, 1260 = 5 years
-- as_of_date:   the last trading date included in the computation window
-- =============================================================================

CREATE TABLE IF NOT EXISTS market_data.rolling_metrics (
    symbol          TEXT            NOT NULL,
    exchange        TEXT            NOT NULL,
    asset_class     TEXT            NOT NULL DEFAULT 'equity',
    frequency       TEXT            NOT NULL DEFAULT 'daily',
    as_of_date      DATE            NOT NULL,
    window_days     INTEGER         NOT NULL,
    total_return    DOUBLE PRECISION,
    cagr            DOUBLE PRECISION,
    max_drawdown    DOUBLE PRECISION,
    volatility      DOUBLE PRECISION,
    sharpe          DOUBLE PRECISION,
    sortino         DOUBLE PRECISION,
    computed_at     TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    PRIMARY KEY (symbol, exchange, asset_class, frequency, as_of_date, window_days)
);

CREATE INDEX IF NOT EXISTS idx_rolling_metrics_lookup
    ON market_data.rolling_metrics (symbol, exchange, as_of_date DESC);
