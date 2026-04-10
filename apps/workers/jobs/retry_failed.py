from __future__ import annotations

import argparse
from datetime import datetime, timezone
from pathlib import Path

from market_data.config import load_job_config
from market_data.duckdb_meta import DuckDbMetaStore
from market_data.ibkr_client import IbkrHistoricalClient
from market_data.models import Instrument
from market_data.parquet_store import ParquetStore
from market_data.time_utils import utc_year_start
from market_data.windowing import TimeWindow

from .backfill import _process_year_window


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _failed_slice_to_window(year: int, now_utc: datetime) -> TimeWindow | None:
    start = utc_year_start(year)
    end = min(utc_year_start(year + 1), now_utc)
    if start >= end:
        return None
    return TimeWindow(start_utc=start, end_utc=end)


def run_retry_failed() -> None:
    cfg = load_job_config(_repo_root() / "config" / "market_data.backfill.yaml")
    run_retry_failed_with_options(dry_run=getattr(cfg, "dry_run", False))


def run_retry_failed_with_options(dry_run: bool = False) -> None:
    root = _repo_root()
    cfg = load_job_config(root / "config" / "market_data.backfill.yaml")
    meta = DuckDbMetaStore(root / cfg.storage.duckdb_path)
    ibkr_client = IbkrHistoricalClient.from_ibkr_config(cfg.ibkr, rate_limits=cfg.rate_limits)
    parquet_store = ParquetStore(root / cfg.storage.parquet_root)

    run_id = "dry-run"
    if not dry_run:
        meta.init_schema()
        run_id = meta.start_job_run(job_name="retry_failed_backfill", mode="backfill")

    try:
        failed_slices = meta.list_backfill_slices(status="FAILED", frequency=cfg.frequency.name)
        print(f"[{datetime.now(timezone.utc).isoformat()}] retry_failed started (run_id={run_id})")
        if dry_run:
            print("mode: DRY RUN (no parquet/db writes)")
        print(f"failed slices found: {len(failed_slices)}")

        processed_count = 0
        failed_count = 0
        now_utc = datetime.now(timezone.utc)

        for item in failed_slices:
            window = _failed_slice_to_window(item["year"], now_utc)
            if window is None:
                continue

            instrument = Instrument(
                symbol=item["symbol"],
                exchange=item["exchange"],
                asset_class=item["asset_class"],
                priority=True,
            )

            rows_written, page_errors = _process_year_window(
                run_id=run_id,
                instrument=instrument,
                frequency=cfg.frequency.name,
                bar_size=cfg.frequency.ibkr_bar_size,
                what_to_show=cfg.ibkr.what_to_show,
                use_rth=cfg.ibkr.use_regular_trading_hours,
                window=window,
                ibkr_client=ibkr_client,
                parquet_store=parquet_store,
                meta=meta,
                dry_run=dry_run,
            )
            processed_count += rows_written
            failed_count += page_errors

        if not dry_run:
            meta.finish_job_run(
                run_id=run_id,
                status="COMPLETE",
                processed_count=processed_count,
                failed_count=failed_count,
            )
    except Exception as exc:
        if not dry_run:
            meta.append_job_error(run_id=run_id, scope="job", error_message=str(exc))
            meta.finish_job_run(run_id=run_id, status="FAILED", processed_count=0, failed_count=1, notes=str(exc))
        raise


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Retry failed backfill slices.")
    parser.add_argument("--dry-run", action="store_true", help="Run without writing parquet/metadata.")
    args = parser.parse_args()
    run_retry_failed_with_options(dry_run=args.dry_run)
