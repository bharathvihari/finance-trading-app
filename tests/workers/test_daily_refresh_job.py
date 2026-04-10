from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys

# Allow importing worker package when running tests from repo root.
sys.path.append(str(Path(__file__).resolve().parents[2] / "apps" / "workers"))

from jobs.daily_refresh import _build_incremental_window, _resolve_latest_timestamp  # noqa: E402
from market_data.models import Instrument  # noqa: E402


class _StubMeta:
    def __init__(self, latest=None):
        self.latest = latest

    def get_latest_covered_timestamp(self, **kwargs):
        _ = kwargs
        return self.latest


class _StubParquet:
    def __init__(self, latest=None):
        self.latest = latest

    def latest_timestamp(self, **kwargs):
        _ = kwargs
        return self.latest


def test_build_incremental_window_returns_none_when_no_delta() -> None:
    now = datetime.now(timezone.utc)
    assert _build_incremental_window(now, now) is None
    assert _build_incremental_window(now, now + timedelta(microseconds=500)) is None


def test_build_incremental_window_returns_expected_start_end() -> None:
    latest = datetime(2025, 1, 1, tzinfo=timezone.utc)
    now = datetime(2025, 1, 2, tzinfo=timezone.utc)
    window = _build_incremental_window(latest, now)
    assert window is not None
    assert window[0] > latest
    assert window[1] == now


def test_resolve_latest_timestamp_prefers_meta_then_parquet() -> None:
    instrument = Instrument(symbol="AAPL", exchange="NASDAQ", asset_class="equity")
    latest_meta = datetime(2025, 1, 10, tzinfo=timezone.utc)
    latest_parquet = datetime(2025, 1, 9, tzinfo=timezone.utc)

    resolved = _resolve_latest_timestamp(
        _StubMeta(latest=latest_meta),
        _StubParquet(latest=latest_parquet),
        instrument,
        frequency="daily",
    )
    assert resolved == latest_meta

    resolved_fallback = _resolve_latest_timestamp(
        _StubMeta(latest=None),
        _StubParquet(latest=latest_parquet),
        instrument,
        frequency="daily",
    )
    assert resolved_fallback == latest_parquet
