import argparse
from datetime import datetime, timezone
from pathlib import Path

from market_data.config import load_job_config
from market_data.corporate_actions import fetch_splits
from market_data.duckdb_meta import DuckDbMetaStore
from market_data.ibkr_client import IbkrClientError, IbkrHistoricalClient
from market_data.logger import StructuredLogger
from market_data.parquet_store import ParquetStore
from market_data.universe_loader import load_universe
from market_data.windowing import yearly_windows_newest_to_oldest

from jobs.backfill import _process_year_window


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def run_adjust_splits(dry_run: bool = False) -> None:
    root = _repo_root()
    cfg = load_job_config(root / "config" / "market_data.backfill.yaml")
    meta = DuckDbMetaStore(root / cfg.storage.duckdb_path)
    parquet_store = ParquetStore(root / cfg.storage.parquet_root)
    ibkr_client = IbkrHistoricalClient.from_ibkr_config(cfg.ibkr, rate_limits=cfg.rate_limits)
    universe = load_universe(cfg)

    meta.init_schema()
    meta.init_split_check_schema()

    run_id = "dry-run"
    if not dry_run:
        run_id = meta.start_job_run(job_name="adjust_splits", mode="adjust")

    logger = StructuredLogger(job_name="adjust_splits", log_dir=root / "data" / "logs")
    logger.set_run_id(run_id)

    print(f"[{datetime.now(timezone.utc).isoformat()}] split adjustment job started (run_id={run_id})")
    if dry_run:
        print("mode: DRY RUN (no parquet/db writes)")

    processed_count = 0
    failed_count = 0

    try:
        logger.log("job_start", dry_run=dry_run,
                   ibkr_host=ibkr_client.runtime.host, ibkr_port=ibkr_client.runtime.port,
                   gateway_mode=ibkr_client.runtime.gateway_mode)

        for instrument in universe.instruments:
            if instrument.asset_class == "index":
                continue

            last_check = meta.get_last_split_check(
                symbol=instrument.symbol,
                exchange=instrument.exchange,
                asset_class=instrument.asset_class,
            )
            is_first_check = last_check is None
            since = last_check.get("last_split_date") if last_check else None
            checked_at = datetime.now(timezone.utc)

            try:
                splits = fetch_splits(
                    symbol=instrument.symbol,
                    exchange=instrument.exchange,
                    asset_class=instrument.asset_class,
                    since=since,
                )
            except Exception as exc:
                failed_count += 1
                logger.log("split_fetch_error", symbol=instrument.symbol, exchange=instrument.exchange,
                           asset_class=instrument.asset_class, error=str(exc))
                if not dry_run:
                    meta.append_job_error(
                        run_id=run_id,
                        scope="fetch_splits",
                        symbol=instrument.symbol,
                        exchange=instrument.exchange,
                        error_message=str(exc),
                    )
                continue

            if not splits:
                if not dry_run:
                    meta.upsert_split_check(
                        symbol=instrument.symbol,
                        exchange=instrument.exchange,
                        asset_class=instrument.asset_class,
                        last_checked_at=checked_at,
                        last_split_date=since,
                    )
                continue

            latest_split = max(splits, key=lambda s: s.split_date)

            if is_first_check:
                # Initial run: data from backfill is already adjusted for historical splits.
                # Just record the latest known split date without re-backfilling.
                print(f"  {instrument.symbol}: first check — recording {len(splits)} historical split(s), no re-backfill needed")
                logger.log("first_check_recorded", symbol=instrument.symbol, exchange=instrument.exchange,
                           asset_class=instrument.asset_class, historical_splits=len(splits),
                           latest_split_date=latest_split.split_date.date().isoformat(),
                           latest_split_ratio=latest_split.ratio)
                if not dry_run:
                    meta.upsert_split_check(
                        symbol=instrument.symbol,
                        exchange=instrument.exchange,
                        asset_class=instrument.asset_class,
                        last_checked_at=checked_at,
                        last_split_date=latest_split.split_date,
                    )
                continue

            # New split(s) after the last known split → wipe and re-backfill.
            print(f"  {instrument.symbol}: {len(splits)} new split(s) detected — wiping and re-backfilling")
            for s in splits:
                print(f"    {s.ratio}:1 split on {s.split_date.date()}")
                logger.log("split_detected", symbol=instrument.symbol, exchange=instrument.exchange,
                           asset_class=instrument.asset_class,
                           split_date=s.split_date.date().isoformat(), ratio=s.ratio)

            if dry_run:
                continue

            deleted = parquet_store.delete_symbol_data(
                symbol=instrument.symbol,
                exchange=instrument.exchange,
                asset_class=instrument.asset_class,
                frequency=cfg.frequency.name,
            )
            print(f"    deleted {deleted} existing rows from parquet")
            logger.log("wipe_complete", symbol=instrument.symbol, exchange=instrument.exchange,
                       asset_class=instrument.asset_class, rows_deleted=deleted)

            meta.reset_slices_for_symbol(
                symbol=instrument.symbol,
                exchange=instrument.exchange,
                asset_class=instrument.asset_class,
                frequency=cfg.frequency.name,
            )

            try:
                head = ibkr_client.get_head_timestamp(instrument)
            except IbkrClientError as exc:
                failed_count += 1
                logger.log("symbol_head_error", symbol=instrument.symbol, exchange=instrument.exchange,
                           asset_class=instrument.asset_class, error=str(exc))
                meta.append_job_error(
                    run_id=run_id,
                    scope="head_timestamp",
                    symbol=instrument.symbol,
                    exchange=instrument.exchange,
                    error_message=str(exc),
                )
                continue

            if head is None:
                continue

            logger.log("rebackfill_start", symbol=instrument.symbol, exchange=instrument.exchange,
                       asset_class=instrument.asset_class, head_ts=head.date().isoformat())

            symbol_rows = 0
            latest = datetime.now(timezone.utc)
            for window in yearly_windows_newest_to_oldest(head, latest):
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
                    dry_run=False,
                    logger=logger,
                )
                symbol_rows += rows_written
                processed_count += rows_written
                failed_count += page_errors

            logger.log("rebackfill_complete", symbol=instrument.symbol, exchange=instrument.exchange,
                       asset_class=instrument.asset_class, rows_written=symbol_rows)

            meta.upsert_split_check(
                symbol=instrument.symbol,
                exchange=instrument.exchange,
                asset_class=instrument.asset_class,
                last_checked_at=checked_at,
                last_split_date=latest_split.split_date,
            )

        if not dry_run:
            meta.finish_job_run(
                run_id=run_id,
                status="COMPLETE",
                processed_count=processed_count,
                failed_count=failed_count,
            )
        logger.log("job_end", status="complete", processed=processed_count, failed=failed_count)

    except Exception as exc:
        logger.log("job_end", status="failed", error=str(exc))
        if not dry_run:
            meta.append_job_error(run_id=run_id, scope="job", error_message=str(exc))
            meta.finish_job_run(run_id=run_id, status="FAILED", processed_count=0, failed_count=1, notes=str(exc))
        raise
    finally:
        logger.close()

    print(f"[{datetime.now(timezone.utc).isoformat()}] done — processed={processed_count} failed={failed_count}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Detect stock splits and re-backfill adjusted data from IBKR.")
    parser.add_argument("--dry-run", action="store_true", help="Run without writing parquet/metadata.")
    args = parser.parse_args()
    run_adjust_splits(dry_run=args.dry_run)
