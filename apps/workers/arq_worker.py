"""
ARQ async job worker.

Runs as a long-lived process alongside the API server. The API enqueues
jobs via an ArqRedis pool; this worker picks them up and executes them.

Tasks:
  fetch_bars_task        — on-demand IBKR fetch when a symbol has no local data
  precompute_metrics_task — nightly rolling metric pre-aggregation (also a cron)

Cron schedule:
  precompute_metrics_task  runs daily at 02:00 UTC (after market data is refreshed)
  daily_refresh_task       runs daily at 22:00 UTC (after US market close + buffer)

Start worker:
    cd apps/workers
    python arq_worker.py
  or via Docker:
    CMD ["python", "arq_worker.py"]
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path

from arq import cron
from arq.connections import RedisSettings

logger = logging.getLogger(__name__)

_REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO_ROOT / "apps" / "workers"))
sys.path.insert(0, str(_REPO_ROOT / "apps" / "api"))


# ---------------------------------------------------------------------------
# Task definitions
# ---------------------------------------------------------------------------

async def fetch_bars_task(
    ctx: dict,
    symbol: str,
    exchange: str,
    asset_class: str = "equity",
    frequency: str = "daily",
) -> dict:
    """
    On-demand IBKR historical bar fetch for a symbol not in local storage.

    Enqueued by the API when GET /bars/{symbol} returns no data.
    After completion, the API WebSocket pushes a notification to the
    requesting user's channel.
    """
    from market_data.config import load_job_config
    from market_data.ibkr_client import IbkrHistoricalClient
    from market_data.parquet_store import ParquetStore
    from market_data.postgres_store import PostgresBarStore
    from jobs.backfill import _process_year_window, _hot_window_cutoff
    from market_data.duckdb_meta import DuckDbMetaStore
    from market_data.logger import StructuredLogger
    from market_data.windowing import yearly_windows_newest_to_oldest
    from market_data.universe_loader import Instrument
    from datetime import datetime, timezone

    logger.info("fetch_bars_task: symbol=%s exchange=%s", symbol, exchange)

    cfg = load_job_config(_REPO_ROOT / "config" / "market_data.backfill.yaml")
    meta = DuckDbMetaStore(_REPO_ROOT / cfg.storage.duckdb_path)
    ibkr_client = IbkrHistoricalClient.from_ibkr_config(cfg.ibkr, rate_limits=cfg.rate_limits)
    parquet_store = ParquetStore(_REPO_ROOT / cfg.storage.parquet_root)
    hot_store = PostgresBarStore.from_config(cfg.postgres) if cfg.postgres.enabled else None
    hot_cutoff = _hot_window_cutoff(datetime.now(timezone.utc), cfg.postgres.hot_window_months)
    worker_logger = StructuredLogger("arq_fetch_bars", _REPO_ROOT / "data" / "logs")

    instrument = Instrument(symbol=symbol, exchange=exchange, asset_class=asset_class)

    try:
        head = ibkr_client.get_head_timestamp(instrument)
        if head is None:
            return {"status": "no_data", "symbol": symbol}

        run_id = meta.start_job_run(job_name="arq_fetch_bars", mode="backfill")
        total_rows = 0
        for window in yearly_windows_newest_to_oldest(head, datetime.now(timezone.utc)):
            rows, _ = _process_year_window(
                run_id=run_id, instrument=instrument, frequency=frequency,
                bar_size=cfg.frequency.ibkr_bar_size,
                what_to_show=cfg.ibkr.what_to_show,
                use_rth=cfg.ibkr.use_regular_trading_hours,
                window=window, ibkr_client=ibkr_client,
                parquet_store=parquet_store, meta=meta,
                hot_store=hot_store, hot_cutoff_utc=hot_cutoff,
                logger=worker_logger,
            )
            total_rows += rows

        meta.finish_job_run(run_id=run_id, status="COMPLETE", processed_count=total_rows)
        return {"status": "complete", "symbol": symbol, "rows": total_rows}
    except Exception as exc:
        logger.error("fetch_bars_task failed: %s", exc)
        return {"status": "failed", "symbol": symbol, "error": str(exc)}
    finally:
        worker_logger.close()


async def precompute_metrics_task(ctx: dict) -> dict:
    """
    Nightly pre-aggregation of rolling performance metrics for all symbols.
    Triggered by cron at 02:00 UTC and also callable on-demand.
    """
    logger.info("precompute_metrics_task: starting")
    try:
        from jobs.precompute_metrics import run_precompute
        run_precompute()
        return {"status": "complete"}
    except Exception as exc:
        logger.error("precompute_metrics_task failed: %s", exc)
        return {"status": "failed", "error": str(exc)}


async def daily_refresh_task(ctx: dict) -> dict:
    """
    Daily incremental bar data refresh. Triggered by cron at 22:00 UTC
    (after US market close + settlement buffer).
    """
    logger.info("daily_refresh_task: starting")
    try:
        from jobs.daily_refresh import run_daily_refresh
        run_daily_refresh()
        return {"status": "complete"}
    except Exception as exc:
        logger.error("daily_refresh_task failed: %s", exc)
        return {"status": "failed", "error": str(exc)}


# ---------------------------------------------------------------------------
# Worker settings
# ---------------------------------------------------------------------------

import os

class WorkerSettings:
    functions = [fetch_bars_task, precompute_metrics_task, daily_refresh_task]

    cron_jobs = [
        cron(daily_refresh_task,       hour=22, minute=0),   # 22:00 UTC after US close
        cron(precompute_metrics_task,  hour=2,  minute=0),   # 02:00 UTC nightly
    ]

    redis_settings = RedisSettings(
        host=os.getenv("REDIS_HOST", "127.0.0.1"),
        port=int(os.getenv("REDIS_PORT", "6379")),
    )

    max_jobs = 4
    job_timeout = 3600          # 1 hour max per job
    keep_result = 86400         # keep job results in Redis for 24 hours


if __name__ == "__main__":
    import asyncio
    from arq import run_worker
    logging.basicConfig(level=logging.INFO)
    asyncio.run(run_worker(WorkerSettings))
