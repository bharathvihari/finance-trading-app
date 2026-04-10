import pandas as pd

REQUIRED_BAR_COLUMNS = {"timestamp", "open", "high", "low", "close", "volume"}


def validate_daily_bars(df: pd.DataFrame) -> list[str]:
    """Return validation issue codes for a daily-bar DataFrame."""
    issues: list[str] = []
    if df.empty:
        issues.append("empty_dataframe")
        return issues

    missing = sorted(REQUIRED_BAR_COLUMNS.difference(df.columns))
    if missing:
        issues.append(f"missing_columns:{','.join(missing)}")
        return issues

    ts = pd.to_datetime(df["timestamp"], utc=True, errors="coerce")
    if ts.isna().any():
        issues.append("null_or_invalid_timestamp")
    if ts.duplicated().any():
        issues.append("duplicate_timestamps")
    if not ts.is_monotonic_increasing:
        issues.append("non_monotonic_timestamps")

    for col in ("open", "high", "low", "close"):
        if (pd.to_numeric(df[col], errors="coerce") < 0).any():
            issues.append(f"negative_{col}_detected")
    if (pd.to_numeric(df["volume"], errors="coerce") < 0).any():
        issues.append("negative_volume_detected")

    ts_sorted = ts.sort_values()
    if len(ts_sorted) >= 2:
        max_gap = (ts_sorted.diff().max() or pd.Timedelta(0)).total_seconds()
        # Heuristic gap check for daily bars. Holidays/weekends are expected;
        # very large gaps can indicate missing data chunks.
        if max_gap > 12 * 24 * 3600:
            issues.append("large_time_gap_detected")

    return issues


def validation_metrics(df: pd.DataFrame) -> dict:
    """Compute basic summary metrics per symbol dataset."""
    if df.empty:
        return {
            "row_count": 0,
            "min_timestamp": None,
            "max_timestamp": None,
        }

    ts = pd.to_datetime(df["timestamp"], utc=True, errors="coerce")
    return {
        "row_count": int(len(df)),
        "min_timestamp": None if ts.isna().all() else ts.min().isoformat(),
        "max_timestamp": None if ts.isna().all() else ts.max().isoformat(),
    }
