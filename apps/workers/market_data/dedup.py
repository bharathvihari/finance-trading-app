import pandas as pd


def deduplicate_bars(df: pd.DataFrame) -> pd.DataFrame:
    """Placeholder dedup by primary key-like columns.

    Canonical dedupe keys will be finalized in Step 9.
    """
    if df.empty:
        return df
    keys = [k for k in ("symbol", "exchange", "timestamp") if k in df.columns]
    if not keys:
        return df
    return df.drop_duplicates(subset=keys, keep="last")
