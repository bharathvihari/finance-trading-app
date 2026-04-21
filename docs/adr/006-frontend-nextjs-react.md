# ADR-006: Frontend — Next.js + React + TypeScript (replacing Angular)

## Status
Accepted (supersedes earlier Angular assumption in architecture.md)

## Context
The original `docs/architecture.md` noted Angular + ngx-echarts + gridstack as the frontend
stack. This was an early placeholder. The stack was revisited before any frontend code was written.

Criteria:
- Strong TypeScript ecosystem.
- Good integration story for TradingView Lightweight Charts (a JS/TS library).
- SSR capability for internet-accessible, SEO-friendly pages.
- Large component ecosystem for financial UI patterns.
- Familiarity / hiring pool.

Angular was removed from consideration: heavier framework overhead, smaller component ecosystem
for financial dashboards, and `ngx-echarts` is not TradingView Lightweight Charts.

## Decision
Use **Next.js 14** (App Router) + **React** + **TypeScript**.

- Next.js App Router gives SSR/SSG for public pages (landing, login) and client components
  for the interactive dashboard.
- TradingView Lightweight Charts has first-class React wrapper support.
- `react-grid-layout` (see ADR-009) is React-native and works directly.
- TypeScript aligns with the Pydantic API schemas — response types can be auto-generated
  from the OpenAPI spec.

Since the Brain/Skin contract (ADR-002) is fully defined by the `ChartSeries` API response,
this frontend switch costs nothing on the backend.

## Consequences

### Good
- Largest React ecosystem in existence — charting, data grid, form, and animation libraries.
- OpenAPI → TypeScript client generation (e.g., `openapi-ts`) gives type-safe API calls.
- Next.js file-based routing simplifies the multi-view dashboard structure.
- React Server Components can pre-fetch non-interactive data (e.g., market overview) server-side.

### Bad
- Next.js App Router is more opinionated than plain React — some learning curve.
- SSR for heavily real-time WebSocket-driven widgets doesn't add value; those must be client components.
- `apps/web/` scaffold needs to be created from scratch (no Angular scaffold to migrate from).
