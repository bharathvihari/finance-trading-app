from datetime import datetime, timezone
from pathlib import Path
import sys

import pandas as pd

sys.path.append(str(Path(__file__).resolve().parents[2] / "apps" / "trading"))

from data_import.parquet_to_nautilus import (  # noqa: E402
    NautilusImportError,
    load_daily_parquet_bars,
    to_nautilus_bar_objects,
    to_nautilus_payloads,
)


def test_to_nautilus_payloads_sorts_and_dedupes() -> None:
    df = pd.DataFrame(
        [
            {"time": "2025-01-03T00:00:00Z", "o": 11, "h": 12, "l": 10, "c": 11.5, "v": 100},
            {"time": "2025-01-02T00:00:00Z", "o": 10, "h": 11, "l": 9, "c": 10.5, "v": 120},
            {"time": "2025-01-03T00:00:00Z", "o": 11.2, "h": 12.2, "l": 10.2, "c": 11.7, "v": 90},
        ]
    )
    payloads = to_nautilus_payloads(df, instrument_id="AAPL.NASDAQ", bar_type="AAPL.NASDAQ-1D-LAST-EXTERNAL")

    assert len(payloads) == 2
    assert payloads[0]["ts_event"] < payloads[1]["ts_event"]
    assert payloads[0]["instrument_id"] == "AAPL.NASDAQ"


def test_load_daily_parquet_bars_reads_partitioned_symbol(tmp_path: Path) -> None:
    root = tmp_path / "price-data"
    target = root / "asset_class=equity" / "exchange=NASDAQ" / "frequency=daily" / "year=2025"
    target.mkdir(parents=True, exist_ok=True)

    df = pd.DataFrame(
        [
            {"symbol": "AAPL", "timestamp": "2025-01-02T00:00:00Z", "open": 10, "high": 11, "low": 9, "close": 10.5, "volume": 100},
            {"symbol": "MSFT", "timestamp": "2025-01-02T00:00:00Z", "open": 20, "high": 21, "low": 19, "close": 20.5, "volume": 200},
        ]
    )
    df.to_parquet(target / "part-001.parquet", index=False)

    out = load_daily_parquet_bars(root, symbol="AAPL", exchange="NASDAQ")
    assert len(out) == 1
    assert out.iloc[0]["close"] == 10.5


def test_to_nautilus_bar_objects_requires_factory() -> None:
    df = pd.DataFrame(
        [
            {
                "timestamp": datetime(2025, 1, 2, tzinfo=timezone.utc),
                "open": 10,
                "high": 11,
                "low": 9,
                "close": 10.5,
                "volume": 100,
            }
        ]
    )
    try:
        to_nautilus_bar_objects(df, instrument_id="AAPL.NASDAQ", bar_type="AAPL.NASDAQ-1D-LAST-EXTERNAL")
        raise AssertionError("Expected NautilusImportError")
    except NautilusImportError:
        pass
