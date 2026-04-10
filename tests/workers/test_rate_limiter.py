from pathlib import Path
import sys

# Allow importing worker package when running tests from repo root.
sys.path.append(str(Path(__file__).resolve().parents[2] / "apps" / "workers"))

from market_data.rate_limiter import ExponentialBackoff, RequestPacer  # noqa: E402


def test_request_pacer_enforces_identical_request_cooldown() -> None:
    now = {"t": 0.0}
    sleeps: list[float] = []

    def _clock() -> float:
        return now["t"]

    def _sleep(seconds: float) -> None:
        sleeps.append(seconds)
        now["t"] += seconds

    pacer = RequestPacer(
        max_requests_per_window=60,
        window_seconds=600,
        identical_request_cooldown_seconds=2,
        utilization_target_pct=100,
        base_delay_seconds=0.0,
        jitter_seconds=0.0,
        clock=_clock,
        sleeper=_sleep,
    )

    pacer.pace("same-request")
    pacer.pace("same-request")

    assert sleeps[-1] >= 2.0


def test_exponential_backoff_retries_until_success() -> None:
    calls = {"n": 0}
    sleeps: list[float] = []

    def _sleep(seconds: float) -> None:
        sleeps.append(seconds)

    def _fn() -> str:
        calls["n"] += 1
        if calls["n"] < 3:
            raise RuntimeError("transient")
        return "ok"

    backoff = ExponentialBackoff(
        max_retries=3,
        base_delay_seconds=0.0,
        max_backoff_seconds=0.0,
        jitter_seconds=0.0,
        sleeper=_sleep,
    )
    out = backoff.run(_fn, lambda exc: isinstance(exc, RuntimeError))

    assert out == "ok"
    assert calls["n"] == 3
