CREATE SCHEMA IF NOT EXISTS market_data;

CREATE TABLE IF NOT EXISTS market_data.daily_bars (
    symbol TEXT NOT NULL,
    exchange TEXT NOT NULL,
    asset_class TEXT NOT NULL,
    frequency TEXT NOT NULL,
    timestamp TIMESTAMPTZ NOT NULL,
    open DOUBLE PRECISION NOT NULL,
    high DOUBLE PRECISION NOT NULL,
    low DOUBLE PRECISION NOT NULL,
    close DOUBLE PRECISION NOT NULL,
    volume DOUBLE PRECISION NOT NULL,
    ingested_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (symbol, exchange, asset_class, frequency, timestamp)
);

CREATE INDEX IF NOT EXISTS idx_daily_bars_timestamp
    ON market_data.daily_bars (timestamp);

CREATE INDEX IF NOT EXISTS idx_daily_bars_symbol_ts
    ON market_data.daily_bars (symbol, exchange, asset_class, frequency, timestamp DESC);
