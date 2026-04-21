-- =============================================================================
-- 003_app_schema.sql
-- Application schema migration — extends the skeleton from 001_init.sql
-- with the full set of columns and tables needed for multi-user operation.
--
-- Safe to run against a DB that already has 001_init.sql applied:
--   - Existing tables are extended with ADD COLUMN IF NOT EXISTS.
--   - New tables use CREATE TABLE IF NOT EXISTS.
--   - All indexes use CREATE INDEX IF NOT EXISTS.
-- =============================================================================


-- ---------------------------------------------------------------------------
-- 1. USERS  (extend skeleton)
-- ---------------------------------------------------------------------------

ALTER TABLE users
    ADD COLUMN IF NOT EXISTS password_hash     TEXT,
    ADD COLUMN IF NOT EXISTS base_currency     TEXT        NOT NULL DEFAULT 'USD',
    ADD COLUMN IF NOT EXISTS display_tz        TEXT        NOT NULL DEFAULT 'UTC',
    ADD COLUMN IF NOT EXISTS display_date_fmt  TEXT        NOT NULL DEFAULT 'YYYY-MM-DD',
    ADD COLUMN IF NOT EXISTS created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW();

CREATE INDEX IF NOT EXISTS idx_users_email ON users (email);


-- ---------------------------------------------------------------------------
-- 2. BROKER ACCOUNTS  (new)
--    One user can have multiple broker connections (IBKR live, IBKR paper,
--    manual import, demo). Each portfolio links to exactly one account.
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS broker_accounts (
    id              UUID        PRIMARY KEY,
    user_id         UUID        NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    broker          TEXT        NOT NULL CHECK (broker IN ('ibkr', 'paper', 'demo', 'manual')),
    display_name    TEXT        NOT NULL,
    account_ref     TEXT,                         -- broker-assigned account ID (nullable for demo/manual)
    currency        TEXT        NOT NULL DEFAULT 'USD',
    is_active       BOOLEAN     NOT NULL DEFAULT TRUE,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_broker_accounts_user ON broker_accounts (user_id);


-- ---------------------------------------------------------------------------
-- 3. PORTFOLIOS  (new)
--    Represents a single account view — live, paper, demo, or imported.
--    A user can have many portfolios; each is hard-isolated to its owner.
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS portfolios (
    id                  UUID        PRIMARY KEY,
    user_id             UUID        NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    broker_account_id   UUID        REFERENCES broker_accounts (id) ON DELETE SET NULL,
    name                TEXT        NOT NULL,
    portfolio_type      TEXT        NOT NULL CHECK (portfolio_type IN ('live', 'paper', 'demo', 'imported')),
    base_currency       TEXT        NOT NULL DEFAULT 'USD',
    is_default          BOOLEAN     NOT NULL DEFAULT FALSE,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_portfolios_user ON portfolios (user_id);

-- Enforce at most one default portfolio per user
CREATE UNIQUE INDEX IF NOT EXISTS idx_portfolios_user_default
    ON portfolios (user_id)
    WHERE is_default = TRUE;


-- ---------------------------------------------------------------------------
-- 4. DASHBOARD LAYOUTS  (extend skeleton)
-- ---------------------------------------------------------------------------

ALTER TABLE dashboard_layouts
    ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ NOT NULL DEFAULT NOW();

CREATE INDEX IF NOT EXISTS idx_dashboard_layouts_user ON dashboard_layouts (user_id);


-- ---------------------------------------------------------------------------
-- 5. WIDGET CONFIGS  (new)
--    Each widget belongs to exactly one dashboard. user_id is denormalized
--    here so the API can enforce isolation in a single-table query.
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS widget_configs (
    id                  UUID        PRIMARY KEY,
    dashboard_layout_id UUID        NOT NULL REFERENCES dashboard_layouts (id) ON DELETE CASCADE,
    user_id             UUID        NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    widget_type         TEXT        NOT NULL,     -- e.g. 'candlestick_chart', 'portfolio_overview', 'watchlist'
    title               TEXT,
    config_json         JSONB       NOT NULL DEFAULT '{}',   -- symbol, timeframe, indicators, etc.
    position_json       JSONB       NOT NULL DEFAULT '{}',   -- {x, y, w, h} for gridstack
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_widget_configs_dashboard ON widget_configs (dashboard_layout_id);
CREATE INDEX IF NOT EXISTS idx_widget_configs_user      ON widget_configs (user_id);


-- ---------------------------------------------------------------------------
-- 6. STRATEGY CONFIGS  (extend skeleton)
-- ---------------------------------------------------------------------------

ALTER TABLE strategy_configs
    ADD COLUMN IF NOT EXISTS description TEXT,
    ADD COLUMN IF NOT EXISTS created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW();

CREATE INDEX IF NOT EXISTS idx_strategy_configs_user ON strategy_configs (user_id);


-- ---------------------------------------------------------------------------
-- 7. BACKTEST RUNS  (new)
--    Each run is a snapshot execution of a strategy config. Results are
--    stored as JSONB (CAGR, Sharpe, Sortino, max_dd, trade list, etc.)
--    so the schema does not need to change as metrics evolve.
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS backtest_runs (
    id                  UUID        PRIMARY KEY,
    user_id             UUID        NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    strategy_config_id  UUID        REFERENCES strategy_configs (id) ON DELETE SET NULL,
    portfolio_id        UUID        REFERENCES portfolios (id) ON DELETE SET NULL,
    status              TEXT        NOT NULL DEFAULT 'pending'
                                    CHECK (status IN ('pending', 'running', 'complete', 'failed')),
    params_json         JSONB       NOT NULL DEFAULT '{}',  -- parameter snapshot for this run
    results_json        JSONB,                              -- populated on completion
    error_message       TEXT,
    started_at          TIMESTAMPTZ,
    finished_at         TIMESTAMPTZ,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_backtest_runs_user     ON backtest_runs (user_id);
CREATE INDEX IF NOT EXISTS idx_backtest_runs_strategy ON backtest_runs (strategy_config_id);


-- ---------------------------------------------------------------------------
-- 8. ALERTS  (extend skeleton)
--    The skeleton has (alert_type, message). Extend with symbol context,
--    a structured condition, and lifecycle status.
-- ---------------------------------------------------------------------------

ALTER TABLE alerts
    ADD COLUMN IF NOT EXISTS symbol         TEXT,
    ADD COLUMN IF NOT EXISTS exchange       TEXT,
    ADD COLUMN IF NOT EXISTS condition_json JSONB,
    ADD COLUMN IF NOT EXISTS status         TEXT        NOT NULL DEFAULT 'active'
                                            CHECK (status IN ('active', 'triggered', 'dismissed')),
    ADD COLUMN IF NOT EXISTS triggered_at   TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS resolved_at    TIMESTAMPTZ;

CREATE INDEX IF NOT EXISTS idx_alerts_user        ON alerts (user_id);
CREATE INDEX IF NOT EXISTS idx_alerts_user_status ON alerts (user_id, status);
