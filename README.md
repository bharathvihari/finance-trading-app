# Finance Trading App

Monorepo scaffold for a personal finance and trading platform that can evolve to multi-user SaaS.

## What this structure supports

- Live and historical market data ingestion
- NautilusTrader-based strategy execution (backtest/paper/live)
- Multi-user dashboard backend APIs + WebSocket streaming
- Angular frontend with configurable and drag/drop-ready dashboard widgets
- Scheduled pipelines for backfills and incremental refreshes
- Internet-accessible deployment via containerized services

## Repository layout

```text
.
|-- apps/
|   |-- api/                # FastAPI + WebSocket + control APIs
|   |-- web/                # Angular UI (dashboard + strategy control)
|   |-- trading/            # NautilusTrader runners, adapters, strategies
|   `-- workers/            # Backfill and daily/incremental jobs
|-- libs/
|   `-- shared/             # Cross-app contracts/schemas
|-- infra/
|   |-- docker/             # Dockerfiles by service
|   `-- docker-compose.yml  # Local orchestration
|-- data/
|   |-- parquet/            # Market data lake
|   `-- postgres/init/      # DB bootstrapping SQL
|-- docs/                   # Architecture and implementation notes
|-- scripts/                # Local helper scripts
`-- tests/                  # API and integration test placeholders
```

## Quick start

1. Copy `.env.example` to `.env` and fill secrets.
2. Start infra:
   - `docker compose -f infra/docker-compose.yml up -d postgres redis minio`
3. Start API, trading, workers, and web apps incrementally from each app folder.
4. Open architecture notes:
   - [`docs/architecture.md`](docs/architecture.md)

## Design choices in this scaffold

- `Postgres` for metadata, users, layouts, orders, positions, alerts.
- `Parquet` for large historical OHLCV/time-series store.
- `Redis Pub/Sub` for event-driven live dashboard refresh.
- `FastAPI` backend for REST + WebSocket streaming.
- `Angular` frontend foundation for configurable dashboards.
- `Gridstack` (planned in web app) for drag/drop widgets.
- `NautilusTrader` app as isolated trading runtime service.

## Next implementation steps

1. Generate a full Angular app in `apps/web` and wire authentication.
2. Build Nautilus adapters/config for IBKR credentials + venue routing.
3. Implement API persistence models and RBAC multi-user isolation.
4. Add CI/CD and cloud deployment (Kubernetes or managed containers).
