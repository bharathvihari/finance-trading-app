from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
import sys

import pandas as pd

# Allow importing worker package when running tests from repo root.
sys.path.append(str(Path(__file__).resolve().parents[2] / "apps" / "workers"))

from jobs import archive_cold_bars as archive_job  # noqa: E402


def _cfg() -> SimpleNamespace:
    return SimpleNamespace(
        job_name="cold_bar_archival",
        mode="archive",
        dry_run=False,
        storage=SimpleNamespace(
            parquet_root="data/parquet/price-data",
            duckdb_path="data/duckdb/market_data.duckdb",
        ),
        postgres=SimpleNamespace(
            enabled=True,
            hot_window_months=6,
        ),
    )


class _MetaStub:
    def __init__(self) -> None:
        self.finished = None
        self.parquet_symbols: list[tuple[str, str, str, str]] = []
        self.coverage_updates: list[dict] = []
        self.exchange_sync_updates: list[dict] = []

    def init_schema(self) -> None:
        pass

    def start_job_run(self, **kwargs) -> str:
        _ = kwargs
        return "run-archive-1"

    def finish_job_run(self, **kwargs) -> None:
        self.finished = kwargs

    def append_job_error(self, **kwargs) -> None:
        _ = kwargs

    def upsert_parquet_symbol(self, symbol: str, exchange: str, asset_class: str, frequency: str) -> None:
        self.parquet_symbols.append((symbol, exchange, asset_class, frequency))

    def upsert_coverage(self, **kwargs) -> None:
        self.coverage_updates.append(kwargs)

    def get_combo_parquet_sync_ts(self, **kwargs):
        _ = kwargs
        return datetime(2024, 1, 3, tzinfo=timezone.utc)

    def upsert_exchange_last_traded_date(self, **kwargs) -> None:
        self.exchange_sync_updates.append(kwargs)


class _ParquetStub:
    def write_partition(self, df: pd.DataFrame):
        _ = df
        return [Path("dummy.parquet")]


class _HotStoreStub:
    def fetch_cold_partition_keys(self, cutoff_utc):
        _ = cutoff_utc
        return [
            {
                "asset_class": "equity",
                "exchange": "NASDAQ",
                "frequency": "daily",
                "year": 2024,
                "row_count": 2,
            }
        ]

    def read_bars_for_partition(self, **kwargs) -> pd.DataFrame:
        _ = kwargs
        return pd.DataFrame(
            [
                {
                    "symbol": "AAPL",
                    "exchange": "NASDAQ",
                    "asset_class": "equity",
                    "frequency": "daily",
                    "timestamp": datetime(2024, 1, 2, tzinfo=timezone.utc),
                    "open": 10.0,
                    "high": 11.0,
                    "low": 9.0,
                    "close": 10.5,
                    "volume": 100.0,
                },
                {
                    "symbol": "MSFT",
                    "exchange": "NASDAQ",
                    "asset_class": "equity",
                    "frequency": "daily",
                    "timestamp": datetime(2024, 1, 3, tzinfo=timezone.utc),
                    "open": 20.0,
                    "high": 21.0,
                    "low": 19.0,
                    "close": 20.5,
                    "volume": 200.0,
                },
            ]
        )

    def delete_bars_for_partition(self, **kwargs) -> int:
        _ = kwargs
        return 2


class _LoggerStub:
    def __init__(self, *args, **kwargs) -> None:
        _ = (args, kwargs)

    def set_run_id(self, run_id: str) -> None:
        _ = run_id

    def log(self, *args, **kwargs) -> None:
        _ = (args, kwargs)

    def close(self) -> None:
        pass


def test_update_archive_metadata_updates_symbol_and_coverage() -> None:
    meta = _MetaStub()
    df = pd.DataFrame(
        [
            {
                "symbol": "AAPL",
                "exchange": "NASDAQ",
                "asset_class": "equity",
                "frequency": "daily",
                "timestamp": datetime(2024, 1, 2, tzinfo=timezone.utc),
            },
            {
                "symbol": "AAPL",
                "exchange": "NASDAQ",
                "asset_class": "equity",
                "frequency": "daily",
                "timestamp": datetime(2024, 1, 5, tzinfo=timezone.utc),
            },
            {
                "symbol": "MSFT",
                "exchange": "NASDAQ",
                "asset_class": "equity",
                "frequency": "daily",
                "timestamp": datetime(2024, 1, 3, tzinfo=timezone.utc),
            },
        ]
    )

    updates = archive_job._update_archive_metadata(meta, df)

    assert updates == 2
    assert len(meta.parquet_symbols) == 2
    assert len(meta.coverage_updates) == 2


def test_run_archive_updates_duckdb_metadata(monkeypatch) -> None:
    meta = _MetaStub()
    parquet = _ParquetStub()
    hot = _HotStoreStub()

    monkeypatch.setattr(archive_job, "load_job_config", lambda _: _cfg())
    monkeypatch.setattr(archive_job, "DuckDbMetaStore", lambda _: meta)
    monkeypatch.setattr(archive_job, "ParquetStore", lambda _: parquet)
    monkeypatch.setattr(archive_job, "PostgresBarStore", SimpleNamespace(from_config=lambda _: hot))
    monkeypatch.setattr(archive_job, "StructuredLogger", _LoggerStub)

    archive_job.run_archive()

    assert meta.finished is not None
    assert meta.finished["status"] == "COMPLETE"
    assert len(meta.parquet_symbols) == 2
    assert len(meta.coverage_updates) == 2
    assert len(meta.exchange_sync_updates) == 1
