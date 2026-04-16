# Finance Trading App Requirements (Current-State Aligned)

## 1. Product Goal
Build a personal-first finance trading platform that can scale to multi-user usage with personalized dashboards, strategy workflows, and live market/trading monitoring.

## 2. Data & Analytics (Already Implemented Baseline)
- Historical backfill and daily incremental pipelines exist.
- Data is stored in partitioned Parquet (`asset_class/exchange/frequency/year`).
- DuckDB is used for metadata/progress/coverage/error tracking and Parquet query support.
- Validation, retry-failed, dry-run, and runbook support are available.
- Current frequency support is daily bars.

## 3. Dashboard Requirements (Partially Implemented / Pending UI)
- Multi-user personalized dashboards.
- Configurable widgets (ticker, duration, metrics like CAGR and timeline stats).
- Live/event-driven refresh for market and portfolio updates.
- Drag-and-drop widget layout on web dashboards.
- Internet-accessible dashboard app.
- 5-year curves and comparative asset/index views.

## 4. Trading Requirements (Partially Implemented / Pending Execution Layer)
- NautilusTrader-compatible ingestion/import pipeline is present.
- IBKR/Nautilus client scaffolding and pacing/retry controls exist.
- Full live IBKR historical/trading execution wiring is still pending.
- Strategy definition, deployment, order execution, and alerting must be completed.

## 5. Platform Requirements
- Postgres is the main application DB target (users, auth, dashboards, portfolios, strategy configs, alerts, backtest metadata).
- DuckDB + Parquet remain the analytics/time-series layer.
- Redis/event streaming for live UI updates is part of target architecture.
