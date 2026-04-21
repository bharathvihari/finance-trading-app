# Architecture (Logical → Physical Mapping)

## Tech Stack

| Layer | Technology |
|---|---|
| Frontend | Next.js 14 + React + TypeScript |
| Charts | TradingView Lightweight Charts (framework-agnostic adapter) |
| Dashboard layout | react-grid-layout |
| Backend API | FastAPI (Python 3.12) |
| Auth | JWT — python-jose + passlib/bcrypt |
| App database | PostgreSQL 18 |
| Time-series / analytics | DuckDB + partitioned Parquet |
| Live updates / cache | Redis 7 |
| Async job queue | ARQ (Redis-backed, async Python) |
| Local orchestration | Docker Compose |

---

## 1) Data Ingestion & Storage

Logical responsibilities:
- Historical backfill (one-time, restartable)
- Daily incremental updates
- Live feed ingestion for trading and dashboards
- Tiered persistence: hot in Postgres, cold in Parquet

Physical components:
- `apps/workers/jobs/backfill.py`
- `apps/workers/jobs/daily_refresh.py`
- `apps/workers/jobs/archive_cold_bars.py`
- `apps/workers/market_data/` (ParquetStore, PostgresBarStore, DuckDbMetaStore, ...)
- `data/parquet/price-data/` (partitioned: asset_class / exchange / frequency / year)
- `data/duckdb/market_data.duckdb` (job metadata, coverage, slice state)
- `data/postgres/init/` (SQL migrations 001–003)
- `infra/docker-compose.yml` (`postgres`, `redis`, `minio`)

### Tiered storage boundary

```
bars with timestamp >= hot_cutoff  →  Postgres  market_data.daily_bars
bars with timestamp <  hot_cutoff  →  Parquet   price-data/asset_class=.../year=.../part-*.parquet
```

`hot_cutoff = first day of month (now − hot_window_months)`.  Default: 6 months.
The archive job (`archive_cold_bars.py`) runs periodically to drain Postgres rows that
have aged past the cutoff into Parquet, keeping the hot tier bounded.

---

## 2) Trading Engine (NautilusTrader)

Logical responsibilities:
- Market data ingestion, event engine, strategy execution
- Portfolio / risk management and order routing
- Backtest / paper / live environments

Physical components:
- `apps/trading/nautilus_runner/main.py`
- `apps/trading/strategies/`
- `apps/trading/adapters/ibkr_config.example.yaml`

---

## 3) Backend API Layer

Logical responsibilities:
- REST APIs for the frontend and external clients
- WebSocket live streams (PnL, orders, positions, chart ticks)
- Tiered reads from Postgres (hot) and Parquet/DuckDB (cold)
- Auth: JWT issue and validation
- Control endpoints for strategy lifecycle and parameters

Physical components:
- `apps/api/app/main.py` — FastAPI app, router registration
- `apps/api/app/auth/` — JWT, bcrypt, `get_current_user` dependency
- `apps/api/app/db/connection.py` — psycopg2 connection dependency
- `apps/api/app/lib/bar_reader.py` — tiered OHLCV query (hot + cold merge)
- `apps/api/app/api/routes/` — auth, users, bars, dashboard, strategies
- `apps/api/app/api/schemas/` — Pydantic response models (ChartSeries, etc.)
- `apps/api/app/ws/manager.py` — WebSocket connection manager

### API routes (current)

**Auth & Users**

| Method | Path | Auth | Description |
|---|---|---|---|
| POST | `/api/v1/auth/register` | — | Create account, returns JWT |
| POST | `/api/v1/auth/login` | — | Authenticate, returns JWT |
| GET | `/api/v1/users/me` | ✓ | Current user profile |
| PATCH | `/api/v1/users/me` | ✓ | Update preferences (currency, timezone, date format) |

**Market Data**

| Method | Path | Auth | Description |
|---|---|---|---|
| GET | `/api/v1/bars/{symbol}` | ✓ | OHLCV bars — tiered hot+cold read → `ChartSeries` |
| GET | `/api/v1/indicators/{symbol}` | ✓ | Computed indicator series (SMA/EMA/WMA/BB/RSI/MACD/ATR) → `list[ChartSeries]` |
| GET | `/api/v1/events/{symbol}` | ✓ | Corporate event markers (dividends, splits, earnings) → `list[EventMarker]` |

**Dashboards & Widgets**

| Method | Path | Auth | Description |
|---|---|---|---|
| POST | `/api/v1/dashboards` | ✓ | Create dashboard |
| GET | `/api/v1/dashboards` | ✓ | List user's dashboards with widget count |
| GET | `/api/v1/dashboards/{id}` | ✓ | Get dashboard with full widget list |
| PATCH | `/api/v1/dashboards/{id}` | ✓ | Rename / update global layout settings |
| DELETE | `/api/v1/dashboards/{id}` | ✓ | Delete dashboard + all widgets (CASCADE) |
| POST | `/api/v1/dashboards/{id}/widgets` | ✓ | Add widget to dashboard |
| GET | `/api/v1/dashboards/{id}/widgets` | ✓ | List widgets |
| PATCH | `/api/v1/dashboards/{id}/widgets/{wid}` | ✓ | Update widget config or position (drag/resize) |
| DELETE | `/api/v1/dashboards/{id}/widgets/{wid}` | ✓ | Remove widget |

**Annotations**

| Method | Path | Auth | Description |
|---|---|---|---|
| POST | `/api/v1/annotations` | ✓ | Save chart annotation (trendline, note, etc.) |
| GET | `/api/v1/annotations?symbol=&exchange=` | ✓ | List user's annotations for a symbol |
| PATCH | `/api/v1/annotations/{id}` | ✓ | Update annotation (move endpoint, change label) |
| DELETE | `/api/v1/annotations/{id}` | ✓ | Delete annotation |

**Broker Accounts & Portfolios**

| Method | Path | Auth | Description |
|---|---|---|---|
| POST | `/api/v1/broker-accounts` | ✓ | Register a broker connection |
| GET | `/api/v1/broker-accounts` | ✓ | List user's broker accounts |
| PATCH | `/api/v1/broker-accounts/{id}` | ✓ | Update broker account |
| DELETE | `/api/v1/broker-accounts/{id}` | ✓ | Delete broker account |
| POST | `/api/v1/portfolios` | ✓ | Create portfolio (live/paper/demo/imported) |
| GET | `/api/v1/portfolios` | ✓ | List portfolios (default first) |
| GET | `/api/v1/portfolios/{id}` | ✓ | Get portfolio |
| PATCH | `/api/v1/portfolios/{id}` | ✓ | Rename / change currency / set default |
| DELETE | `/api/v1/portfolios/{id}` | ✓ | Delete portfolio + positions (CASCADE) |
| POST | `/api/v1/portfolios/{id}/positions` | ✓ | Add position (buy lot) |
| GET | `/api/v1/portfolios/{id}/positions` | ✓ | List positions (`?open_only=true`) |
| PATCH | `/api/v1/portfolios/{id}/positions/{pos_id}` | ✓ | Update / close position |
| DELETE | `/api/v1/portfolios/{id}/positions/{pos_id}` | ✓ | Delete position |
| GET | `/api/v1/portfolios/{id}/overview` | ✓ | Live prices + unrealized P&L + performance metrics |
| GET | `/api/v1/portfolios/{id}/curve` | ✓ | Equity curve as `list[ChartSeries]` with optional benchmark |

**Strategies**

| Method | Path | Auth | Description |
|---|---|---|---|
| POST | `/api/v1/strategies` | ✓ | Create strategy config |
| GET | `/api/v1/strategies` | ✓ | List user's strategy configs |
| GET | `/api/v1/strategies/{id}` | ✓ | Get strategy config |
| PATCH | `/api/v1/strategies/{id}` | ✓ | Update params / toggle active |
| DELETE | `/api/v1/strategies/{id}` | ✓ | Delete strategy config |
| POST | `/api/v1/strategies/start` | ✓ | Start live/paper strategy (NautilusTrader — placeholder) |
| POST | `/api/v1/strategies/stop` | ✓ | Stop live/paper strategy (NautilusTrader — placeholder) |

**Backtests**

| Method | Path | Auth | Description |
|---|---|---|---|
| POST | `/api/v1/backtests` | ✓ | Create run record (status=pending) |
| GET | `/api/v1/backtests` | ✓ | List runs (`?strategy_id=` / `?status=`) |
| GET | `/api/v1/backtests/compare` | ✓ | Overlay equity curves for multiple runs → `list[ChartSeries]` |
| GET | `/api/v1/backtests/{id}` | ✓ | Full run with results_json and extracted summary |
| PATCH | `/api/v1/backtests/{id}/results` | ✓ | Trading engine submits results (status, equity curve, trades) |
| DELETE | `/api/v1/backtests/{id}` | ✓ | Delete run record |
| GET | `/api/v1/backtests/{id}/curve` | ✓ | Equity + drawdown curves → `list[ChartSeries]` |

**Jobs & Market Data**

| Method | Path | Auth | Description |
|---|---|---|---|
| POST | `/api/v1/jobs/fetch-bars` | ✓ | Enqueue on-demand IBKR bar fetch → ARQ job; returns job_id + 202 |
| GET | `/api/v1/jobs/{job_id}` | ✓ | Poll ARQ job status (queued / in_progress / complete / failed) |
| GET | `/api/v1/market-data/metrics/{symbol}` | ✓ | Pre-aggregated rolling metrics (1Y/2Y/3Y/5Y) from nightly worker |

**Alerts**

| Method | Path | Auth | Description |
|---|---|---|---|
| POST | `/api/v1/alerts` | ✓ | Create price/condition alert |
| GET | `/api/v1/alerts` | ✓ | List alerts (`?status=active`, `?symbol=AAPL`) |
| PATCH | `/api/v1/alerts/{id}` | ✓ | Update condition, message, or dismiss |
| DELETE | `/api/v1/alerts/{id}` | ✓ | Delete alert |

**WebSocket (live feeds)**

| Channel | Auth | Description |
|---|---|---|
| `wss://api/ws/prices:{SYM}:{EXCH}?token=<jwt>` | ✓ | Live price tick — chart candlestick update |
| `wss://api/ws/portfolio:{portfolio_id}?token=<jwt>` | ✓ owner | Live portfolio unrealized P&L delta |
| `wss://api/ws/alerts:{user_id}?token=<jwt>` | ✓ self | Alert trigger notifications |

---

## 4) Frontend (Next.js)

Logical responsibilities:
- Personalized dashboards (PnL, positions, risk, alerts)
- Strategy configuration, deployment, and monitoring
- Historical analytics and charting
- Multi-panel drag-and-drop layout

Physical components:
- `apps/web/` (to be scaffolded)
- Framework: Next.js 14 App Router + React + TypeScript
- Charts: TradingView Lightweight Charts (via the `ChartSeries` adapter contract)
- Layout: react-grid-layout (drag, drop, resize)

---

## 5) Orchestration / Scheduling

Logical responsibilities:
- Backfill and refresh scheduling
- Cold archival scheduling
- Async on-demand job execution (ARQ)
- Environment coordination across backtest / paper / live

Physical components:
- `apps/workers/` (standalone job scripts)
- `infra/docker-compose.yml` (local orchestration)
- ARQ worker process (Phase 6 — Redis-backed async queue for on-demand tasks)

---

## Event-Driven Live Refresh Pattern

```
Price event / trade event
        │
        ▼
  Redis pub/sub channel
        │
        ▼
  API subscriber loop  (apps/api/app/ws/)
        │
        ▼
  ConnectionManager.broadcast()
        │
        ▼
  WebSocket → browser widget
```

1. Live market events (from trading engine or ingestion) are published to Redis channels.
2. The API subscribes and fans out messages through the WebSocket `ConnectionManager`.
3. Next.js dashboard widgets subscribe to relevant streams (ticker / timeframe / user context).
4. User layout and widget config are persisted in Postgres (`dashboard_layouts`, `widget_configs`).

---

## Data Flow Overview

```
IBKR (Client Portal Gateway)
        │  NautilusTrader adapter
        ▼
  IbkrHistoricalClient
        │
        ├─── bars >= hot_cutoff ──► PostgresBarStore  (market_data.daily_bars)
        │                                  │
        │                          periodic archive job
        │                                  │
        └─── bars <  hot_cutoff ──► ParquetStore  (price-data/.../year=YYYY/part-*.parquet)
                                           │
                                    DuckDB (query engine)

API read request for bars
        │
        ├─ range entirely hot  ──► SELECT FROM market_data.daily_bars
        ├─ range entirely cold ──► DuckDB read_parquet(...)
        └─ range spans both    ──► both, concat + dedup on timestamp
```

---

## 6) Deployment & Infrastructure (Phase 8)

Logical responsibilities:
- TLS termination and HTTPS enforcement
- Request routing and rate limiting
- Service health monitoring and auto-restart
- Environment configuration and secrets management
- Container security (non-root users, minimal images)

Physical components:
- `infra/nginx/nginx.conf` (reverse proxy with TLS, rate limiting, security headers)
- `infra/docker-compose.yml` (Docker services with health checks and dependency ordering)
- `infra/docker/api.Dockerfile` (multi-stage build, non-root user, optimized for production)
- `infra/docker/workers.Dockerfile` (multi-stage build, non-root user)
- `infra/gen-tls-cert.sh` (helper script for TLS certificate generation)
- `.env.template` (documented environment variables)
- `docs/deployment.md` (deployment guide and troubleshooting)

### Nginx reverse proxy

```
Client (HTTPS)
    │
    ▼
Nginx (TLS termination, port 443)
    │
    ├─ /api/*  ──► API service (rate limited 10 req/s)
    ├─ /ws/*   ──► WebSocket (rate limited 20 req/s)
    └─ /health ──► Health check endpoint
```

- HTTP (port 80) redirects to HTTPS
- TLS 1.2+ with modern ciphers
- Security headers: HSTS, X-Frame-Options, X-Content-Type-Options
- Gzip compression enabled
- Request rate limiting per IP address

### Service health checks

All services report health status to Docker:
- `postgres` — `pg_isready` check
- `redis` — `redis-cli ping` check
- `api` — HTTP `/health` endpoint check
- `workers` — None (background job processor)
- `nginx` — HTTP `/health` forwarding check

Docker waits for health checks to pass before starting dependent services.

### Container security

- Multi-stage builds: compile dependencies in builder, copy only runtime packages to final image
- Non-root user: containers run as `appuser` (UID 1000) instead of root
- Minimal base images: `*-alpine` variants where available (postgres, redis, nginx)
- No-cache pip installs to reduce image size and avoid stale packages

See `docs/deployment.md` for setup, scaling, and troubleshooting.
