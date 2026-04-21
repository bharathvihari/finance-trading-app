# Code Flow & Data Flow Reference

This document explains what each part of the codebase does, how data moves through the system,
and how the major flows are implemented end-to-end. Updated after each implementation phase.

---

## Table of Contents

1. [System Map](#1-system-map)
2. [Data Ingestion Flow — Backfill](#2-data-ingestion-flow--backfill)
3. [Data Ingestion Flow — Daily Incremental](#3-data-ingestion-flow--daily-incremental)
4. [Cold Archival Flow](#4-cold-archival-flow)
5. [API Request Lifecycle](#5-api-request-lifecycle)
6. [Bar Data Tiered Query Flow](#6-bar-data-tiered-query-flow)
7. [Auth Flow](#7-auth-flow)
8. [Dashboard & Widget CRUD Flow *(Phase 2)*](#8-dashboard--widget-crud-flow)
9. [Indicator Calculation Flow *(Phase 3)*](#9-indicator-calculation-flow)
10. [Corporate Events Flow *(Phase 3)*](#10-corporate-events-flow)
11. [Annotations CRUD Flow *(Phase 3)*](#11-annotations-crud-flow)
12. [Portfolio & Performance Analytics Flow *(Phase 4)*](#12-portfolio--performance-analytics-flow)
13. [Backtest & Strategy Views Flow *(Phase 5)*](#13-backtest--strategy-views-flow)
14. [Caching, ARQ Jobs & Pre-aggregated Metrics *(Phase 6)*](#14-caching-arq-jobs--pre-aggregated-metrics)
15. [WebSocket Live Feeds *(Phase 7)*](#15-websocket-live-feeds)
16. [Postgres Schema Reference](#16-postgres-schema-reference)
17. [Module Reference](#17-module-reference)

---

## 1. System Map

```
┌─────────────────────────────────────────────────────────────────────┐
│                          IBKR Client Portal                         │
└───────────────────────────────┬─────────────────────────────────────┘
                                │  NautilusTrader adapter
                                ▼
┌─────────────────────────────────────────────────────────────────────┐
│                    apps/workers/  (Python jobs)                     │
│                                                                     │
│  jobs/backfill.py          full historical download, restartable    │
│  jobs/daily_refresh.py     incremental update after market close    │
│  jobs/archive_cold_bars.py drain Postgres hot → Parquet cold        │
│  jobs/fetch_events.py      yfinance → corporate_events table        │
│                                                                     │
│  market_data/                                                       │
│    ibkr_client.py     fetch from IBKR via NautilusTrader            │
│    parquet_store.py   hive-partitioned Parquet read/write           │
│    postgres_store.py  hot-tier read/write/delete                    │
│    duckdb_meta.py     job metadata & slice-progress tracking        │
│    windowing.py       year/page time-window generators              │
│    dedup.py           timestamp deduplication                       │
│    universe_loader.py symbol list from config                       │
│    logger.py          structured JSONL logging                      │
└───────────────────┬────────────────────────┬────────────────────────┘
                    │ writes market data      │ reads yfinance events
          ┌─────────┴──────────┐             │
          ▼                    ▼             ▼
  ┌───────────────────────────────────────────────────────────────────┐
  │                        PostgreSQL 18                              │
  │                                                                   │
  │  public schema (application data)                                 │
  │    users                  id, email, password_hash, preferences   │
  │    broker_accounts        per-user broker connections             │
  │    portfolios             live / paper / demo / imported          │
  │    positions              individual holdings per portfolio        │
  │    dashboard_layouts      user-defined dashboard configs          │
  │    widget_configs         per-widget type, config, position       │
  │    strategy_configs       NautilusTrader strategy params          │
  │    backtest_runs          run records + results JSON              │
  │    alerts                 price/event alert definitions           │
  │    annotations            user chart trendlines and notes         │
  │                                                                   │
  │  market_data schema (time-series hot tier)                        │
  │    daily_bars             recent N months of OHLCV bars           │
  │    corporate_events       dividends, splits, earnings dates        │
  └────────────────────────────────────┬──────────────────────────────┘
                                       │
          ┌────────────────────────────┘
          ▼
  ┌────────────────────────────────────────────────────────────────┐
  │  Parquet files — cold tier (hive partitioned)                  │
  │  data/parquet/price-data/                                      │
  │    asset_class=equity/exchange=NASDAQ/frequency=daily/         │
  │      year=2023/part-20231001T120000-a1b2c3d4.parquet           │
  └──────────────────────────────┬─────────────────────────────────┘
                                 │ queried by
                        ┌────────┴────────┐
                        │     DuckDB      │
                        │  (query engine  │
                        │  + metadata DB) │
                        └────────┬────────┘
                                 │
  ┌──────────────────────────────┘
  │ reads (tiered hot + cold)
  ▼
┌─────────────────────────────────────────────────────────────────────┐
│                      apps/api/  (FastAPI)                           │
│                                                                     │
│  main.py               router registration, WebSocket endpoint     │
│  auth/                 JWT, bcrypt, get_current_user dependency    │
│  db/connection.py      psycopg2 connection (Depends)               │
│  lib/bar_reader.py     tiered hot+cold OHLCV query and merge       │
│  lib/indicators.py     pure Python: SMA/EMA/WMA/BB/RSI/MACD/ATR   │
│                                                                     │
│  api/routes/                                                        │
│    auth.py             register, login                             │
│    users.py            /me profile + preferences                   │
│    bars.py             OHLCV candlestick data (tiered)             │
│    indicators.py       technical indicator series                  │
│    events.py           corporate event markers                     │
│    dashboards.py       dashboard + widget CRUD                     │
│    annotations.py      user chart annotations CRUD                 │
│    strategy_control.py start/stop strategies                       │
│                                                                     │
│  api/schemas/                                                       │
│    bars.py             Bar, EventMarker, ChartSeries (§8 contract) │
│    auth.py             TokenResponse, RegisterRequest, etc.        │
│    users.py            UserResponse, UpdateUserRequest             │
│    dashboards.py       DashboardDetailResponse, WidgetResponse      │
│    annotations.py      AnnotationResponse, CreateAnnotationRequest  │
│                                                                     │
│  ws/manager.py         WebSocket channel fan-out                   │
└───────────────────────────────┬─────────────────────────────────────┘
                                │ HTTP / WebSocket
                                ▼
┌─────────────────────────────────────────────────────────────────────┐
│                   apps/web/  (Next.js + React + TypeScript)         │
│                                                                     │
│  TradingView Lightweight Charts  chart rendering (ChartSeries)     │
│  react-grid-layout              drag / drop / resize panels        │
│  WebSocket client               live price / PnL feeds             │
└─────────────────────────────────────────────────────────────────────┘
```

---

## 2. Data Ingestion Flow — Backfill

**Entry point:** `apps/workers/jobs/backfill.py::run_backfill()`
**Config:** `config/market_data.backfill.yaml`

```
run_backfill()
│
├── load_job_config()              reads YAML → JobConfig (Pydantic)
├── DuckDbMetaStore(duckdb_path)   opens/creates metadata DB
├── IbkrHistoricalClient           wraps NautilusTrader + rate limiter
├── ParquetStore(parquet_root)     cold-tier writer
├── PostgresBarStore               hot-tier writer (if postgres.enabled)
│
├── _hot_window_cutoff()           cutoff = first of month (now − hot_window_months)
├── meta.start_job_run()           inserts row into duckdb job_runs
│
└── for instrument in universe.instruments:   (priority tickers first)
    │
    ├── ibkr_client.get_head_timestamp()     earliest IBKR date for this instrument
    │
    └── for window in yearly_windows_newest_to_oldest(head, now):
        │   iterates: 2026-window, 2025-window, 2024-window, …
        │
        └── _process_year_window()
            │
            ├── meta.get_slice_state()        COMPLETE? → skip entire year
            ├── meta.upsert_slice_progress()  mark IN_PROGRESS
            │
            └── for page in paginated_windows_backward(window, page_span=1300d):
                │
                ├── ibkr_client.fetch_bars()          IBKR API call → list[dict]
                ├── deduplicate_bars()                 remove dupes by timestamp
                ├── _split_hot_cold_frames(hot_cutoff)
                │     hot_frame  = rows with timestamp >= cutoff
                │     cold_frame = rows with timestamp <  cutoff
                │
                ├── hot_store.upsert_bars(hot_frame)
                │     INSERT INTO market_data.daily_bars … ON CONFLICT DO UPDATE
                │
                ├── parquet_store.write_partition(cold_frame)
                │     writes data/parquet/price-data/asset_class=…/year=…/part-*.parquet
                │
                └── meta.upsert_slice_progress()      save min/max timestamps
            │
            └── meta.upsert_slice_progress(status=COMPLETE)
```

**Rate limiting:** token-bucket inside `IbkrHistoricalClient` with jitter and exponential
backoff. Targets ~65% of IBKR's 60-requests-per-10-min limit.

**Restartability:** `get_slice_state()` detects COMPLETE slices and skips them. Partial slices
resume from `earliest_downloaded_ts`.

**Combo sync watermark:** backfill updates `exchange_trading_dates` at
`(exchange, frequency, asset_class)` level with the latest common parquet-sync
date (`last_traded_ts`) after processing symbols.

---

## 3. Data Ingestion Flow — Daily Incremental

**Entry point:** `apps/workers/jobs/daily_refresh.py`

Same structure as backfill but requests only the gap from the latest stored bar to now.

```
for instrument in universe:
    latest_parquet = parquet_store.latest_timestamp(symbol, exchange, frequency)
    latest_hot     = hot_store.latest_timestamp(symbol, exchange, asset_class, frequency)
    latest_local   = max(latest_parquet, latest_hot)

    fetch IBKR bars: (latest_local + 1 day) → now
    split hot/cold on cutoff and write (same as backfill)
```

Scheduled daily after market close via cron or ARQ.

---

## 4. Cold Archival Flow

**Entry point:** `apps/workers/jobs/archive_cold_bars.py::run_archive()`
**Config:** `config/market_data.archive.yaml`
**Run frequency:** Weekly (or on-demand).

```
run_archive()
│
├── _hot_cutoff()    same formula as backfill — must match exactly
│
├── hot_store.fetch_cold_partition_keys(cutoff)
│     SELECT asset_class, exchange, frequency,
│            EXTRACT(YEAR FROM timestamp)::INT, COUNT(*)
│     FROM market_data.daily_bars WHERE timestamp < cutoff
│     GROUP BY …
│     → list of {asset_class, exchange, frequency, year, row_count}
│
└── for partition in partitions:
    │
    ├── hot_store.read_bars_for_partition()
    │     SELECT … WHERE asset_class=? AND … AND timestamp < cutoff
    │
    ├── parquet_store.write_partition(df)
    │     → new part-*.parquet file under the correct year directory
    │     (crash here = rows still in Postgres, safe to re-run)
    │
    └── hot_store.delete_bars_for_partition()
          DELETE … WHERE asset_class=? AND … AND timestamp < cutoff
          (only runs AFTER successful Parquet write)
```

After each successful partition archive, the job also updates DuckDB metadata:
- `meta.upsert_parquet_symbol(...)` for every symbol written to Parquet.
- `meta.upsert_coverage(...)` with archived min/max timestamps per symbol.
- `meta.upsert_exchange_last_traded_date(...)` for `(exchange, frequency, asset_class)`
  based on the latest common parquet-sync watermark.

**Safety invariant:** write → delete ordering. Duplicate Parquet rows from a partial run
are harmless — `BarReader` deduplicates on `(symbol, exchange, asset_class, frequency, timestamp)`.

---

## 5. API Request Lifecycle

Every protected HTTP request follows this chain:

```
HTTP Request  (Authorization: Bearer <jwt_token>)
│
▼
FastAPI matches route → handler function
│
├── HTTPBearer extracts token from Authorization header
├── get_current_user(credentials)
│     jose.jwt.decode(token, secret, algorithms=["HS256"])
│     → HTTP 401 if invalid / expired
│     → CurrentUser(id=UUID, email=str)
│
├── get_db()                  ← injected via Depends if route touches app tables
│     psycopg2.connect(postgres_*)
│     yield conn
│     commit on success / rollback on exception / always close
│
├── get_bar_reader()          ← injected via Depends if route reads market data
│     BarReader(parquet_root, hot_window_months, pg_*)
│
└── route handler executes
    ├── queries via BarReader.read() or conn.cursor().execute()
    ├── all user-data queries include WHERE user_id = %s
    ├── builds Pydantic response model (validation enforced)
    └── returns JSON
```

No DB query fires unless the JWT is valid. `user_id` from the token scopes every query —
no cross-user data leakage is possible (ADR-010).

---

## 6. Bar Data Tiered Query Flow

**Route:** `GET /api/v1/bars/{symbol}?exchange=NASDAQ&start=2020-01-01&end=2024-12-31`

```
bars.py::get_bars()
│
├── parse start/end → start_utc, end_utc (UTC datetimes)
│
└── BarReader.read(symbol, exchange, asset_class, frequency, start_utc, end_utc)
    │
    ├── cutoff = _hot_cutoff(hot_window_months)
    │           today=2026-04-16, hot_window_months=6 → cutoff=2025-10-01
    │
    ├── need_cold = (start_utc is None) or (start_utc < cutoff)
    ├── need_hot  = pg_enabled and ((end_utc is None) or (end_utc >= cutoff))
    │
    ├── [if need_cold]
    │     _read_cold(…, start_utc, cold_end=min(end_utc, cutoff))
    │       DuckDB: read_parquet('data/parquet/**/*.parquet', hive_partitioning=true)
    │               WHERE symbol=? AND exchange=? AND timestamp BETWEEN ? AND ?
    │
    ├── [if need_hot]
    │     _read_hot(…, hot_start=max(start_utc, cutoff), end_utc)
    │       psycopg2: SELECT … FROM market_data.daily_bars WHERE … AND timestamp BETWEEN ? AND ?
    │
    ├── pd.concat([cold_df, hot_df])
    ├── drop_duplicates(subset=[symbol, exchange, asset_class, frequency, timestamp])
    ├── sort_values("timestamp")
    └── → merged DataFrame

route handler: DataFrame rows → list[Bar(t, o, h, l, c)]
→ ChartSeries(symbol, exchange, …, series_type="candlestick", name="AAPL", bars=[…])
→ JSON response
```

**Routing table:**

| Request range | cutoff = 2025-10-01 | Stores queried |
|---|---|---|
| 2015-01-01 → 2020-12-31 | entirely before cutoff | Parquet / DuckDB only |
| 2025-11-01 → 2026-04-16 | entirely after cutoff | Postgres only |
| 2024-01-01 → 2026-04-16 | straddles cutoff | both, merged |

---

## 7. Auth Flow

### Registration

```
POST /api/v1/auth/register  {"email": "user@example.com", "password": "secret123"}
│
├── Pydantic: email format valid, password length ≥ 8
├── hash_password(plain) → bcrypt hash (passlib, cost=12)
├── uuid.uuid4() → new user_id
├── INSERT INTO users (id, email, password_hash) VALUES (…)
│     → HTTP 409 Conflict if email already exists (psycopg2.errors.UniqueViolation)
├── create_access_token(user_id, email)
│     jose.jwt.encode({sub: user_id, email: email, exp: now+30d}, secret, HS256)
└── → TokenResponse {access_token, token_type="bearer"}
```

### Login

```
POST /api/v1/auth/login  {"email": "user@example.com", "password": "secret123"}
│
├── SELECT id, email, password_hash FROM users WHERE email = %s
│     → HTTP 401 if not found
├── verify_password(plain, hashed)    passlib bcrypt.verify()
│     → HTTP 401 if mismatch
├── create_access_token(user_id, email)
└── → TokenResponse
```

### Protected request (every subsequent call)

```
Authorization: Bearer <token>
│
├── HTTPBearer extracts token string
├── jose.jwt.decode(token, secret) → payload {sub, email, exp}
│     → HTTP 401 on JWTError (expired, bad signature, malformed)
└── CurrentUser(id=UUID(payload["sub"]), email=payload["email"])
      injected into handler via Depends(get_current_user)
```

### User preferences update

```
PATCH /api/v1/users/me  {"base_currency": "EUR", "display_tz": "Europe/London"}
│
├── get_current_user → CurrentUser
├── build SET clause only for non-None fields
│     prevents PATCH from overwriting unchanged preferences with nulls
├── UPDATE users SET base_currency=%s, display_tz=%s WHERE id=%s
└── SELECT updated row → UserResponse
```

---

## 8. Dashboard & Widget CRUD Flow

**Phase 2** — `apps/api/app/api/routes/dashboards.py`

All dashboard and widget data lives in Postgres (`public` schema).
Every query scopes by `user_id` (ADR-010).

### Create a dashboard

```
POST /api/v1/dashboards  {"name": "My Watchlist", "layout_json": {}}
│
├── get_current_user → CurrentUser
├── uuid.uuid4() → dashboard_id
├── INSERT INTO dashboard_layouts (id, user_id, name, layout_json)
└── → DashboardSummaryResponse {id, name, widget_count=0, created_at, updated_at}
```

### List dashboards

```
GET /api/v1/dashboards
│
├── get_current_user → CurrentUser
├── SELECT d.*, COUNT(w.id) AS widget_count
│   FROM dashboard_layouts d
│   LEFT JOIN widget_configs w ON w.dashboard_layout_id = d.id
│   WHERE d.user_id = %s
│   GROUP BY d.id  ORDER BY d.updated_at DESC
└── → list[DashboardSummaryResponse]   (no widgets embedded — faster list view)
```

### Get dashboard with widgets

```
GET /api/v1/dashboards/{id}
│
├── _get_dashboard_or_404(conn, dashboard_id, user_id)
│     SELECT … FROM dashboard_layouts WHERE id=%s AND user_id=%s
│     → HTTP 404 if missing or owned by someone else
├── _fetch_widgets(conn, dashboard_id)
│     SELECT … FROM widget_configs WHERE dashboard_layout_id=%s ORDER BY created_at
└── → DashboardDetailResponse {id, name, layout_json, widgets: [WidgetResponse, …]}
```

### Update widget position (called on every drag/resize)

```
PATCH /api/v1/dashboards/{id}/widgets/{widget_id}
      {"position_json": {"x": 2, "y": 0, "w": 4, "h": 6}}
│
├── _get_dashboard_or_404()           ownership check
├── build SET only for non-None fields (position_json, config_json, or title)
├── UPDATE widget_configs SET position_json=%s, updated_at=NOW()
│   WHERE id=%s AND dashboard_layout_id=%s
└── SELECT updated row → WidgetResponse
```

**Why per-field PATCH?** The frontend sends position updates on every mouse-up event.
Sending the full widget record would risk overwriting `config_json` with stale client state.
Only the changed field is in the request body.

### Delete dashboard (cascade)

```
DELETE /api/v1/dashboards/{id}
│
├── _get_dashboard_or_404()   ownership check
├── DELETE FROM dashboard_layouts WHERE id=%s
│     → CASCADE removes all child widget_configs rows automatically (FK)
└── HTTP 204 No Content
```

---

## 9. Indicator Calculation Flow

**Phase 3** — `apps/api/app/lib/indicators.py` + `apps/api/app/api/routes/indicators.py`

### Architecture: pure functions + thin route

`indicators.py` contains only pure Python functions. No I/O, no FastAPI, no state.
Each function takes a `pd.Series` (or DataFrame for ATR), returns a Series/DataFrame
aligned to the input index. NaN appears during the warm-up period.

```
GET /api/v1/indicators/AAPL?exchange=NASDAQ&indicator=SMA&period=50&start=2024-01-01
│
├── get_current_user → CurrentUser
│
├── lookback_days = max(period, slow) * 3   (ensures warm-up before `start`)
│   fetch_start   = start_utc − lookback_days
│
├── BarReader.read(…, start_utc=fetch_start, end_utc=end_utc)
│     same tiered hot+cold path as bars endpoint
│     → DataFrame with [timestamp, open, high, low, close, volume]
│
├── df.set_index("timestamp")
├── close = df["close"]
│
├── [SMA]   indicators.sma(close, period=50)
│             close.rolling(window=50, min_periods=50).mean()
│             → pd.Series (NaN for first 49 rows)
│
├── _series_to_chart(series, name="SMA(50)", series_type="line", …)
│     for ts, val in zip(index, series):
│         if ts < start_utc: skip   ← trim warm-up rows from response
│         if isnan(val):     skip
│         bars.append(Bar(t=ts, v=val))
│
└── → list[ChartSeries]  (one item for single-output indicators)
```

### Multi-output indicators (BB, MACD)

```
GET /api/v1/indicators/AAPL?indicator=MACD&fast=12&slow=26&signal=9
│
├── indicators.macd(close, fast=12, slow=26, signal=9)
│     ema_fast = close.ewm(span=12).mean()
│     ema_slow = close.ewm(span=26).mean()
│     macd_line    = ema_fast − ema_slow
│     signal_line  = macd_line.ewm(span=9).mean()
│     histogram    = macd_line − signal_line
│     → DataFrame {macd, macd_signal, macd_histogram}
│
└── → list[ChartSeries] with 3 items:
      ChartSeries(name="MACD(12,26)",  series_type="line",      bars=[…])
      ChartSeries(name="Signal(9)",    series_type="line",      bars=[…])
      ChartSeries(name="Histogram(…)", series_type="histogram", bars=[…])
```

### ChartSeries unified schema (§8 Brain↔Skin contract)

```python
class Bar(BaseModel):
    t: datetime
    v: float | None = None  # line / area / histogram value
    o: float | None = None  # candlestick open
    h: float | None = None  # candlestick high
    l: float | None = None  # candlestick low
    c: float | None = None  # candlestick close

class EventMarker(BaseModel):
    t: datetime
    marker_type: "dividend" | "split" | "earnings" | "trade"
    label: str
    color: str

class ChartSeries(BaseModel):
    symbol, exchange, asset_class, frequency: str
    series_type: "candlestick" | "line" | "area" | "histogram"
    name: str        # display label e.g. "SMA(50)", "MACD(12,26)", "AAPL"
    color: str|None
    bars: list[Bar]
    markers: list[EventMarker]   # overlaid on any series_type
```

| series_type | Bar fields used | TradingView API |
|---|---|---|
| `candlestick` | `o, h, l, c` | `createCandlestickSeries()` |
| `line` | `v` | `createLineSeries()` |
| `area` | `v` | `createAreaSeries()` |
| `histogram` | `v` | `createHistogramSeries()` |

---

## 10. Corporate Events Flow

**Phase 3** — `apps/workers/jobs/fetch_events.py` + `apps/api/app/api/routes/events.py`

Events are pre-fetched by a worker and stored in Postgres. The API reads from the table — no
live yfinance calls in the request path (would be too slow at ~1-2s per symbol).

### Worker: populate events table

```
python -m jobs.fetch_events  (scheduled weekly or on-demand)
│
├── load universe from config (same symbol list as backfill)
│
└── for instrument in universe:
    │
    ├── yf.Ticker(symbol).dividends
    │     → pd.Series {date: amount}
    │     INSERT INTO market_data.corporate_events
    │       (symbol, exchange, event_type='dividend', event_date, value=amount)
    │     ON CONFLICT DO UPDATE
    │
    ├── yf.Ticker(symbol).splits
    │     → pd.Series {date: ratio}
    │     INSERT … (event_type='split', value=ratio)
    │
    └── yf.Ticker(symbol).get_earnings_dates(limit=20)
          → DataFrame with earnings timestamps
          INSERT … (event_type='earnings')
```

### API: serve events as markers

```
GET /api/v1/events/AAPL?exchange=NASDAQ&types=dividend,split&start=2020-01-01
│
├── get_current_user
├── SELECT event_type, event_date, value, description
│   FROM market_data.corporate_events
│   WHERE symbol='AAPL' AND exchange='NASDAQ'
│     AND event_type = ANY(['dividend','split'])
│     AND event_date BETWEEN start AND end
│   ORDER BY event_date ASC
│
└── → list[EventMarker]
      EventMarker(t=2023-02-03, marker_type="dividend", label="Div $0.23", color="#4CAF50")
      EventMarker(t=2020-08-31, marker_type="split",    label="Split 4:1", color="#2196F3")
```

The frontend attaches these markers to a `ChartSeries` via TradingView's `setMarkers()` API.
Markers can also be embedded directly in a `ChartSeries.markers` field when the bars and
events are fetched together.

---

## 11. Annotations CRUD Flow

**Phase 3** — `apps/api/app/api/routes/annotations.py`

User-drawn chart annotations (trendlines, notes, horizontal levels, etc.) are stored per-user
in Postgres. The API is a standard CRUD wrapper — ownership enforced by `WHERE user_id = %s`.

### Create

```
POST /api/v1/annotations
{
  "symbol": "AAPL", "exchange": "NASDAQ",
  "annotation_type": "trendline",
  "timestamp_start": "2023-01-10T00:00:00Z",
  "timestamp_end":   "2023-06-15T00:00:00Z",
  "price_start": 130.5, "price_end": 178.2,
  "label": "Uptrend", "color": "#2196F3"
}
│
├── get_current_user → CurrentUser
├── uuid.uuid4() → annotation_id
├── INSERT INTO annotations (id, user_id, symbol, exchange, annotation_type,
│     timestamp_start, timestamp_end, price_start, price_end, label, color, data_json)
└── → AnnotationResponse
```

### List (by symbol)

```
GET /api/v1/annotations?symbol=AAPL&exchange=NASDAQ
│
├── SELECT … FROM annotations
│   WHERE user_id=%s AND symbol='AAPL' AND exchange='NASDAQ'
│   ORDER BY timestamp_start ASC
└── → list[AnnotationResponse]
```

### Update (e.g. user moves a trendline endpoint)

```
PATCH /api/v1/annotations/{id}  {"price_end": 185.0}
│
├── build SET clause for non-None fields only
├── UPDATE annotations SET price_end=%s, updated_at=NOW()
│   WHERE id=%s AND user_id=%s        ← ownership check in WHERE clause
└── SELECT updated row → AnnotationResponse (HTTP 404 if not found or wrong user)
```

### Annotation types and their data shape

| annotation_type | timestamp_end | price_start | price_end | Notes |
|---|---|---|---|---|
| `note` | — | — | — | Text callout at a point |
| `trendline` | ✓ | ✓ | ✓ | Line between two price-time points |
| `horizontal` | — | ✓ | — | Horizontal price level across full chart |
| `vertical` | — | — | — | Vertical time marker |
| `rectangle` | ✓ | ✓ | ✓ | Shaded box over a time+price range |

---

## 12. Portfolio & Performance Analytics Flow

**Phase 4** — `apps/api/app/lib/metrics.py`, `apps/api/app/api/routes/portfolios.py`, `apps/api/app/api/routes/broker_accounts.py`

### Data model

```
broker_accounts  (ibkr | paper | demo | manual)
    └── portfolios  (live | paper | demo | imported)
            └── positions  (symbol, quantity, cost_basis, opened_at, closed_at)
```

Each portfolio has many positions. A position is a single buy lot. Closing a position means
setting `closed_at`; the row is retained for realized P&L history.

### Broker account and portfolio CRUD

Standard user-scoped CRUD — all queries include `WHERE user_id = %s` enforced by
`get_current_user`. The `portfolios` table has a partial unique index:
`UNIQUE (user_id) WHERE is_default = TRUE` — creating/updating a portfolio as default
first clears the old default via `UPDATE portfolios SET is_default = FALSE WHERE user_id = %s`.

### Portfolio overview flow

```
GET /api/v1/portfolios/{id}/overview
│
├── _get_portfolio_or_404()          ownership check
├── _fetch_open_positions()          SELECT … WHERE portfolio_id=%s AND closed_at IS NULL
│
├── for each open position:
│     reader.latest_price(symbol, exchange, asset_class)
│       → BarReader.read(start=now-10d)  last 10 days is enough to find latest bar
│       → float(df["close"].iloc[-1])   most recent close
│
│     current_value  = quantity × latest_price
│     unrealized_pnl = current_value − (quantity × cost_basis)
│     pnl_pct        = unrealized_pnl / (quantity × cost_basis)
│
├── total_cost  = sum(quantity × cost_basis)  for all open positions
├── total_value = sum(quantity × latest_price) for positions with a price
│
├── [if open positions exist] build_portfolio_curve(pos_dicts, reader)
│     → pd.Series {timestamp → total_value}
│     [if len(curve) >= 30 bars]
│       metrics.compute_metrics(curve)
│         → {total_return, cagr, max_drawdown, volatility, sharpe, sortino}
│
└── → PortfolioOverviewResponse
```

### Metrics computation (lib/metrics.py)

All functions are pure — no I/O, no state, accept a `pd.Series` and return a `float`.

| Function | Formula |
|---|---|
| `total_return` | `(end − start) / start` |
| `cagr` | `(end/start)^(1/years) − 1` |
| `max_drawdown` | `min((value − cummax) / cummax)` over the series |
| `volatility` | `std(daily_returns) × √252` |
| `sharpe` | `mean(excess_returns) / std(returns) × √252` |
| `sortino` | `mean(excess_returns) / std(negative_returns) × √252` |

`compute_metrics(values)` runs all of the above in one call and returns a dict.

### Equity curve flow

```
GET /api/v1/portfolios/{id}/curve?start=2023-01-01&benchmark=SPX&normalize=true
│
├── _fetch_open_positions()
│
├── build_portfolio_curve(pos_dicts, reader, start_utc, end_utc)
│     for each position:
│       reader.read(symbol, …, start_utc, end_utc)  → DataFrame
│       df["close"] × quantity  → value series for this position
│     pd.DataFrame(all_value_series)
│     .ffill()          ← fill weekends/holidays for cross-exchange positions
│     .dropna(how="all")
│     .sum(axis=1)      ← total portfolio value per day
│     → pd.Series {timestamp → total_value}
│
├── [if normalize=true]  equity_curve(series, base=100)
│     → series / series.iloc[0] * 100   (all curves start at 100)
│
├── → ChartSeries(name=portfolio_name, series_type="line", bars=[Bar(t,v), …])
│
├── [if benchmark param provided]
│     reader.read(benchmark, benchmark_exchange, …)
│     [if normalize] equity_curve(close_series)
│     → ChartSeries(name="SPX", series_type="line", color="#FF9800", bars=[…])
│
└── → list[ChartSeries]   portfolio curve + optional benchmark
      Frontend overlays both on the same chart panel for visual comparison.
```

**Why forward-fill?** Different exchanges have different trading calendars. A portfolio
holding AAPL (NASDAQ) and a European ETF will have days where only one has a price.
`ffill()` propagates the last known price forward so the portfolio value series is
continuous — the same assumption TradingView's chart engine makes.

---

## 13. Backtest & Strategy Views Flow

**Phase 5** — `apps/api/app/api/routes/strategies.py`, `apps/api/app/api/routes/backtests.py`

### Separation of concerns

| Concern | File | What it does |
|---|---|---|
| Strategy **config** CRUD | `strategies.py` | Stores/reads NautilusTrader strategy params in `strategy_configs` |
| Live **control** (start/stop) | `strategy_control.py` | Talks to NautilusTrader event bus (placeholder wiring pending) |
| Backtest **run** lifecycle | `backtests.py` | Creates run records, accepts results from trading engine, serves curves |

### Strategy config flow

```
POST /api/v1/strategies  {"strategy_name": "MACross", "mode": "backtest", "config_json": {...}}
│
├── INSERT INTO strategy_configs (id, user_id, strategy_name, mode, config_json, description)
└── → StrategyResponse

PATCH /api/v1/strategies/{id}  {"is_active": true}
│
├── UPDATE strategy_configs SET is_active=true WHERE id=%s AND user_id=%s
└── → StrategyResponse (re-fetched)
```

### Backtest run lifecycle

```
1. USER creates run record
   POST /api/v1/backtests  {"strategy_config_id": "...", "params_json": {...}}
   │
   ├── INSERT INTO backtest_runs (id, user_id, strategy_config_id, status='pending', params_json)
   └── → BacktestRunResponse {id, status="pending"}

2. TRADING ENGINE picks up the pending run, executes NautilusTrader backtest,
   then submits results:
   PATCH /api/v1/backtests/{id}/results
   {
     "status": "complete",
     "results_json": {
       "summary": {"cagr": 0.18, "sharpe": 1.23, "max_drawdown": -0.12, ...},
       "equity_curve":   [{"t": "2023-01-01T00:00:00Z", "v": 100.0}, ...],
       "drawdown_curve": [{"t": "2023-01-01T00:00:00Z", "v":  0.0},  ...],
       "trades":         [{"symbol": "AAPL", "side": "buy", ...}, ...]
     },
     "started_at": "...", "finished_at": "..."
   }
   │
   └── UPDATE backtest_runs SET status='complete', results_json=%s, finished_at=%s

3. DASHBOARD reads results
   GET /api/v1/backtests/{id}           → full record with results_json + extracted summary
   GET /api/v1/backtests/{id}/curve     → equity curve + drawdown curve as list[ChartSeries]
```

### Equity curve endpoint

```
GET /api/v1/backtests/{id}/curve?normalize=true
│
├── fetch run row (ownership check)
├── results_json["equity_curve"] → list of {t, v}
│     → ChartSeries(series_type="line",      name="Equity Curve", color="#2196F3")
│
├── results_json["drawdown_curve"] → list of {t, v}  (negative values)
│     → ChartSeries(series_type="histogram", name="Drawdown",     color="#F44336")
│
└── → list[ChartSeries]  (2 items — frontend stacks them vertically)

[if normalize=true]
  equity bars: v = v / bars[0].v * 100   (starts at 100)
  drawdown bars: unchanged (already expressed as drawdown depth)
```

### Strategy comparison endpoint

```
GET /api/v1/backtests/compare?ids=uuid1,uuid2,uuid3&normalize=true
│
├── NOTE: /compare is declared BEFORE /{id} in the router to avoid
│         FastAPI treating "compare" as a path parameter
│
├── for each run_id in ids (max 10):
│     fetch run (ownership check)
│     extract results_json["equity_curve"]
│     [if normalize] scale to base=100
│     ChartSeries(name=label, color=colors[i], bars=[...])
│
└── → list[ChartSeries]   one per run, overlaid on same chart panel
```

### results_json contract (written by NautilusTrader adapter)

```
{
  "summary": {
    "total_return": float,   "cagr": float,
    "sharpe": float,         "sortino": float,
    "max_drawdown": float,   "volatility": float,
    "hit_rate": float,       "total_trades": int
  },
  "equity_curve":   [ {"t": "<ISO UTC>", "v": <float>}, ... ],
  "drawdown_curve": [ {"t": "<ISO UTC>", "v": <float>}, ... ],
  "trades": [
    { "symbol": str, "side": "buy"|"sell",
      "qty": float, "price": float,
      "ts": "<ISO UTC>", "pnl": float },
    ...
  ]
}
```

The `summary` block maps directly to `BacktestSummary` — extracted on every
`GET /backtests` list call without returning the full results_json (performance).
The full `results_json` is only returned on `GET /backtests/{id}`.

---

## 14. Caching, ARQ Jobs & Pre-aggregated Metrics

**Phase 6** — `apps/api/app/lib/cache.py`, `apps/api/app/lib/arq_pool.py`, `apps/workers/arq_worker.py`, `apps/workers/jobs/precompute_metrics.py`

### Redis bar cache (P6-1)

```
BarReader.read(symbol, exchange, …, start_utc, end_utc)
│
├── [cache enabled?]
│     cache_key = blake2b_hash(symbol:exchange:asset_class:frequency:start:end)
│     cached_df = RedisBarCache.get(cache_key)
│     → cache HIT: return immediately (no Parquet/Postgres I/O)
│
├── cache MISS → tiered read (cold + hot merge, same as before)
│
└── RedisBarCache.set(cache_key, combined_df, end_utc)
      TTL = SHORT_TTL (300s)  if end_utc is None or ≤ 2 days ago
      TTL = LONG_TTL  (3600s) if end_utc > 2 days ago  (historical, stable)
      Serialised as Parquet bytes via pyarrow (preserves all dtypes)
```

**Cache key** is a 12-byte blake2b hash of all range parameters — keeps Redis key space compact even with many unique date combinations.

**`latest_price()` bypasses the cache** — it always reads fresh data because it's used for live P&L calculations where staleness matters.

**Cache location** — `RedisBarCache` is created once at startup in the FastAPI lifespan context and stored in `app.state.bar_cache`. The `get_bar_reader()` dependency injects it via `request.app.state`.

### FastAPI startup / lifespan (cache + ARQ pool)

```python
@asynccontextmanager
async def lifespan(app: FastAPI):
    # Create Redis cache (sync client — thread-safe across route handler threads)
    redis_client = make_redis_client(settings.redis_host, settings.redis_port)
    app.state.bar_cache = RedisBarCache(redis_client) if redis_client else None

    # Create ARQ pool (async — used for enqueuing jobs)
    app.state.arq_pool = await create_arq_pool(settings.redis_host, settings.redis_port)

    yield

    await app.state.arq_pool.aclose()
```

Both are `None` if Redis is unavailable — the API degrades gracefully (no caching, no job enqueueing).

### On-demand IBKR fetch via ARQ (P6-2)

```
User dashboard requests AAPL data → GET /api/v1/bars/AAPL?exchange=NASDAQ
│
├── BarReader.read() → empty (no local data for this symbol)
│
├── Client calls POST /api/v1/jobs/fetch-bars
│     {"symbol": "AAPL", "exchange": "NASDAQ"}
│
├── arq_pool.enqueue_job("fetch_bars_task", symbol="AAPL", exchange="NASDAQ")
│     → job_id stored in Redis
│
└── HTTP 202 Accepted {"job_id": "abc123", "status": "queued"}

ARQ worker process (workers container)
│
├── picks up fetch_bars_task(ctx, symbol="AAPL", exchange="NASDAQ")
│     IbkrHistoricalClient.fetch_bars(…)   backfill all history
│     write hot/cold as normal backfill     Postgres + Parquet
│
└── job result stored in Redis (kept 24h)
      {"status": "complete", "symbol": "AAPL", "rows": 8765}

Client polls GET /api/v1/jobs/{job_id}
│
└── get_job_status(pool, job_id)
      arq.jobs.Job.status() → queued | in_progress | complete | failed
      arq.jobs.Job.result() → the returned dict (when complete)
```

### ARQ worker cron schedule

```
WorkerSettings.cron_jobs:
  daily_refresh_task   → runs at 22:00 UTC  (after US market close + buffer)
  precompute_metrics   → runs at 02:00 UTC  (after daily refresh completes)

Process:  cd apps/workers && python arq_worker.py
Docker:   workers service CMD ["python", "arq_worker.py"]
```

### Pre-aggregated rolling metrics (P6-3)

```
Nightly (02:00 UTC): precompute_metrics_task (ARQ cron)
│
├── for each instrument in universe:
│     read last 5Y of closes from BarReader (Parquet + Postgres)
│     for each window in [252, 504, 756, 1260]:
│       compute_metrics(closes.iloc[-window:])
│         → {total_return, cagr, max_drawdown, volatility, sharpe, sortino}
│       UPSERT INTO market_data.rolling_metrics
│         (symbol, exchange, …, as_of_date, window_days, cagr, sharpe, …)
│         ON CONFLICT DO UPDATE
│
└── total rows upserted ≈ universe_size × 4 windows

API read (< 10 ms, no computation):
GET /api/v1/market-data/metrics/AAPL?exchange=NASDAQ&windows=252,756
│
├── SELECT … FROM market_data.rolling_metrics
│   WHERE symbol='AAPL' AND exchange='NASDAQ'
│     AND as_of_date = (SELECT MAX(as_of_date) …)
│     AND window_days = ANY([252, 756])
│
└── → list[RollingMetricsResponse]
      [{window_days:252, window_label:"1Y", cagr:0.18, sharpe:1.23, …},
       {window_days:756, window_label:"3Y", cagr:0.14, sharpe:1.05, …}]
```

**Why pre-aggregate?** Computing Sharpe/Sortino over 5 years of daily bars across 20+ positions on every portfolio overview request would take 200–800 ms. Reading a row from Postgres takes < 5 ms. The nightly computation amortises the cost across all users.

---

## 15. WebSocket Live Update Flow *(planned — Phase 7)*

```
Trading engine / ingestion worker
│
└── redis.publish("prices:AAPL", {"symbol":"AAPL","close":189.50,"ts":"…"})

API server — background subscriber task (started on app startup)
│
├── redis.subscribe("prices:*")
└── on message:
    └── ConnectionManager.broadcast("prices:AAPL", payload)
        └── for ws in active_connections["prices:AAPL"]:
                await ws.send_json(payload)

Browser (Next.js dashboard widget)
│
├── useEffect → new WebSocket("wss://api.example.com/ws/prices:AAPL")
└── onmessage → append new bar to TradingView chart series
                update portfolio P&L display
```

`ConnectionManager` (`ws/manager.py`) is a dict of `{channel: [WebSocket]}`.
Widgets subscribe to the channel matching their ticker on mount and unsubscribe on unmount.

---

## 15. WebSocket Live Feeds

**Phase 7** — `apps/api/app/ws/`

### Architecture overview

```
Trading engine / daily refresh job
│
└── redis.publish("prices:AAPL:NASDAQ", json.dumps(tick))

API server — price_subscriber_loop (background asyncio task, started in lifespan)
│
├── redis.asyncio.pubsub.psubscribe("prices:*")
│
└── for each pmessage:
    ├── manager.broadcast("prices:AAPL:NASDAQ", tick)     ← chart live update
    ├── PnlTracker.on_tick("AAPL", "NASDAQ", 189.50)      ← portfolio P&L delta
    └── AlertEvaluator.on_tick("AAPL", "NASDAQ", 189.50)  ← condition evaluation

Browser (Next.js widget)
│
└── new WebSocket("wss://api/ws/prices:AAPL:NASDAQ?token=<jwt>")
    onmessage → append new bar to TradingView chart
```

### WebSocket connection + auth

```
ws://api/ws/{channel}?token=<jwt_access_token>
│
├── decode_access_token(token)   → 1008 close if invalid/expired
├── channel ownership check:
│     alerts:{user_id}           → JWT user_id must match channel user_id
│     portfolio:{portfolio_id}   → owner validated lazily on P&L push
│     prices:{sym}:{exch}        → any authenticated user
│
└── manager.connect(channel, websocket, user_id=current_user_id)
```

Browsers cannot set `Authorization` headers on WebSocket upgrades.
Passing the token as a query param is the standard workaround. The connection
is rejected with close code 1008 (Policy Violation) if the token is missing or invalid.

### Channel naming

| Channel | Producer | Consumer |
|---|---|---|
| `prices:{SYMBOL}:{EXCHANGE}` | Trading engine / refresh worker via `redis.publish` | Chart widgets that display live price |
| `portfolio:{portfolio_id}` | `PnlTracker` (on price tick) | Portfolio overview widget |
| `alerts:{user_id}` | `AlertEvaluator` (when condition fires) | Notification panel |

### P&L tracker flow (PnlTracker.on_tick)

```
price tick: AAPL = $189.50
│
├── asyncio.to_thread(_fetch_affected_positions, pg, "AAPL", "NASDAQ")
│     SELECT id, portfolio_id, user_id, quantity, cost_basis
│     FROM positions WHERE symbol='AAPL' AND closed_at IS NULL
│     → [{portfolio_id, quantity=100, cost_basis=165.0}, ...]
│
├── for each portfolio group:
│     current_value  = quantity × 189.50  = 18,950.0
│     cost_total     = quantity × 165.00  = 16,500.0
│     unrealized_pnl = 18,950 - 16,500   =  2,450.0
│     pnl_pct        = 2,450 / 16,500    =  14.85%
│
└── manager.broadcast("portfolio:{portfolio_id}", {
      "type":                     "pnl_update",
      "symbol":                   "AAPL",
      "current_price":            189.50,
      "position_updates":         [...],
      "portfolio_unrealized_pnl": 2450.0,
      "ts":                       "2026-04-17T21:30:00Z"
    })
```

### Alert evaluator flow (AlertEvaluator.on_tick)

```
price tick: AAPL = $201.50
│
├── asyncio.to_thread(_fetch_active_alerts, pg, "AAPL", "NASDAQ")
│     SELECT id, user_id, condition_json FROM alerts
│     WHERE symbol='AAPL' AND status='active'
│
├── for each alert: _evaluate_condition(condition, 201.50)
│     {"type": "price_above", "threshold": 200.0} → True  (201.50 > 200.0)
│
├── asyncio.create_task(_mark_triggered(pg, alert_id, ts))
│     UPDATE alerts SET status='triggered', triggered_at=NOW() WHERE id=...
│
└── manager.broadcast("alerts:{user_id}", {
      "type":            "alert_triggered",
      "alert_id":        "...",
      "condition_type":  "price_above",
      "threshold":       200.0,
      "triggered_price": 201.50,
      "message":         "AAPL crossed above 200.0",
      "ts":              "2026-04-17T21:30:00Z"
    })
```

Supported condition types in `alerts.condition_json`:
- `price_above` / `price_below` — trigger when close crosses a fixed level
- `pct_change_above` / `pct_change_below` — trigger when close moves N% from `reference_price`

### Price tick publisher (used by workers / trading engine)

```python
import json, redis

r = redis.Redis(host="127.0.0.1", port=6379)
r.publish("prices:AAPL:NASDAQ", json.dumps({
    "symbol": "AAPL", "exchange": "NASDAQ",
    "close": 189.50, "open": 188.0, "high": 190.2, "low": 187.5,
    "volume": 65_432_100, "ts": "2026-04-17T21:30:00Z", "frequency": "daily"
}))
```

The subscriber loop reconnects with exponential back-off (1s → 60s) if Redis drops.

### Subscriber lifecycle

```
startup lifespan:
  subscriber_task = asyncio.create_task(price_subscriber_loop(...))

shutdown lifespan:
  subscriber_task.cancel()
  await subscriber_task   (catches CancelledError, exits cleanly)
```

---

## 15. Background Job Flow — ARQ *(implemented — see §14)*

### On-demand IBKR fetch

```
GET /api/v1/bars/RELIANCE?exchange=NSE  →  BarReader.read() returns empty
│
├── await arq_pool.enqueue_job("fetch_bars_task", symbol="RELIANCE", …)
└── HTTP 202 Accepted {"job_id": "…", "status": "queued"}

ARQ worker process
│
└── fetch_bars_task(ctx, symbol, exchange, …)
      ibkr_client.fetch_bars(…)  →  split hot/cold  →  write
      redis.publish("job_done:{job_id}", {"status":"complete"})
      → WebSocket notifies browser → frontend re-fetches
```

### Nightly precompute

```
ARQ cron: precompute_rolling_metrics  (02:00 UTC nightly)
│
└── for (symbol, exchange) in universe:
      bars = BarReader.read(…, last_1Y)
      compute CAGR, Sharpe, volatility, max_drawdown
      store in Redis or market_data.rolling_metrics table
      → next API call reads from cache in <10ms
```

---

## 16. Postgres Schema Reference

### public schema — application tables

```
users
  id UUID PK | email TEXT UNIQUE | password_hash TEXT | base_currency TEXT
  display_tz TEXT | display_date_fmt TEXT | created_at TIMESTAMPTZ

broker_accounts
  id UUID PK | user_id FK→users | broker TEXT | display_name TEXT
  account_ref TEXT | currency TEXT | is_active BOOL | created_at | updated_at

portfolios
  id UUID PK | user_id FK→users | broker_account_id FK→broker_accounts (nullable)
  name TEXT | portfolio_type TEXT | base_currency TEXT | is_default BOOL
  created_at | updated_at
  INDEX: unique partial (user_id) WHERE is_default=TRUE

positions                                          ← added Phase 2
  id UUID PK | portfolio_id FK→portfolios | user_id FK→users
  symbol TEXT | exchange TEXT | asset_class TEXT
  quantity DOUBLE | cost_basis DOUBLE | currency TEXT
  opened_at TIMESTAMPTZ | closed_at TIMESTAMPTZ (NULL=open) | notes TEXT
  created_at | updated_at

dashboard_layouts
  id UUID PK | user_id FK→users | name TEXT
  layout_json JSONB | created_at | updated_at

widget_configs
  id UUID PK | dashboard_layout_id FK→dashboard_layouts | user_id FK→users
  widget_type TEXT | title TEXT
  config_json JSONB   ← symbol, timeframe, indicators, thresholds, etc.
  position_json JSONB ← {x, y, w, h} for react-grid-layout
  created_at | updated_at

strategy_configs
  id UUID PK | user_id FK→users | strategy_name TEXT
  config_json JSONB | mode TEXT | is_active BOOL | description TEXT
  created_at | updated_at

backtest_runs
  id UUID PK | user_id FK→users | strategy_config_id FK (nullable)
  portfolio_id FK (nullable) | status TEXT | params_json JSONB
  results_json JSONB | error_message TEXT | started_at | finished_at | created_at

alerts
  id UUID PK | user_id FK→users | alert_type TEXT | message TEXT
  symbol TEXT | exchange TEXT | condition_json JSONB
  status TEXT | triggered_at | resolved_at | created_at

annotations                                        ← added Phase 2
  id UUID PK | user_id FK→users | symbol TEXT | exchange TEXT
  annotation_type TEXT | timestamp_start TIMESTAMPTZ | timestamp_end TIMESTAMPTZ
  price_start DOUBLE | price_end DOUBLE | label TEXT | color TEXT
  data_json JSONB | created_at | updated_at
```

### market_data schema — time-series tables

```
daily_bars                                         (hot tier)
  symbol, exchange, asset_class, frequency, timestamp → PK composite
  open, high, low, close, volume DOUBLE
  ingested_at | updated_at
  INDEX: (symbol, exchange, asset_class, frequency, timestamp DESC)

corporate_events                                   ← added Phase 3
  id BIGSERIAL PK | symbol TEXT | exchange TEXT | asset_class TEXT
  event_type TEXT (dividend|split|earnings)
  event_date DATE | value DOUBLE | currency TEXT | description TEXT
  fetched_at TIMESTAMPTZ
  UNIQUE: (symbol, exchange, event_type, event_date)
  INDEX: (symbol, exchange, event_date DESC)
```

---

## 17. Module Reference

### `apps/workers/market_data/`

| Module | What it does |
|---|---|
| `config.py` | Pydantic config models: `JobConfig`, `PostgresConfig`, `IbkrConfig`, etc. |
| `ibkr_client.py` | NautilusTrader wrapper; `fetch_bars()`, `get_head_timestamp()`, rate limiter |
| `parquet_store.py` | `write_partition()` hive-partitioned writes; `read_bars()` DuckDB reads |
| `postgres_store.py` | `upsert_bars()`, `read_bars_for_partition()`, `delete_bars_for_partition()`, `fetch_cold_partition_keys()`, `latest_timestamp()` |
| `duckdb_meta.py` | Job runs, slice progress, coverage, split-check metadata |
| `windowing.py` | `yearly_windows_newest_to_oldest()`, `paginated_windows_backward()` |
| `dedup.py` | `deduplicate_bars()` — removes timestamp duplicates within a page |
| `universe_loader.py` | Reads exchange/symbol lists from config; returns priority-ordered instruments |
| `logger.py` | `StructuredLogger` — one JSON line per event, flushed after every write |

### `apps/workers/jobs/`

| Module | What it does |
|---|---|
| `backfill.py` | Full historical backfill; year-by-year, page-by-page; restartable |
| `daily_refresh.py` | Incremental update: latest stored timestamp → now |
| `archive_cold_bars.py` | Drains aged Postgres rows into Parquet; write-then-delete ordering; updates DuckDB parquet/coverage metadata |
| `fetch_events.py` | yfinance → `market_data.corporate_events`; dividends, splits, earnings |
| `precompute_metrics.py` | Nightly rolling metrics (1Y/2Y/3Y/5Y) → `market_data.rolling_metrics` |
| `arq_worker.py` | ARQ worker entry point; `fetch_bars_task`, `precompute_metrics_task`, `daily_refresh_task`; cron schedule |

### `apps/api/app/`

| Module | What it does |
|---|---|
| `main.py` | FastAPI app, all router registrations, WebSocket endpoint |
| `core/config.py` | `Settings` — all env-var configuration for the API |
| `db/connection.py` | `get_db()` — psycopg2 `Depends`; commit/rollback/close lifecycle |
| `auth/password.py` | `hash_password()`, `verify_password()` — passlib bcrypt |
| `auth/jwt.py` | `create_access_token()`, `decode_access_token()` — python-jose HS256 |
| `auth/dependencies.py` | `get_current_user` — Bearer JWT → `CurrentUser(id, email)` |
| `lib/bar_reader.py` | `BarReader.read()` — tiered cold + hot dispatch, merge, dedup; `latest_price()` — last close for a symbol |
| `lib/indicators.py` | Pure functions: `sma`, `ema`, `wma`, `bollinger_bands`, `rsi`, `macd`, `atr` |
| `lib/metrics.py` | Pure functions: `cagr`, `total_return`, `max_drawdown`, `volatility`, `sharpe`, `sortino`, `equity_curve`, `build_portfolio_curve`, `compute_metrics` |
| `lib/cache.py` | `RedisBarCache` — Parquet-serialised DataFrame cache; `make_redis_client()`; TTL logic (5 min live / 1 h historical) |
| `lib/arq_pool.py` | `create_arq_pool()`, `get_arq_pool()` dependency, `get_job_status()` |
| `api/schemas/bars.py` | `Bar`, `EventMarker`, `ChartSeries` — unified Brain↔Skin contract (§8) |
| `api/schemas/auth.py` | `RegisterRequest`, `LoginRequest`, `TokenResponse` |
| `api/schemas/users.py` | `UserResponse`, `UpdateUserRequest` |
| `api/schemas/dashboards.py` | `DashboardSummaryResponse`, `DashboardDetailResponse`, `WidgetResponse`, `CreateWidgetRequest`, etc. |
| `api/schemas/annotations.py` | `AnnotationResponse`, `CreateAnnotationRequest`, `UpdateAnnotationRequest` |
| `api/schemas/portfolios.py` | `PortfolioResponse`, `PositionResponse`, `PortfolioOverviewResponse`, `PositionWithValue`, `BrokerAccountResponse`, etc. |
| `api/schemas/strategies.py` | `StrategyResponse`, `BacktestRunResponse`, `BacktestRunDetailResponse`, `BacktestSummary`, `SubmitBacktestResultsRequest` |
| `api/routes/auth.py` | `POST /auth/register`, `POST /auth/login` |
| `api/routes/users.py` | `GET /users/me`, `PATCH /users/me` |
| `api/routes/bars.py` | `GET /bars/{symbol}` — tiered OHLCV read → `ChartSeries` |
| `api/routes/indicators.py` | `GET /indicators/{symbol}?indicator=SMA&period=20` → `list[ChartSeries]` |
| `api/routes/events.py` | `GET /events/{symbol}?types=dividend,split,earnings` → `list[EventMarker]` |
| `api/routes/dashboards.py` | Full CRUD for dashboards and widgets |
| `api/routes/annotations.py` | `GET/POST/PATCH/DELETE /annotations` |
| `api/routes/broker_accounts.py` | `GET/POST/PATCH/DELETE /broker-accounts` |
| `api/routes/portfolios.py` | Full portfolio + position CRUD; `GET /portfolios/{id}/overview`; `GET /portfolios/{id}/curve` |
| `api/routes/strategies.py` | Strategy config CRUD (`GET/POST/PATCH/DELETE /strategies`) |
| `api/routes/backtests.py` | Backtest run CRUD; `PATCH /{id}/results`; `GET /{id}/curve`; `GET /compare` |
| `api/routes/strategy_control.py` | `POST /strategies/start|stop` — live trading engine controls (placeholder) |
| `api/routes/jobs.py` | `POST /jobs/fetch-bars` (enqueue); `GET /jobs/{id}` (poll status) |
| `api/routes/market_metrics.py` | `GET /market-data/metrics/{symbol}` — pre-aggregated rolling metrics |
| `api/routes/alerts.py` | `GET/POST/PATCH/DELETE /alerts` — alert condition CRUD |
| `api/schemas/alerts.py` | `AlertResponse`, `CreateAlertRequest`, `UpdateAlertRequest` |
| `ws/manager.py` | `ConnectionManager` — channel + user-id keyed WS broadcast; `send_to_user()` |
| `ws/redis_subscriber.py` | `price_subscriber_loop()` — async Redis pub/sub fan-out; reconnect with back-off |
| `ws/pnl_tracker.py` | `PnlTracker.on_tick()` — portfolio P&L delta on price tick |
| `ws/alert_evaluator.py` | `AlertEvaluator.on_tick()` — condition evaluation + triggered alert push |

### `data/postgres/init/`

| File | What it migrates |
|---|---|
| `001_init.sql` | Skeleton: `users`, `dashboard_layouts`, `strategy_configs`, `alerts` |
| `002_market_data_hot_bars.sql` | `market_data` schema + `daily_bars` hot-tier table |
| `003_app_schema.sql` | Extends 001 + adds `broker_accounts`, `portfolios`, `widget_configs`, `backtest_runs` |
| `004_positions_annotations.sql` | Adds `positions`, `annotations` |
| `005_corporate_events.sql` | Adds `market_data.corporate_events` |
| `006_rolling_metrics.sql` | Adds `market_data.rolling_metrics` (pre-aggregated 1Y–5Y metrics) |

### `docs/`

| File | What it covers |
|---|---|
| `architecture.md` | Tech stack, component map, API route table, data flow diagram |
| `code-flow.md` | This document — end-to-end flows and module reference |
| `backfill-design.md` | Detailed backfill spec (requirements + design decisions) |
| `dashboard-requirements.md` | Full product requirements for the dashboard |
| `requirements-current-state.md` | Implementation status snapshot |
| `adr/` | ADR-001 through ADR-010 — architecture decision records |
