# ADR-003: DuckDB for Metadata Store and Parquet Query Engine

## Status
Accepted

## Context
Two distinct needs emerged during the backfill design:

1. **Job metadata** — tracking which (symbol, exchange, year) slices have been downloaded,
   their completion status, timestamps, and errors. This needs ACID writes and point lookups.
2. **Historical bar queries** — reading OHLCV data from hundreds of Parquet files scattered
   across hive-partitioned directories. This needs efficient columnar scans with predicate pushdown.

Postgres could handle (1) but is a poor fit for (2) since it cannot natively read Parquet.
A Python pandas solution for (2) would load all partition files into memory.

## Decision
Use **DuckDB** for both:
- As a lightweight embedded SQL database for job metadata tables
  (`job_runs`, `backfill_slices`, `data_coverage`, `job_errors`, `split_checks`).
- As a query engine for cold Parquet reads via `read_parquet('**/*.parquet', hive_partitioning=true)`.

DuckDB runs in-process (no separate server), reads Parquet with predicate and partition pruning,
and supports standard SQL — including `ON CONFLICT` upserts used by the metadata store.

Physical files:
- `data/duckdb/market_data.duckdb` — metadata only (small, < 100 MB typically)
- `data/parquet/price-data/...` — the actual bar data (queried via DuckDB, not stored in it)

## Consequences

### Good
- No separate metadata database server to run or backup.
- Parquet queries get partition pruning (e.g., `year=2023` directory skipped if not in range)
  and columnar pushdown without loading data into memory.
- Same SQL dialect for both metadata and data queries — one mental model.
- DuckDB is file-based; backups are a file copy.

### Bad
- DuckDB has a single-writer limitation — concurrent writes from multiple processes require
  coordination (not currently an issue; jobs are single-process).
- The metadata DB and the Parquet files are separate; a partial write can leave them
  temporarily inconsistent (mitigated by the slice-progress tracking design).
- DuckDB's in-memory query model means very large Parquet scans (full history, all symbols)
  can be memory-intensive on small machines.
