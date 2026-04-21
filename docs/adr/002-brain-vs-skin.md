# ADR-002: Brain vs. Skin Architecture Separation

## Status
Accepted

## Context
Dashboard applications are frequently rewritten at the UI layer — design trends shift,
framework ecosystems evolve, and mobile vs desktop requirements diverge. Coupling business
logic to a specific UI framework means every frontend rewrite also rewrites the calculations,
data access, and validation logic. This is expensive and introduces regressions.

## Decision
All work is explicitly categorised into **Brain** or **Skin**:

**Brain** (~80% of total effort) — permanently stable:
- The database schemas and Parquet data layout.
- The data pipeline (IBKR → Postgres/Parquet).
- All calculation logic: indicators, P&L, performance metrics (CAGR, Sharpe, TWRR, etc.).
- The REST + WebSocket API that exposes this logic.
- The unified `ChartSeries` / `EventMarker` response contract (ADR-007).

**Skin** (~20% of effort) — replaceable:
- The frontend framework (currently Next.js; could be React Native, Flutter, etc.).
- The chart library adapter (currently TradingView Lightweight Charts).
- The layout engine (react-grid-layout).

The contract between Brain and Skin is the `ChartSeries` API response model defined in
`apps/api/app/api/schemas/bars.py`. Any frontend can be plugged in without modifying
any backend code.

## Consequences

### Good
- Frontend framework decisions are low-stakes and reversible.
- The Brain can be tested end-to-end (API tests) independently of any UI.
- Backend logic is usable by multiple clients: web, mobile app, CLI, backtester.
- Encourages pure-function calculation modules (no side effects, easy to unit test).

### Bad
- Requires discipline to keep logic out of frontend components.
- The API contract (`ChartSeries`) must be versioned carefully — breaking changes affect
  all Skin implementations.
- More initial backend investment before any UI is visible.
