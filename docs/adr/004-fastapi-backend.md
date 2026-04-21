# ADR-004: FastAPI for the Backend API

## Status
Accepted

## Context
The backend API needs to serve:
- REST endpoints for OHLCV data, portfolio analytics, auth, dashboard config.
- WebSocket streams for live price and PnL updates.
- Blocking I/O paths (psycopg2 for Postgres, DuckDB for Parquet) alongside async paths.
- Auto-generated OpenAPI docs (useful for frontend development).

Alternatives considered: Django REST Framework, Flask, Litestar.

## Decision
Use **FastAPI** with **uvicorn** as the ASGI server.

Key reasons:
- Native async support with clean `async def` / `def` route distinction.
- FastAPI runs synchronous route handlers (psycopg2, DuckDB) in a thread pool automatically —
  no manual `asyncio.to_thread()` needed.
- `Depends()` system makes auth, DB connections, and BarReader injectable and testable.
- Pydantic v2 response models give us the `ChartSeries` contract for free with validation.
- Auto-generated Swagger UI at `/docs` — usable as an API explorer during development.
- WebSocket support is first-class.

## Consequences

### Good
- Auth middleware (`get_current_user`) is a single injectable dependency reusable across all routes.
- Pydantic models are the source of truth for both validation and OpenAPI schema.
- Sync routes (bar reads, DB writes) and async routes (WebSocket) coexist cleanly.
- `uvicorn` with multiple workers scales horizontally behind a load balancer.

### Bad
- psycopg2 (sync) holds a thread-pool thread per open connection — for high concurrency,
  asyncpg + a connection pool would be more efficient (acceptable trade-off for personal use).
- FastAPI's `Depends()` graph can become complex if over-abstracted.
