# Initial Technical Decisions

## Why FastAPI for backend
- First-class async support for WebSockets and streaming.
- Python ecosystem alignment with data/trading services.

## Why Angular for frontend
- Strong structure for large apps, typed TS architecture.
- Good fit for enterprise-style multi-user dashboard product evolution.

## Why Postgres + Parquet
- Postgres: relational data, user state, app metadata, controls.
- Parquet: efficient columnar storage for time-series history.

## Why Redis for live updates
- Lightweight pub/sub bridge for event-driven dashboard refresh.
- Supports decoupling between ingestion/trading and UI streaming.

## Why isolate NautilusTrader
- Clean separation between trading runtime and web/API concerns.
- Easier environment controls for backtest/paper/live.
