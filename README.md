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

## Quick Start

### Development

```bash
# 1. Copy environment template and fill in values
cp .env.template .env

# 2. Generate TLS certificate for HTTPS
cd infra
./gen-tls-cert.sh
cd ..

# 3. Start all services with Docker Compose
docker-compose -f infra/docker-compose.yml up -d

# 4. Verify services are running and healthy
docker-compose -f infra/docker-compose.yml ps

# 5. Test the API
curl -k https://localhost/api/health
```

See [`infra/README.md`](infra/README.md) for detailed commands.

### Production Deployment

See [`docs/deployment.md`](docs/deployment.md) for:
- Environment configuration and secrets
- TLS certificate setup (Let's Encrypt recommended)
- Service health monitoring
- Troubleshooting and scaling

## Project Status

### Completed (Phases 1–7)

- ✅ **Phase 1** — User authentication (JWT + Postgres)
- ✅ **Phase 2** — Market data API (tiered hot+cold storage)
- ✅ **Phase 3** — Cold data archival (Postgres → Parquet periodic job)
- ✅ **Phase 4** — Backtesting engine (equity curves, drawdown, trades)
- ✅ **Phase 5** — Monitoring widgets (dashboard layout + widget CRUD)
- ✅ **Phase 6** — Background job processing (ARQ + Redis + scheduled tasks)
- ✅ **Phase 7** — WebSocket live feeds (P&L tracker, alert evaluator)

### Current (Phase 8)

- ✅ **Phase 8** — Deployment infrastructure
  - Nginx reverse proxy with TLS termination
  - Multi-stage Docker builds (minimal production images)
  - Non-root container users for security
  - Health checks on all services
  - Environment configuration via `.env.template`
  - Documentation and deployment guide

All application features are complete. Future work is operational (monitoring, scaling, optimization).

## Design Choices

| Component | Technology | Why |
|-----------|-----------|-----|
| App DB | PostgreSQL 18 | ACID guarantees, JSON support, TimescaleDB-ready |
| Time-series / Analytics | DuckDB + Parquet | OLAP queries on partitioned historical data |
| Cache / Pub-Sub | Redis 7 | Low-latency live data, connection sockets |
| Backend API | FastAPI | Async Python, type-safe, auto-documentation |
| Charts | TradingView Lightweight Charts | Production-grade, lightweight, no vendor lock-in |
| Layout | react-grid-layout | Drag/drop + resize, React-friendly |
| Auth | JWT (HS256) | Stateless, long-lived tokens for personal use |
| Job Queue | ARQ | Async Python, Redis-backed, cron scheduling |
| Reverse Proxy | Nginx | Battle-tested, fast, TLS termination |
| Deployment | Docker Compose | Local dev + production parity |

## Next Steps

1. **Frontend**: Implement Next.js + React dashboard scaffolding (Phase 0)
2. **Trading**: Integrate NautilusTrader for strategy execution
3. **Monitoring**: Add Prometheus metrics and ELK stack (if scaling)
4. **CI/CD**: GitHub Actions for build, test, deploy
5. **Kubernetes**: Move beyond Docker Compose if multi-machine deployment needed
