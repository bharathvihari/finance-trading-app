# ADR-001: Tiered Market Data Storage (Postgres Hot + Parquet Cold)

## Status
Accepted

## Context
Daily bar data for the configured universe of instruments (equities + indices, multiple exchanges)
accumulates at roughly 250 trading days × N symbols per year. Accessing recent data — what a
live dashboard actually needs for chart rendering and portfolio valuation — should be fast
(< 500 ms p95). Accessing multi-year history for backtests or long-range charts can tolerate
a slightly higher latency. A single storage medium is a poor fit for both:

- Parquet is excellent for columnar scans over large date ranges but requires file I/O and
  DuckDB query planning — too slow for "what closed today" lookups.
- A relational DB is fast for recent point lookups but expensive to store decades of OHLCV data
  for hundreds of symbols.

## Decision
Split storage into two tiers separated by a rolling time boundary called the **hot cutoff**:

```
hot_cutoff = first day of the month (now − hot_window_months)
             (default: hot_window_months = 6)

bars timestamp >= hot_cutoff  →  Postgres  schema=market_data, table=daily_bars
bars timestamp <  hot_cutoff  →  Parquet   price-data/asset_class=.../exchange=.../
                                           frequency=.../year=.../part-*.parquet
```

The backfill and daily jobs split each fetched page using `_split_hot_cold_frames()` and write
to the appropriate store. A periodic archival job (`archive_cold_bars.py`) drains Postgres rows
that have aged past the cutoff into Parquet, keeping the hot tier bounded.

The API's `BarReader` reconstructs a unified response by routing to one or both stores depending
on whether the requested date range is entirely hot, entirely cold, or straddles the boundary.

## Consequences

### Good
- Dashboard chart loads for recent data hit Postgres with a simple indexed SELECT — fast.
- Long-range historical queries (5Y+) use DuckDB columnar scans over Parquet — efficient.
- The hot tier stays small (≤ 6 months × universe size) — no unbounded Postgres growth.
- Each tier is independently queryable for debugging and maintenance.
- `hot_window_months` is a single config knob — tunable without code changes.

### Bad
- The API must merge results from two stores for queries that straddle the boundary —
  slightly more complex than a single-store read.
- `hot_window_months` must be kept in sync between the backfill config and the API settings;
  drift causes the API to look in the wrong store for boundary-region data.
- The archival job must run periodically or the hot tier will grow beyond its intended bound.
- Parquet has no ACID guarantees; crash during write can leave partial files
  (mitigated by write-then-delete ordering and idempotent re-runs).
