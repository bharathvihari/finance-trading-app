from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
import sys

import pandas as pd

# Allow importing worker package when running tests from repo root.
sys.path.append(str(Path(__file__).resolve().parents[2] / "apps" / "workers"))

from jobs import backfill as backfill_job  # noqa: E402
from jobs import daily_refresh as daily_job  # noqa: E402
from market_data.models import Instrument, SymbolUniverse  # noqa: E402
from market_data.windowing import TimeWindow  # noqa: E402


def _cfg(mode: str):
    return SimpleNamespace(
        job_name=f"{mode}_job",
        mode=mode,
        fail_on_unresolved_exchange_last_traded=False,
        storage=SimpleNamespace(duckdb_path="data/duckdb/test.duckdb", parquet_root="data/parquet/price-data"),
        frequency=SimpleNamespace(name="daily", ibkr_bar_size="1 day"),
        postgres=SimpleNamespace(enabled=False, hot_window_months=6),
        ibkr=SimpleNamespace(
            what_to_show="TRADES",
            use_regular_trading_hours=True,
            host="127.0.0.1",
            port=7497,
            gateway_mode="paper",
        ),
        rate_limits=SimpleNamespace(),
        universe=SimpleNamespace(
            exchanges={
                "NASDAQ": SimpleNamespace(
                    reference_symbol="AAPL",
                    priority_symbols=["AAPL"],
                    symbols=["AAPL"],
                    priority_indices=[],
                    indices=[],
                )
            }
        ),
    )


class _MetaStub:
    def __init__(self):
        self.finished = None
        self.errors: list[dict] = []
        self.coverage_updates = 0

    def init_schema(self) -> None:
        pass

    def start_job_run(self, **kwargs) -> str:
        _ = kwargs
        return "run-1"

    def finish_job_run(self, **kwargs) -> None:
        self.finished = kwargs

    def append_job_error(self, **kwargs) -> None:
        self.errors.append(kwargs)

    def get_latest_covered_timestamp(self, **kwargs):
        _ = kwargs
        return datetime(2025, 1, 1, tzinfo=timezone.utc)

    def upsert_coverage(self, **kwargs) -> None:
        _ = kwargs
        self.coverage_updates += 1

    def get_symbol_sync_status(self, **kwargs):
        _ = kwargs
        return None

    def get_coverage(self, **kwargs):
        _ = kwargs
        return {
            "min_ts": datetime(2024, 1, 1, tzinfo=timezone.utc),
            "max_ts": datetime(2025, 1, 2, tzinfo=timezone.utc),
            "row_count": 1,
            "updated_at": datetime.now(timezone.utc),
        }

    def upsert_symbol_sync_status(self, **kwargs) -> None:
        _ = kwargs

    def get_combo_parquet_sync_ts(self, **kwargs):
        _ = kwargs
        return datetime(2025, 1, 2, tzinfo=timezone.utc)

    def upsert_exchange_last_traded_date(self, **kwargs) -> None:
        _ = kwargs


class _IbkrStub:
    def __init__(self):
        self.runtime = SimpleNamespace(host="127.0.0.1", port=7497, gateway_mode="paper")

    def get_head_timestamp(self, instrument):
        _ = instrument
        return datetime(2024, 1, 1, tzinfo=timezone.utc)

    def fetch_bars(self, request):
        _ = request
        return [
            {
                "symbol": "AAPL",
                "exchange": "NASDAQ",
                "asset_class": "equity",
                "frequency": "daily",
                "timestamp": datetime(2025, 1, 2, tzinfo=timezone.utc),
                "open": 10.0,
                "high": 11.0,
                "low": 9.0,
                "close": 10.5,
                "volume": 100.0,
            }
        ]


class _ParquetStub:
    def __init__(self):
        self.writes = 0

    def write_partition(self, df: pd.DataFrame) -> None:
        self.writes += len(df)

    def latest_timestamp(self, **kwargs):
        _ = kwargs
        return datetime(2025, 1, 1, tzinfo=timezone.utc)


def test_run_backfill_finishes_with_processed_and_failed_counts(monkeypatch) -> None:
    meta = _MetaStub()
    ibkr = _IbkrStub()
    parquet = _ParquetStub()

    monkeypatch.setattr(backfill_job, "load_job_config", lambda _: _cfg("backfill"))
    monkeypatch.setattr(backfill_job, "DuckDbMetaStore", lambda _: meta)
    monkeypatch.setattr(backfill_job.IbkrHistoricalClient, "from_ibkr_config", lambda *args, **kwargs: ibkr)
    monkeypatch.setattr(backfill_job, "ParquetStore", lambda _: parquet)
    monkeypatch.setattr(
        backfill_job,
        "load_universe",
        lambda _: SymbolUniverse([Instrument(symbol="AAPL", exchange="NASDAQ", asset_class="equity", priority=True)]),
    )
    monkeypatch.setattr(
        backfill_job,
        "yearly_windows_newest_to_oldest",
        lambda *_: [TimeWindow(start_utc=datetime(2025, 1, 1, tzinfo=timezone.utc), end_utc=datetime(2025, 2, 1, tzinfo=timezone.utc))],
    )
    monkeypatch.setattr(backfill_job, "_process_year_window", lambda **kwargs: (7, 1))

    backfill_job.run_backfill()

    assert meta.finished is not None
    assert meta.finished["status"] == "COMPLETE"
    assert meta.finished["processed_count"] == 7
    assert meta.finished["failed_count"] == 1


def test_run_daily_refresh_updates_coverage_and_counts(monkeypatch) -> None:
    meta = _MetaStub()
    ibkr = _IbkrStub()
    parquet = _ParquetStub()

    monkeypatch.setattr(daily_job, "load_job_config", lambda _: _cfg("daily"))
    monkeypatch.setattr(daily_job, "DuckDbMetaStore", lambda _: meta)
    monkeypatch.setattr(daily_job.IbkrHistoricalClient, "from_ibkr_config", lambda *args, **kwargs: ibkr)
    monkeypatch.setattr(daily_job, "ParquetStore", lambda _: parquet)
    monkeypatch.setattr(
        daily_job,
        "load_universe",
        lambda _: SymbolUniverse([Instrument(symbol="AAPL", exchange="NASDAQ", asset_class="equity", priority=True)]),
    )
    monkeypatch.setattr(
        daily_job,
        "_resolve_latest_timestamp",
        lambda *args, **kwargs: datetime(2025, 1, 1, tzinfo=timezone.utc),
    )
    monkeypatch.setattr(
        daily_job,
        "_build_incremental_window",
        lambda *args, **kwargs: (datetime(2025, 1, 1, 0, 0, 1, tzinfo=timezone.utc), datetime(2025, 1, 2, tzinfo=timezone.utc)),
    )

    daily_job.run_incremental_refresh()

    assert meta.finished is not None
    assert meta.finished["status"] == "COMPLETE"
    assert meta.finished["processed_count"] == 1
    assert meta.finished["failed_count"] == 0
    assert meta.coverage_updates == 1


def test_run_backfill_dry_run_skips_meta_finish(monkeypatch) -> None:
    meta = _MetaStub()
    ibkr = _IbkrStub()
    parquet = _ParquetStub()

    monkeypatch.setattr(backfill_job, "load_job_config", lambda _: _cfg("backfill"))
    monkeypatch.setattr(backfill_job, "DuckDbMetaStore", lambda _: meta)
    monkeypatch.setattr(backfill_job.IbkrHistoricalClient, "from_ibkr_config", lambda *args, **kwargs: ibkr)
    monkeypatch.setattr(backfill_job, "ParquetStore", lambda _: parquet)
    monkeypatch.setattr(
        backfill_job,
        "load_universe",
        lambda _: SymbolUniverse([Instrument(symbol="AAPL", exchange="NASDAQ", asset_class="equity", priority=True)]),
    )
    monkeypatch.setattr(
        backfill_job,
        "yearly_windows_newest_to_oldest",
        lambda *_: [TimeWindow(start_utc=datetime(2025, 1, 1, tzinfo=timezone.utc), end_utc=datetime(2025, 2, 1, tzinfo=timezone.utc))],
    )
    monkeypatch.setattr(backfill_job, "_process_year_window", lambda **kwargs: (3, 0))

    backfill_job.run_backfill(dry_run=True)

    assert meta.finished is None
