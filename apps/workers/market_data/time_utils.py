from datetime import datetime, timezone


def to_utc(ts: datetime) -> datetime:
    if ts.tzinfo is None:
        return ts.replace(tzinfo=timezone.utc)
    return ts.astimezone(timezone.utc)


def utc_year_start(year: int) -> datetime:
    return datetime(year, 1, 1, tzinfo=timezone.utc)
