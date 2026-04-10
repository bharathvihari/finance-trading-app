from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Iterator

from .time_utils import to_utc, utc_year_start


@dataclass(frozen=True)
class TimeWindow:
    """Half-open UTC interval: [start_utc, end_utc)."""

    start_utc: datetime
    end_utc: datetime

    def __post_init__(self) -> None:
        if self.start_utc.tzinfo is None or self.end_utc.tzinfo is None:
            raise ValueError("TimeWindow expects timezone-aware UTC datetimes.")
        if self.start_utc >= self.end_utc:
            raise ValueError("TimeWindow start_utc must be < end_utc.")

    @property
    def year(self) -> int:
        return self.start_utc.year


def yearly_windows_newest_to_oldest(earliest_utc: datetime, latest_utc: datetime) -> list[TimeWindow]:
    """Split [earliest_utc, latest_utc) into full/partial yearly windows newest->oldest."""
    start = to_utc(earliest_utc)
    end = to_utc(latest_utc)

    if start >= end:
        return []

    windows: list[TimeWindow] = []
    cursor_end = end

    while cursor_end > start:
        current_year_start = utc_year_start(cursor_end.year)
        if cursor_end == current_year_start:
            year_start = utc_year_start(cursor_end.year - 1)
        else:
            year_start = current_year_start
        window_start = max(start, year_start)
        if window_start >= cursor_end:
            break
        windows.append(TimeWindow(start_utc=window_start, end_utc=cursor_end))
        cursor_end = window_start

    return windows


def paginated_windows_backward(
    window: TimeWindow,
    page_span: timedelta,
    resume_from_utc: datetime | None = None,
) -> Iterator[TimeWindow]:
    """Yield sub-windows newest->oldest inside a parent yearly window.

    `resume_from_utc` is an already-downloaded frontier in this same window.
    Backfill should continue with pages that end at this frontier to avoid re-fetching newer data.
    """
    if page_span <= timedelta(0):
        raise ValueError("page_span must be positive.")

    cursor_end = window.end_utc
    if resume_from_utc is not None:
        resume_utc = to_utc(resume_from_utc)
        if resume_utc <= window.start_utc:
            return
        if resume_utc < window.end_utc:
            cursor_end = resume_utc

    while cursor_end > window.start_utc:
        page_start = max(window.start_utc, cursor_end - page_span)
        yield TimeWindow(start_utc=page_start, end_utc=cursor_end)
        cursor_end = page_start


def previous_cursor_from_oldest_bar(oldest_bar_ts_utc: datetime, overlap_guard: timedelta = timedelta(milliseconds=1)) -> datetime:
    """Compute next page end cursor from oldest returned bar timestamp.

    Moves slightly backward to avoid overlap when APIs treat end-time as inclusive.
    """
    if overlap_guard < timedelta(0):
        raise ValueError("overlap_guard must be >= 0.")
    return to_utc(oldest_bar_ts_utc) - overlap_guard
