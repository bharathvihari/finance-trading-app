CREATE TABLE IF NOT EXISTS users (
    id UUID PRIMARY KEY,
    email TEXT UNIQUE NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS dashboard_layouts (
    id UUID PRIMARY KEY,
    user_id UUID NOT NULL REFERENCES users(id),
    name TEXT NOT NULL,
    layout_json JSONB NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS strategy_configs (
    id UUID PRIMARY KEY,
    user_id UUID NOT NULL REFERENCES users(id),
    strategy_name TEXT NOT NULL,
    config_json JSONB NOT NULL,
    mode TEXT NOT NULL CHECK (mode IN ('backtest', 'paper', 'live')),
    is_active BOOLEAN NOT NULL DEFAULT FALSE,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS alerts (
    id UUID PRIMARY KEY,
    user_id UUID NOT NULL REFERENCES users(id),
    alert_type TEXT NOT NULL,
    message TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
