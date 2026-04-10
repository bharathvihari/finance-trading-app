# Architecture (Logical -> Physical Mapping)

## 1) Data Ingestion & Storage

Logical responsibilities:
- Historical backfill jobs
- Daily incremental jobs
- Live feed ingestion for trading + dashboards
- Data persistence in Parquet + Postgres

Physical components in this repo:
- `apps/workers/jobs/backfill.py`
- `apps/workers/jobs/daily_refresh.py`
- `data/parquet/`
- `data/postgres/init/001_init.sql`
- `infra/docker-compose.yml` (`postgres`, `redis`, `minio`)

## 2) Trading Engine (NautilusTrader)

Logical responsibilities:
- Market data ingestion, event engine, strategies
- Portfolio/risk + order routing
- Backtest/paper/live environments

Physical components:
- `apps/trading/nautilus_runner/main.py`
- `apps/trading/strategies/`
- `apps/trading/adapters/ibkr_config.example.yaml`

## 3) Backend API Layer

Logical responsibilities:
- REST APIs for UI/external clients
- WebSocket live streams (PnL, orders, positions, chart ticks)
- Reads from Postgres and Parquet
- Control endpoints for strategy lifecycle and parameters

Physical components:
- `apps/api/app/main.py`
- `apps/api/app/api/routes/`
- `apps/api/app/ws/manager.py`

## 4) Frontend (Angular)

Logical responsibilities:
- Personalized dashboards (PnL, positions, risk, alerts)
- Strategy configuration/deployment/monitoring
- Historical analytics and charting

Physical components:
- `apps/web/` scaffold
- Suggested widget framework:
  - `gridstack` for drag/drop layouts
  - `ngx-echarts` or TradingView charting widgets

## 5) Orchestration / Scheduling

Logical responsibilities:
- Backfill and refresh scheduling
- Environment coordination across backtest/paper/live

Physical components:
- `apps/workers/` (job runners)
- `infra/docker-compose.yml` (local orchestration)
- `scripts/run-dev.ps1` (developer startup entrypoint)

---

## Event-Driven Live Refresh Pattern

1. Live market events (from trading or ingestion) are published to Redis channels.
2. API subscribes and fans out messages through WebSocket.
3. Angular dashboard widgets subscribe to relevant streams (ticker/timeframe/user context).
4. User layout and widget config are persisted in Postgres (`dashboard_layouts`).

This keeps dashboards live and personalized while scaling from single-user to multi-user.
