from datetime import datetime, timezone
from pathlib import Path
import sys

import pandas as pd

# Allow importing worker package when running tests from repo root.
sys.path.append(str(Path(__file__).resolve().parents[2] / "apps" / "workers"))

from jobs.backfill import _process_year_window, _split_hot_cold_frames  # noqa: E402
from market_data.models import Instrument  # noqa: E402
from market_data.windowing import TimeWindow  # noqa: E402


class _StubIbkrClient:
    def fetch_bars(self, request):
        return [
            {
                "symbol": request.instrument.symbol,
                "exchange": request.instrument.exchange,
                "asset_class": request.instrument.asset_class,
                "frequency": request.frequency,
                "timestamp": request.start_utc,
                "open": 100.0,
                "high": 101.0,
                "low": 99.0,
                "close": 100.5,
                "volume": 1000.0,
            }
        ]


class _StubParquetStore:
    def __init__(self) -> None:
        self.writes = 0
        self.last_write_rows = 0

    def write_partition(self, df) -> None:
        self.writes += len(df)
        self.last_write_rows = len(df)


class _StubHotStore:
    def __init__(self) -> None:
        self.upserts = 0
        self.last_upsert_rows = 0

    def upsert_bars(self, df) -> int:
        self.upserts += len(df)
        self.last_upsert_rows = len(df)
        return len(df)


class _StubMetaStore:
    def __init__(self) -> None:
        self.slices: dict[tuple[str, str, str, str, int], dict] = {}

    def _key(self, symbol: str, exchange: str, asset_class: str, frequency: str, year: int) -> tuple[str, str, str, str, int]:
        return (symbol, exchange, asset_class, frequency, year)

    def get_slice_state(self, symbol: str, exchange: str, asset_class: str, frequency: str, year: int):
        return self.slices.get(self._key(symbol, exchange, asset_class, frequency, year))

    def upsert_slice_progress(
        self,
        symbol: str,
        exchange: str,
        asset_class: str,
        frequency: str,
        year: int,
        status: str,
        earliest_downloaded_ts=None,
        latest_downloaded_ts=None,
        last_success_request_at=None,
        last_error=None,
    ) -> None:
        key = self._key(symbol, exchange, asset_class, frequency, year)
        current = self.slices.get(key, {})
        self.slices[key] = {
            "symbol": symbol,
            "exchange": exchange,
            "asset_class": asset_class,
            "frequency": frequency,
            "year": year,
            "status": status,
            "earliest_downloaded_ts": earliest_downloaded_ts if earliest_downloaded_ts is not None else current.get("earliest_downloaded_ts"),
            "latest_downloaded_ts": latest_downloaded_ts if latest_downloaded_ts is not None else current.get("latest_downloaded_ts"),
            "last_success_request_at": last_success_request_at,
            "last_error": last_error,
        }

    def append_job_error(self, **kwargs) -> None:
        _ = kwargs

    def upsert_coverage(self, **kwargs) -> None:
        _ = kwargs


def test_process_year_window_writes_and_marks_complete(tmp_path: Path) -> None:
    _ = tmp_path
    meta = _StubMetaStore()
    parquet_store = _StubParquetStore()

    instrument = Instrument(symbol="AAPL", exchange="NASDAQ", asset_class="equity", priority=True)
    window = TimeWindow(
        start_utc=datetime(2025, 1, 1, tzinfo=timezone.utc),
        end_utc=datetime(2025, 2, 1, tzinfo=timezone.utc),
    )

    rows_written, errors = _process_year_window(
        run_id="test-run",
        instrument=instrument,
        frequency="daily",
        bar_size="1 day",
        what_to_show="TRADES",
        use_rth=True,
        window=window,
        ibkr_client=_StubIbkrClient(),
        parquet_store=parquet_store,
        meta=meta,
    )

    assert rows_written == 1
    assert errors == 0

    state = meta.get_slice_state("AAPL", "NASDAQ", "equity", "daily", 2025)
    assert state is not None
    assert state["status"] == "COMPLETE"


def test_process_year_window_skips_complete_slice(tmp_path: Path) -> None:
    _ = tmp_path
    meta = _StubMetaStore()
    parquet_store = _StubParquetStore()

    instrument = Instrument(symbol="AAPL", exchange="NASDAQ", asset_class="equity", priority=True)
    meta.upsert_slice_progress(
        symbol="AAPL",
        exchange="NASDAQ",
        asset_class="equity",
        frequency="daily",
        year=2025,
        status="COMPLETE",
    )

    rows_written, errors = _process_year_window(
        run_id="test-run",
        instrument=instrument,
        frequency="daily",
        bar_size="1 day",
        what_to_show="TRADES",
        use_rth=True,
        window=TimeWindow(
            start_utc=datetime(2025, 1, 1, tzinfo=timezone.utc),
            end_utc=datetime(2025, 2, 1, tzinfo=timezone.utc),
        ),
        ibkr_client=_StubIbkrClient(),
        parquet_store=parquet_store,
        meta=meta,
    )

    assert rows_written == 0
    assert errors == 0


def test_split_hot_cold_frames_routes_using_cutoff() -> None:
    frame = pd.DataFrame(
        [
            {"timestamp": datetime(2025, 9, 30, tzinfo=timezone.utc), "symbol": "AAPL"},
            {"timestamp": datetime(2025, 10, 1, tzinfo=timezone.utc), "symbol": "AAPL"},
        ]
    )

    hot, cold = _split_hot_cold_frames(frame, datetime(2025, 10, 1, tzinfo=timezone.utc))

    assert len(hot) == 1
    assert len(cold) == 1


def test_process_year_window_writes_hot_rows_to_postgres(tmp_path: Path) -> None:
    _ = tmp_path
    meta = _StubMetaStore()
    parquet_store = _StubParquetStore()
    hot_store = _StubHotStore()

    instrument = Instrument(symbol="AAPL", exchange="NASDAQ", asset_class="equity", priority=True)
    window = TimeWindow(
        start_utc=datetime(2026, 1, 1, tzinfo=timezone.utc),
        end_utc=datetime(2026, 2, 1, tzinfo=timezone.utc),
    )

    rows_written, errors = _process_year_window(
        run_id="test-run",
        instrument=instrument,
        frequency="daily",
        bar_size="1 day",
        what_to_show="TRADES",
        use_rth=True,
        window=window,
        ibkr_client=_StubIbkrClient(),
        parquet_store=parquet_store,
        meta=meta,
        hot_store=hot_store,
        hot_cutoff_utc=datetime(2025, 10, 1, tzinfo=timezone.utc),
    )

    assert rows_written == 1
    assert errors == 0
    assert hot_store.upserts == 1
    assert parquet_store.writes == 0
