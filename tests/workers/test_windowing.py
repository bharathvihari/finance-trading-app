from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys

# Allow importing worker package when running tests from repo root.
sys.path.append(str(Path(__file__).resolve().parents[2] / "apps" / "workers"))

from market_data.windowing import (
    TimeWindow,
    paginated_windows_backward,
    previous_cursor_from_oldest_bar,
    yearly_windows_newest_to_oldest,
)


def test_yearly_windows_newest_to_oldest() -> None:
    earliest = datetime(2022, 5, 10, tzinfo=timezone.utc)
    latest = datetime(2024, 3, 1, tzinfo=timezone.utc)

    windows = yearly_windows_newest_to_oldest(earliest, latest)

    assert len(windows) == 3
    assert windows[0] == TimeWindow(
        start_utc=datetime(2024, 1, 1, tzinfo=timezone.utc),
        end_utc=datetime(2024, 3, 1, tzinfo=timezone.utc),
    )
    assert windows[1] == TimeWindow(
        start_utc=datetime(2023, 1, 1, tzinfo=timezone.utc),
        end_utc=datetime(2024, 1, 1, tzinfo=timezone.utc),
    )
    assert windows[2] == TimeWindow(
        start_utc=datetime(2022, 5, 10, tzinfo=timezone.utc),
        end_utc=datetime(2023, 1, 1, tzinfo=timezone.utc),
    )


def test_paginated_windows_backward_respects_resume_cursor() -> None:
    window = TimeWindow(
        start_utc=datetime(2024, 1, 1, tzinfo=timezone.utc),
        end_utc=datetime(2024, 2, 1, tzinfo=timezone.utc),
    )
    resume = datetime(2024, 1, 20, tzinfo=timezone.utc)

    pages = list(paginated_windows_backward(window, timedelta(days=7), resume_from_utc=resume))

    assert pages[0] == TimeWindow(
        start_utc=datetime(2024, 1, 13, tzinfo=timezone.utc),
        end_utc=datetime(2024, 1, 20, tzinfo=timezone.utc),
    )
    assert pages[-1].start_utc == window.start_utc


def test_previous_cursor_has_overlap_guard() -> None:
    oldest = datetime(2024, 1, 15, 12, tzinfo=timezone.utc)
    cursor = previous_cursor_from_oldest_bar(oldest)
    assert cursor < oldest
