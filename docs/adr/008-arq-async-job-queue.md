# ADR-008: ARQ for Async Redis-Backed Job Queue (vs Celery)

## Status
Accepted (planned — implementation in Phase 6)

## Context
Two use cases require background job execution beyond simple cron:

1. **On-demand IBKR fetch** — a dashboard requests a symbol not present in Parquet.
   The API cannot block the HTTP request for 10–60 seconds while IBKR data is fetched.
   It must enqueue the job and return a 202, then notify the client via WebSocket when done.

2. **Precompute / cache refresh** — rolling performance metrics (Sharpe, CAGR, volatility)
   and portfolio equity curves are expensive to compute per-request. They should be
   precomputed in the background and stored in Redis or Postgres for fast reads.

Alternatives:
- **Celery + Redis** — mature, feature-rich, supports chaining and groups. But requires
  a separate Celery beat process for scheduling, celery worker process, and adds significant
  boilerplate for a personal app.
- **RQ (Redis Queue)** — simpler than Celery but synchronous workers only.
- **ARQ** — async Redis queue built for async Python, minimal config, native `asyncio`.

## Decision
Use **ARQ** (Async Redis Queue):
- Workers are async Python functions decorated with ARQ's worker protocol.
- The FastAPI API enqueues jobs via `await arq_pool.enqueue_job("fetch_bars", symbol, ...)`.
- The same Redis instance (already in docker-compose) acts as the broker.
- ARQ supports cron-style scheduled functions — replaces the need for a separate scheduler
  for the precompute use case.

The `workers` Docker service (currently running `daily_refresh.py` directly) will be extended
to run the ARQ worker pool, which includes daily refresh, precompute, and on-demand tasks.

## Consequences

### Good
- Single Redis instance serves both cache and job queue — no new infrastructure.
- Async workers share the same event loop model as FastAPI — consistent mental model.
- ARQ's minimal API (`@worker_task`, `enqueue_job`, `cron`) is easy to reason about.
- Job status is queryable from Redis — the API can check if an on-demand fetch is in progress.

### Bad
- ARQ is less battle-tested than Celery for large job volumes (acceptable for personal scale).
- No built-in web UI for job monitoring (Celery has Flower). Can be addressed with a simple
  custom `/api/v1/jobs/status/{job_id}` endpoint.
- ARQ requires `aioredis` / `redis-py[asyncio]` — adds an async Redis client alongside the
  existing sync `redis` client used for caching.
