from datetime import datetime, timezone
from pathlib import Path
import sys

# Allow importing worker package when running tests from repo root.
sys.path.append(str(Path(__file__).resolve().parents[2] / "apps" / "workers"))

from jobs.retry_failed import _failed_slice_to_window  # noqa: E402


def test_failed_slice_to_window_for_past_year() -> None:
    now = datetime(2026, 5, 1, tzinfo=timezone.utc)
    window = _failed_slice_to_window(2025, now)
    assert window is not None
    assert window.start_utc == datetime(2025, 1, 1, tzinfo=timezone.utc)
    assert window.end_utc == datetime(2026, 1, 1, tzinfo=timezone.utc)


def test_failed_slice_to_window_for_current_year_clamps_to_now() -> None:
    now = datetime(2026, 5, 1, tzinfo=timezone.utc)
    window = _failed_slice_to_window(2026, now)
    assert window is not None
    assert window.start_utc == datetime(2026, 1, 1, tzinfo=timezone.utc)
    assert window.end_utc == now
