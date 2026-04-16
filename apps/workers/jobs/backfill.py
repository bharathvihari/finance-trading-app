import argparse
from datetime import datetime, timedelta, timezone
from pathlib import Path
from time import monotonic

import pandas as pd

from market_data.config import load_job_config
from market_data.duckdb_meta import DuckDbMetaStore
from market_data.ibkr_client import HistoricalRequest, IbkrClientError, IbkrHistoricalClient
from market_data.logger import StructuredLogger
from market_data.parquet_store import ParquetStore
from market_data.universe_loader import load_universe
from market_data.windowing import paginated_windows_backward, yearly_windows_newest_to_oldest

BACKFILL_PAGE_DAYS = 1300


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _rows_to_frame(rows: list[dict]) -> pd.DataFrame:
    if not rows:
        return pd.DataFrame()
    return pd.DataFrame(rows)


def _process_year_window(
    run_id: str,
    instrument,
    frequency: str,
    bar_size: str,
    what_to_show: str,
    use_rth: bool,
    window,
    ibkr_client: IbkrHistoricalClient,
    parquet_store: ParquetStore,
    meta: DuckDbMetaStore,
    dry_run: bool = False,
    logger: StructuredLogger | None = None,
) -> tuple[int, int]:
    """Process a single year window and persist progress incrementally."""
    state = None
    if not dry_run:
        state = meta.get_slice_state(
            symbol=instrument.symbol,
            exchange=instrument.exchange,
            asset_class=instrument.asset_class,
            frequency=frequency,
            year=window.year,
        )
    if state and state.get("status") == "COMPLETE":
        if logger:
            logger.log("year_skipped", symbol=instrument.symbol, exchange=instrument.exchange,
                       asset_class=instrument.asset_class, year=window.year, reason="already_complete")
        return (0, 0)

    resume_from = state.get("earliest_downloaded_ts") if state else None

    if not dry_run:
        meta.upsert_slice_progress(
            symbol=instrument.symbol,
            exchange=instrument.exchange,
            asset_class=instrument.asset_class,
            frequency=frequency,
            year=window.year,
            status="IN_PROGRESS",
            earliest_downloaded_ts=state.get("earliest_downloaded_ts") if state else None,
            latest_downloaded_ts=state.get("latest_downloaded_ts") if state else None,
        )

    written_rows = 0
    page_errors = 0

    for page in paginated_windows_backward(
        window=window,
        page_span=timedelta(days=BACKFILL_PAGE_DAYS),
        resume_from_utc=resume_from,
    ):
        request = HistoricalRequest(
            instrument=instrument,
            start_utc=page.start_utc,
            end_utc=page.end_utc,
            bar_size=bar_size,
            what_to_show=what_to_show,
            use_regular_trading_hours=use_rth,
            frequency=frequency,
        )

        _t0 = monotonic()
        try:
            bars = ibkr_client.fetch_bars(request)
            _elapsed_ms = int((monotonic() - _t0) * 1000)
        except IbkrClientError as exc:
            _elapsed_ms = int((monotonic() - _t0) * 1000)
            page_errors += 1
            if logger:
                logger.log("page_error", symbol=instrument.symbol, exchange=instrument.exchange,
                           asset_class=instrument.asset_class, year=window.year,
                           start=page.start_utc.date().isoformat(), end=page.end_utc.date().isoformat(),
                           error=str(exc), elapsed_ms=_elapsed_ms)
            if not dry_run:
                meta.append_job_error(
                    run_id=run_id,
                    scope="slice_page",
                    symbol=instrument.symbol,
                    exchange=instrument.exchange,
                    year=window.year,
                    error_message=str(exc),
                )
                meta.upsert_slice_progress(
                    symbol=instrument.symbol,
                    exchange=instrument.exchange,
                    asset_class=instrument.asset_class,
                    frequency=frequency,
                    year=window.year,
                    status="FAILED",
                    last_error=str(exc),
                )
            continue

        if not bars:
            continue

        if logger:
            logger.log("page_ok", symbol=instrument.symbol, exchange=instrument.exchange,
                       asset_class=instrument.asset_class, year=window.year,
                       start=page.start_utc.date().isoformat(), end=page.end_utc.date().isoformat(),
                       rows=len(bars), elapsed_ms=_elapsed_ms)

        frame = _rows_to_frame(bars)
        if not dry_run:
            parquet_store.write_partition(frame)

        min_ts = frame["timestamp"].min().to_pydatetime()
        max_ts = frame["timestamp"].max().to_pydatetime()
        written_rows += len(frame)

        if not dry_run:
            meta.upsert_slice_progress(
                symbol=instrument.symbol,
                exchange=instrument.exchange,
                asset_class=instrument.asset_class,
                frequency=frequency,
                year=window.year,
                status="IN_PROGRESS",
                earliest_downloaded_ts=min_ts,
                latest_downloaded_ts=max_ts,
                last_success_request_at=datetime.now(timezone.utc),
                last_error=None,
            )

    final_state = state
    if not dry_run:
        final_state = meta.get_slice_state(
            symbol=instrument.symbol,
            exchange=instrument.exchange,
            asset_class=instrument.asset_class,
            frequency=frequency,
            year=window.year,
        )

    is_complete = True
    if final_state and final_state.get("earliest_downloaded_ts") is not None:
        earliest = final_state["earliest_downloaded_ts"]
        is_complete = earliest <= window.start_utc

    if is_complete and not dry_run:
        meta.upsert_slice_progress(
            symbol=instrument.symbol,
            exchange=instrument.exchange,
            asset_class=instrument.asset_class,
            frequency=frequency,
            year=window.year,
            status="COMPLETE",
            last_error=None,
        )

    if written_rows > 0 and not dry_run:
        current_min = final_state.get("earliest_downloaded_ts") if final_state else None
        current_max = final_state.get("latest_downloaded_ts") if final_state else None
        meta.upsert_coverage(
            symbol=instrument.symbol,
            exchange=instrument.exchange,
            asset_class=instrument.asset_class,
            frequency=frequency,
            min_ts=current_min,
            max_ts=current_max,
            row_count=written_rows,
        )

    if logger:
        logger.log("year_end", symbol=instrument.symbol, exchange=instrument.exchange,
                   asset_class=instrument.asset_class, year=window.year,
                   rows_written=written_rows, page_errors=page_errors,
                   status="complete" if is_complete else "partial")

    return (written_rows, page_errors)


def run_backfill(dry_run: bool | None = None) -> None:
    root = _repo_root()
    cfg = load_job_config(root / "config" / "market_data.backfill.yaml")
    meta = DuckDbMetaStore(root / cfg.storage.duckdb_path)
    ibkr_client = IbkrHistoricalClient.from_ibkr_config(cfg.ibkr, rate_limits=cfg.rate_limits)
    parquet_store = ParquetStore(root / cfg.storage.parquet_root)
    universe = load_universe(cfg)

    is_dry_run = getattr(cfg, "dry_run", False) if dry_run is None else dry_run

    run_id = "dry-run"
    if not is_dry_run:
        meta.init_schema()
        run_id = meta.start_job_run(job_name=cfg.job_name, mode=cfg.mode)

    logger = StructuredLogger(job_name=cfg.job_name, log_dir=root / "data" / "logs")
    logger.set_run_id(run_id)

    try:
        print(f"[{datetime.now(timezone.utc).isoformat()}] backfill job started (run_id={run_id})")
        if is_dry_run:
            print("mode: DRY RUN (no parquet/db writes)")
        print(f"duckdb metadata: {root / cfg.storage.duckdb_path}")
        print(f"ibkr gateway: {ibkr_client.runtime.host}:{ibkr_client.runtime.port} mode={ibkr_client.runtime.gateway_mode}")

        logger.log("job_start", dry_run=is_dry_run, mode=cfg.mode,
                   ibkr_host=ibkr_client.runtime.host, ibkr_port=ibkr_client.runtime.port,
                   gateway_mode=ibkr_client.runtime.gateway_mode)

        processed_count = 0
        failed_count = 0

        for instrument in universe.instruments:
            try:
                head = ibkr_client.get_head_timestamp(instrument)
            except IbkrClientError as exc:
                failed_count += 1
                logger.log("symbol_head_error", symbol=instrument.symbol, exchange=instrument.exchange,
                           asset_class=instrument.asset_class, error=str(exc))
                if not is_dry_run:
                    meta.append_job_error(
                        run_id=run_id,
                        scope="instrument_head_timestamp",
                        symbol=instrument.symbol,
                        exchange=instrument.exchange,
                        error_message=str(exc),
                    )
                continue

            if head is None:
                continue

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
                    dry_run=is_dry_run,
                    logger=logger,
                )
                if rows_written > 0:
                    processed_count += rows_written
                failed_count += page_errors

        if not is_dry_run:
            meta.finish_job_run(
                run_id=run_id,
                status="COMPLETE",
                processed_count=processed_count,
                failed_count=failed_count,
            )
        logger.log("job_end", status="complete", processed=processed_count, failed=failed_count)
    except Exception as exc:
        logger.log("job_end", status="failed", error=str(exc))
        if not is_dry_run:
            meta.append_job_error(run_id=run_id, scope="job", error_message=str(exc))
            meta.finish_job_run(run_id=run_id, status="FAILED", processed_count=0, failed_count=1, notes=str(exc))
        raise
    finally:
        logger.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run historical backfill job.")
    parser.add_argument("--dry-run", action="store_true", help="Run without writing parquet/metadata.")
    args = parser.parse_args()
    run_backfill(dry_run=args.dry_run)
