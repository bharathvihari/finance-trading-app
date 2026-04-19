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
from market_data.windowing import (
    paginated_windows_backward,
    previous_cursor_from_oldest_bar,
    yearly_windows_newest_to_oldest,
)

BACKFILL_PAGE_DAYS = 1300
EXCHANGE_LOOKBACK_DAYS = 10


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _rows_to_frame(rows: list[dict]) -> pd.DataFrame:
    if not rows:
        return pd.DataFrame()
    return pd.DataFrame(rows)


def _normalize_trading_date(ts: datetime) -> datetime:
    """Normalize trading timestamp to midnight UTC for stable comparisons."""
    return datetime(ts.year, ts.month, ts.day, tzinfo=timezone.utc)


def _find_instrument(universe, exchange: str, symbol: str, asset_class: str | None = None):
    symbol_upper = symbol.strip().upper()
    if not symbol_upper:
        return None
    for inst in universe.instruments:
        if inst.exchange != exchange:
            continue
        if inst.symbol != symbol_upper:
            continue
        if asset_class is not None and inst.asset_class != asset_class:
            continue
        return inst
    return None


def _exchange_reference_candidates(cfg, universe, exchange: str):
    exchange_cfg = cfg.universe.exchanges.get(exchange)
    if exchange_cfg is None:
        return []

    candidates = []
    seen: set[tuple[str, str, str]] = set()

    def _append(inst) -> None:
        if inst is None:
            return
        key = (inst.exchange, inst.asset_class, inst.symbol)
        if key in seen:
            return
        seen.add(key)
        candidates.append(inst)

    configured_ref = (exchange_cfg.reference_symbol or "").strip().upper()
    if configured_ref:
        if configured_ref in {s.strip().upper() for s in [*exchange_cfg.priority_indices, *exchange_cfg.indices]}:
            _append(_find_instrument(universe, exchange, configured_ref, asset_class="index"))
        else:
            _append(_find_instrument(universe, exchange, configured_ref, asset_class="equity"))
            _append(_find_instrument(universe, exchange, configured_ref, asset_class="index"))

    first_index_symbol = next(
        (
            symbol.strip().upper()
            for symbol in [*exchange_cfg.priority_indices, *exchange_cfg.indices]
            if symbol.strip()
        ),
        None,
    )
    if first_index_symbol:
        _append(_find_instrument(universe, exchange, first_index_symbol, asset_class="index"))

    first_stock_symbol = next((symbol.strip().upper() for symbol in exchange_cfg.symbols if symbol.strip()), None)
    if first_stock_symbol:
        _append(_find_instrument(universe, exchange, first_stock_symbol, asset_class="equity"))

    return candidates


def _resolve_exchange_last_traded_date_from_candidate(
    ibkr_client: IbkrHistoricalClient,
    instrument,
    frequency: str,
    bar_size: str,
    what_to_show: str,
    use_rth: bool,
    as_of_utc: datetime,
    logger: StructuredLogger | None = None,
) -> datetime | None:
    if instrument is None:
        return None

    window_start = as_of_utc - timedelta(days=EXCHANGE_LOOKBACK_DAYS)
    request = HistoricalRequest(
        instrument=instrument,
        start_utc=window_start,
        end_utc=as_of_utc,
        bar_size=bar_size,
        what_to_show=what_to_show,
        use_regular_trading_hours=use_rth,
        frequency=frequency,
    )
    try:
        bars = ibkr_client.fetch_bars(request)
    except IbkrClientError as exc:
        if logger:
            logger.log(
                "exchange_last_traded_error",
                exchange=instrument.exchange,
                symbol=instrument.symbol,
                asset_class=instrument.asset_class,
                error=str(exc),
            )
        return None

    if not bars:
        if logger:
            logger.log(
                "exchange_last_traded_empty",
                exchange=instrument.exchange,
                symbol=instrument.symbol,
                asset_class=instrument.asset_class,
            )
        return None

    last_ts = max(row["timestamp"] for row in bars if "timestamp" in row)
    return _normalize_trading_date(last_ts)


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
    touched_lower_bound = False

    for page in paginated_windows_backward(
        window=window,
        page_span=timedelta(days=BACKFILL_PAGE_DAYS),
        resume_from_utc=resume_from,
    ):
        if page.start_utc == window.start_utc:
            touched_lower_bound = True

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
            meta.upsert_parquet_symbol(
                symbol=instrument.symbol,
                exchange=instrument.exchange,
                asset_class=instrument.asset_class,
                frequency=frequency,
            )

        min_ts = frame["timestamp"].min().to_pydatetime()
        max_ts = frame["timestamp"].max().to_pydatetime()
        written_rows += len(frame)

        if not dry_run:
            # Store a resume frontier instead of the raw earliest bar to avoid
            # re-requesting the same inclusive end timestamp repeatedly.
            earliest_frontier = previous_cursor_from_oldest_bar(min_ts)
            meta.upsert_slice_progress(
                symbol=instrument.symbol,
                exchange=instrument.exchange,
                asset_class=instrument.asset_class,
                frequency=frequency,
                year=window.year,
                status="IN_PROGRESS",
                earliest_downloaded_ts=earliest_frontier,
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

    is_complete = False
    if final_state and final_state.get("earliest_downloaded_ts") is not None:
        earliest = final_state["earliest_downloaded_ts"]
        is_complete = earliest <= window.start_utc
    # If we already requested the oldest page that begins at window.start,
    # this yearly window is exhausted even when Jan 1 has no bar.
    if touched_lower_bound:
        is_complete = True

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
    elif not dry_run:
        meta.upsert_slice_progress(
            symbol=instrument.symbol,
            exchange=instrument.exchange,
            asset_class=instrument.asset_class,
            frequency=frequency,
            year=window.year,
            status="PARTIAL",
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
        now_utc = datetime.now(timezone.utc)
        unresolved_exchanges: list[str] = []

        exchange_last_traded: dict[str, datetime] = {}
        for exchange in cfg.universe.exchanges.keys():
            last_traded = None
            reference = None
            for candidate in _exchange_reference_candidates(cfg, universe, exchange):
                reference = candidate
                last_traded = _resolve_exchange_last_traded_date_from_candidate(
                    ibkr_client=ibkr_client,
                    instrument=candidate,
                    frequency=cfg.frequency.name,
                    bar_size=cfg.frequency.ibkr_bar_size,
                    what_to_show=cfg.ibkr.what_to_show,
                    use_rth=cfg.ibkr.use_regular_trading_hours,
                    as_of_utc=now_utc,
                    logger=logger,
                )
                if last_traded is not None:
                    break
            if last_traded is None:
                logger.log("exchange_last_traded_unresolved", exchange=exchange)
                unresolved_exchanges.append(exchange)
                continue
            exchange_last_traded[exchange] = last_traded
            if not is_dry_run:
                meta.upsert_exchange_last_traded_date(
                    exchange=exchange,
                    frequency=cfg.frequency.name,
                    last_traded_ts=last_traded,
                )
            logger.log(
                "exchange_last_traded",
                exchange=exchange,
                last_traded_date=last_traded.date().isoformat(),
                reference_symbol=reference.symbol if reference else None,
                reference_asset_class=reference.asset_class if reference else None,
            )

        if unresolved_exchanges and cfg.fail_on_unresolved_exchange_last_traded:
            error_message = (
                "Unable to resolve exchange last traded date for exchanges: "
                + ", ".join(sorted(unresolved_exchanges))
            )
            logger.log(
                "job_guard_failed",
                reason="exchange_last_traded_unresolved",
                exchanges=sorted(unresolved_exchanges),
                fail_on_unresolved_exchange_last_traded=True,
            )
            if not is_dry_run:
                for exchange in unresolved_exchanges:
                    meta.append_job_error(
                        run_id=run_id,
                        scope="exchange_last_traded",
                        exchange=exchange,
                        error_message=error_message,
                    )
            raise RuntimeError(error_message)

        for instrument in universe.instruments:
            exchange_trade_date = exchange_last_traded.get(instrument.exchange)
            if exchange_trade_date is not None and not is_dry_run:
                sync_state = meta.get_symbol_sync_status(
                    symbol=instrument.symbol,
                    exchange=instrument.exchange,
                    asset_class=instrument.asset_class,
                    frequency=cfg.frequency.name,
                )
                if sync_state and sync_state.get("status") == "COMPLETE":
                    stored_last = sync_state.get("last_traded_ts")
                    if stored_last is not None and _normalize_trading_date(stored_last) == exchange_trade_date:
                        logger.log(
                            "symbol_skipped_up_to_date",
                            symbol=instrument.symbol,
                            exchange=instrument.exchange,
                            asset_class=instrument.asset_class,
                            last_traded_date=exchange_trade_date.date().isoformat(),
                        )
                        continue

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
                coverage = meta.get_coverage(
                    symbol=instrument.symbol,
                    exchange=instrument.exchange,
                    asset_class=instrument.asset_class,
                    frequency=cfg.frequency.name,
                )
                earliest_ts = coverage.get("min_ts") if coverage else None
                latest_ts = coverage.get("max_ts") if coverage else None

                last_traded = exchange_trade_date
                is_complete = (
                    earliest_ts is not None
                    and latest_ts is not None
                    and head is not None
                    and earliest_ts <= head
                    and last_traded is not None
                    and latest_ts >= last_traded
                )
                meta.upsert_symbol_sync_status(
                    symbol=instrument.symbol,
                    exchange=instrument.exchange,
                    asset_class=instrument.asset_class,
                    frequency=cfg.frequency.name,
                    status="COMPLETE" if is_complete else "PARTIAL",
                    earliest_required_ts=head,
                    earliest_ts=earliest_ts,
                    latest_ts=latest_ts,
                    last_traded_ts=last_traded,
                )

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
