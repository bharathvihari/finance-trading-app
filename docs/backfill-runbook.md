# Backfill & Daily Jobs Runbook

## Prerequisites

- Python environment with worker dependencies installed.
- Config files:
  - `config/market_data.backfill.yaml`
  - `config/market_data.daily.yaml`
- Optional but recommended for real runs:
  - DuckDB available
  - IBKR Gateway/TWS running

## Command Reference

Run from `apps/workers`.

```powershell
cd apps/workers

# Full historical backfill
python -m jobs.backfill

# Daily incremental update
python -m jobs.daily_refresh

# Retry failed backfill slices
python -m jobs.retry_failed

# Validate datasets and write reports
python -m jobs.validate_history
```

## Dry-Run Mode

Dry-run executes orchestration and fetch flow but avoids write side effects.

```powershell
python -m jobs.backfill --dry-run
python -m jobs.daily_refresh --dry-run
python -m jobs.retry_failed --dry-run
python -m jobs.validate_history --dry-run
```

Dry-run behavior:
- No Parquet writes.
- No DuckDB metadata writes (job runs/progress/coverage).
- Validation prints summary and skips report file output.

## Expected Outputs

- Backfill / daily / retry:
  - start line with UTC timestamp and `run_id`
  - gateway info line
  - dry-run banner when enabled
  - Structured JSONL logs in `data/logs/{job_name}/{YYYY-MM-DD}.jsonl`
- Validation:
  - summary printed
  - JSON/CSV report paths for non-dry runs

## Debug Logging (Request-Level)

To see detailed IBKR request/response logs (DEBUG level), set `LOGLEVEL`:

```bash
# Linux/macOS
export LOGLEVEL=DEBUG
python -m jobs.backfill

# Windows PowerShell
$env:LOGLEVEL = "DEBUG"
python -m jobs.backfill
```

Debug logs show:
- Every HTTP request (method, path, params, payload)
- Contract resolution (symbol → conid mappings, cache hits)
- History fetch calls (conid, period, bar size, row counts)
- Head timestamp discovery pagination
- Market snapshot requests with instrument lists

Debug logs go to stderr. Combine with job JSONL logs in `data/logs/` for complete observability.

## Failure Recovery

1. For failed symbol/year slices:
   - `python -m apps.workers.jobs.retry_failed`
2. For missing baseline in daily job:
   - run backfill first, then rerun daily job
3. For validation issues:
   - inspect `data/reports/validation-*.json` and `validation-*.csv`
4. For IBKR connectivity issues:
   - verify gateway host/port in config
   - rerun with `--dry-run` to confirm orchestration path without writes
