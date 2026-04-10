from datetime import datetime, timezone
from pathlib import Path
import sys

import pytest

# Allow importing worker package when running tests from repo root.
sys.path.append(str(Path(__file__).resolve().parents[2] / "apps" / "workers"))

from market_data.ibkr_client import (  # noqa: E402
    HistoricalRequest,
    IbkrConnectionError,
    IbkrDependencyError,
    IbkrHistoricalClient,
    IbkrRequestValidationError,
    IbkrRuntimeConfig,
)
from market_data.models import Instrument  # noqa: E402
from market_data.rate_limiter import ExponentialBackoff, RequestPacer  # noqa: E402


class _StubBackend:
    def get_head_timestamp(self, instrument: Instrument):
        _ = instrument
        return None

    def fetch_historical(self, request: HistoricalRequest):
        _ = request
        return [
            {"time": "2025-01-03T00:00:00Z", "o": 11, "h": 12, "l": 10, "c": 11.5, "v": 1000},
            {"time": "2025-01-02T00:00:00Z", "o": 10, "h": 11, "l": 9, "c": 10.5, "v": 1200},
            {"time": "2025-01-03T00:00:00Z", "o": 11.2, "h": 12.2, "l": 10.2, "c": 11.7, "v": 900},
        ]


def _request(start_utc: datetime, end_utc: datetime) -> HistoricalRequest:
    return HistoricalRequest(
        instrument=Instrument(symbol="AAPL", exchange="NASDAQ", asset_class="equity"),
        start_utc=start_utc,
        end_utc=end_utc,
        bar_size="1 day",
        what_to_show="TRADES",
        use_regular_trading_hours=True,
    )


def _fast_client(backend: _StubBackend) -> IbkrHistoricalClient:
    pacer = RequestPacer(
        max_requests_per_window=60,
        window_seconds=600,
        identical_request_cooldown_seconds=0,
        utilization_target_pct=100,
        base_delay_seconds=0.0,
        jitter_seconds=0.0,
        sleeper=lambda _: None,
    )
    backoff = ExponentialBackoff(
        max_retries=0,
        base_delay_seconds=0.0,
        max_backoff_seconds=0.0,
        jitter_seconds=0.0,
        sleeper=lambda _: None,
    )
    return IbkrHistoricalClient(runtime=IbkrRuntimeConfig(), backend=backend, pacer=pacer, backoff=backoff)


def test_request_validation_rejects_invalid_range_and_naive_time() -> None:
    client = _fast_client(_StubBackend())

    with pytest.raises(IbkrRequestValidationError):
        client.fetch_bars(
            _request(
                datetime(2025, 1, 10, tzinfo=timezone.utc),
                datetime(2025, 1, 10, tzinfo=timezone.utc),
            )
        )

    with pytest.raises(IbkrRequestValidationError):
        client.fetch_bars(
            _request(
                datetime(2025, 1, 1),
                datetime(2025, 1, 2, tzinfo=timezone.utc),
            )
        )


def test_fallback_behavior_missing_dependency() -> None:
    with pytest.raises((IbkrDependencyError, IbkrConnectionError)):
        IbkrHistoricalClient(
            runtime=IbkrRuntimeConfig(fallback_enabled=False),
        )

    client = IbkrHistoricalClient(runtime=IbkrRuntimeConfig(fallback_enabled=True))
    rows = client.fetch_bars(
        _request(
            datetime(2025, 1, 1, tzinfo=timezone.utc),
            datetime(2025, 1, 5, tzinfo=timezone.utc),
        )
    )
    assert rows == []


def test_normalization_keys_sorted_and_utc() -> None:
    client = _fast_client(_StubBackend())
    rows = client.fetch_bars(
        _request(
            datetime(2025, 1, 1, tzinfo=timezone.utc),
            datetime(2025, 1, 5, tzinfo=timezone.utc),
        )
    )

    assert len(rows) == 2  # duplicate timestamp deduped

    required = {"symbol", "exchange", "asset_class", "frequency", "timestamp", "open", "high", "low", "close", "volume"}
    assert required.issubset(rows[0].keys())

    assert rows[0]["timestamp"] < rows[1]["timestamp"]
    assert rows[0]["timestamp"].tzinfo is not None
