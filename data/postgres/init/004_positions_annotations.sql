-- =============================================================================
-- 004_positions_annotations.sql
-- Adds the two tables missing from 003_app_schema.sql:
--   positions   — individual holdings in a portfolio (required by §4 analytics)
--   annotations — user chart notes/trendlines tied to symbol + timestamp (§3)
-- =============================================================================


-- ---------------------------------------------------------------------------
-- 1. POSITIONS
--    One row per open or closed lot. A portfolio can have many positions in
--    the same symbol (e.g. multiple buy lots at different dates/prices).
--    closed_at = NULL means the position is still open.
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS positions (
    id              UUID            PRIMARY KEY,
    portfolio_id    UUID            NOT NULL REFERENCES portfolios (id) ON DELETE CASCADE,
    user_id         UUID            NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    symbol          TEXT            NOT NULL,
    exchange        TEXT            NOT NULL,
    asset_class     TEXT            NOT NULL DEFAULT 'equity',
    quantity        DOUBLE PRECISION NOT NULL,          -- shares / units held
    cost_basis      DOUBLE PRECISION NOT NULL,          -- per-unit cost in `currency`
    currency        TEXT            NOT NULL DEFAULT 'USD',
    opened_at       TIMESTAMPTZ     NOT NULL,
    closed_at       TIMESTAMPTZ,                        -- NULL = open position
    notes           TEXT,
    created_at      TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ     NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_positions_portfolio  ON positions (portfolio_id);
CREATE INDEX IF NOT EXISTS idx_positions_user       ON positions (user_id);
CREATE INDEX IF NOT EXISTS idx_positions_symbol     ON positions (user_id, symbol, exchange);
CREATE INDEX IF NOT EXISTS idx_positions_open       ON positions (user_id, closed_at)
    WHERE closed_at IS NULL;


-- ---------------------------------------------------------------------------
-- 2. ANNOTATIONS
--    User-drawn chart markings tied to a symbol and a point or range in time.
--    Stored in the Brain and served via the API; rendered by the Skin adapter.
--    annotation_type drives how the Skin renders the shape:
--      note       — a text callout at a single timestamp
--      trendline  — a line between (timestamp_start, price_start) and
--                   (timestamp_end, price_end)
--      horizontal — a horizontal price level across the full chart
--      vertical   — a vertical time marker at timestamp_start
--      rectangle  — a shaded box over a time + price range
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS annotations (
    id              UUID        PRIMARY KEY,
    user_id         UUID        NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    symbol          TEXT        NOT NULL,
    exchange        TEXT        NOT NULL,
    annotation_type TEXT        NOT NULL
                                CHECK (annotation_type IN
                                    ('note', 'trendline', 'horizontal', 'vertical', 'rectangle')),
    timestamp_start TIMESTAMPTZ NOT NULL,
    timestamp_end   TIMESTAMPTZ,                    -- NULL for point annotations
    price_start     DOUBLE PRECISION,
    price_end       DOUBLE PRECISION,
    label           TEXT,
    color           TEXT        NOT NULL DEFAULT '#2196F3',
    data_json       JSONB       NOT NULL DEFAULT '{}',
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_annotations_user        ON annotations (user_id);
CREATE INDEX IF NOT EXISTS idx_annotations_user_symbol ON annotations (user_id, symbol, exchange);
