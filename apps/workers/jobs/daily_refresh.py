import argparse
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

from market_data.config import load_job_config
from market_data.dedup import deduplicate_bars
from market_data.duckdb_meta import DuckDbMetaStore
from market_data.ibkr_client import HistoricalRequest, IbkrClientError, IbkrHistoricalClient
from market_data.parquet_store import ParquetStore
from market_data.universe_loader import load_universe

INCREMENTAL_DELTA = timedelta(milliseconds=1)


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _resolve_latest_timestamp(meta, parquet_store, instrument, frequency: str) -> datetime | None:
    try:
        latest = meta.get_latest_covered_timestamp(
            symbol=instrument.symbol,
            exchange=instrument.exchange,
            asset_class=instrument.asset_class,
            frequency=frequency,
        )
        if latest is not None:
            return latest
    except ModuleNotFoundError:
        pass

    try:
        return parquet_store.latest_timestamp(
            symbol=instrument.symbol,
            exchange=instrument.exchange,
            asset_class=instrument.asset_class,
            frequency=frequency,
        )
    except ModuleNotFoundError:
        return None


def _build_incremental_window(latest_local: datetime, now_utc: datetime) -> tuple[datetime, datetime] | None:
    start = latest_local + INCREMENTAL_DELTA
    if start >= now_utc:
        return None
    return start, now_utc


def run_incremental_refresh(dry_run: bool | None = None) -> None:
    root = _repo_root()
    cfg = load_job_config(root / "config" / "market_data.daily.yaml")
    meta = DuckDbMetaStore(root / cfg.storage.duckdb_path)
    ibkr_client = IbkrHistoricalClient.from_ibkr_config(cfg.ibkr, rate_limits=cfg.rate_limits)
    parquet_store = ParquetStore(root / cfg.storage.parquet_root)
    universe = load_universe(cfg)

    is_dry_run = getattr(cfg, "dry_run", False) if dry_run is None else dry_run
    run_id = "dry-run"
    if not is_dry_run:
        meta.init_schema()
        run_id = meta.start_job_run(job_name=cfg.job_name, mode=cfg.mode)

    try:
        print(f"[{datetime.now(timezone.utc).isoformat()}] daily refresh started (run_id={run_id})")
        if is_dry_run:
            print("mode: DRY RUN (no parquet/db writes)")
        print(f"duckdb metadata: {root / cfg.storage.duckdb_path}")
        print(f"ibkr gateway: {ibkr_client.runtime.host}:{ibkr_client.runtime.port} mode={ibkr_client.runtime.gateway_mode}")

        processed_count = 0
        failed_count = 0
        now_utc = datetime.now(timezone.utc)

        for instrument in universe.instruments:
            latest = _resolve_latest_timestamp(meta, parquet_store, instrument, cfg.frequency.name)
            if latest is None:
                if not is_dry_run:
                    meta.append_job_error(
                        run_id=run_id,
                        scope="incremental_no_baseline",
                        symbol=instrument.symbol,
                        exchange=instrument.exchange,
                        error_message="No local baseline found. Run backfill first.",
                    )
                continue

            window = _build_incremental_window(latest, now_utc)
            if window is None:
                continue

            request = HistoricalRequest(
                instrument=instrument,
                start_utc=window[0],
                end_utc=window[1],
                bar_size=cfg.frequency.ibkr_bar_size,
                what_to_show=cfg.ibkr.what_to_show,
                use_regular_trading_hours=cfg.ibkr.use_regular_trading_hours,
                frequency=cfg.frequency.name,
            )

            try:
                bars = ibkr_client.fetch_bars(request)
            except IbkrClientError as exc:
                failed_count += 1
                if not is_dry_run:
                    meta.append_job_error(
                        run_id=run_id,
                        scope="incremental_fetch",
                        symbol=instrument.symbol,
                        exchange=instrument.exchange,
                        error_message=str(exc),
                    )
                continue

            if not bars:
                continue

            frame = pd.DataFrame(bars)
            frame = deduplicate_bars(frame)
            if not is_dry_run:
                parquet_store.write_partition(frame)

            min_ts = frame["timestamp"].min().to_pydatetime()
            max_ts = frame["timestamp"].max().to_pydatetime()
            row_count = len(frame)

            if not is_dry_run:
                meta.upsert_coverage(
                    symbol=instrument.symbol,
                    exchange=instrument.exchange,
                    asset_class=instrument.asset_class,
                    frequency=cfg.frequency.name,
                    min_ts=min_ts,
                    max_ts=max_ts,
                    row_count=row_count,
                )
            processed_count += row_count

        if not is_dry_run:
            meta.finish_job_run(
                run_id=run_id,
                status="COMPLETE",
                processed_count=processed_count,
                failed_count=failed_count,
            )
    except Exception as exc:
        if not is_dry_run:
            meta.append_job_error(run_id=run_id, scope="job", error_message=str(exc))
            meta.finish_job_run(run_id=run_id, status="FAILED", processed_count=0, failed_count=1, notes=str(exc))
        raise


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run daily incremental refresh job.")
    parser.add_argument("--dry-run", action="store_true", help="Run without writing parquet/metadata.")
    args = parser.parse_args()
    run_incremental_refresh(dry_run=args.dry_run)
