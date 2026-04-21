"""
Pure technical indicator functions.

All functions:
  - Accept a pandas Series (or DataFrame for multi-input indicators like ATR).
  - Return a Series or DataFrame aligned to the input index.
  - Produce NaN for the warm-up period — the caller is responsible for trimming
    or passing extra history so the output starts valid at the requested date.
  - Have no side effects, no I/O, no global state.
"""
from __future__ import annotations

import numpy as np
import pandas as pd


# ---------------------------------------------------------------------------
# Moving averages
# ---------------------------------------------------------------------------

def sma(close: pd.Series, period: int) -> pd.Series:
    """Simple Moving Average."""
    return close.rolling(window=period, min_periods=period).mean().rename(f"SMA({period})")


def ema(close: pd.Series, period: int) -> pd.Series:
    """Exponential Moving Average (Wilder smoothing, adjust=False)."""
    return close.ewm(span=period, adjust=False, min_periods=period).mean().rename(f"EMA({period})")


def wma(close: pd.Series, period: int) -> pd.Series:
    """Linearly Weighted Moving Average."""
    weights = np.arange(1, period + 1, dtype=float)
    w_sum = weights.sum()

    def _wma(x: np.ndarray) -> float:
        return float(np.dot(x, weights) / w_sum)

    return (
        close.rolling(window=period, min_periods=period)
        .apply(_wma, raw=True)
        .rename(f"WMA({period})")
    )


# ---------------------------------------------------------------------------
# Bollinger Bands
# ---------------------------------------------------------------------------

def bollinger_bands(
    close: pd.Series,
    period: int = 20,
    std_dev: float = 2.0,
) -> pd.DataFrame:
    """
    Bollinger Bands.

    Returns a DataFrame with columns:
      bb_upper, bb_middle, bb_lower
    """
    middle = close.rolling(window=period, min_periods=period).mean()
    std = close.rolling(window=period, min_periods=period).std(ddof=0)
    return pd.DataFrame(
        {
            "bb_upper": middle + std_dev * std,
            "bb_middle": middle,
            "bb_lower": middle - std_dev * std,
        },
        index=close.index,
    )


# ---------------------------------------------------------------------------
# RSI
# ---------------------------------------------------------------------------

def rsi(close: pd.Series, period: int = 14) -> pd.Series:
    """
    Relative Strength Index using Wilder's smoothing (EWM with com=period-1).
    Values in [0, 100]; NaN for the first `period` rows.
    """
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = (-delta).clip(lower=0)
    avg_gain = gain.ewm(com=period - 1, adjust=False, min_periods=period).mean()
    avg_loss = loss.ewm(com=period - 1, adjust=False, min_periods=period).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    return (100 - 100 / (1 + rs)).rename(f"RSI({period})")


# ---------------------------------------------------------------------------
# MACD
# ---------------------------------------------------------------------------

def macd(
    close: pd.Series,
    fast: int = 12,
    slow: int = 26,
    signal: int = 9,
) -> pd.DataFrame:
    """
    MACD — Moving Average Convergence Divergence.

    Returns a DataFrame with columns:
      macd, macd_signal, macd_histogram
    """
    ema_fast = close.ewm(span=fast, adjust=False, min_periods=fast).mean()
    ema_slow = close.ewm(span=slow, adjust=False, min_periods=slow).mean()
    macd_line = ema_fast - ema_slow
    signal_line = macd_line.ewm(span=signal, adjust=False, min_periods=signal).mean()
    return pd.DataFrame(
        {
            "macd": macd_line,
            "macd_signal": signal_line,
            "macd_histogram": macd_line - signal_line,
        },
        index=close.index,
    )


# ---------------------------------------------------------------------------
# ATR
# ---------------------------------------------------------------------------

def atr(
    high: pd.Series,
    low: pd.Series,
    close: pd.Series,
    period: int = 14,
) -> pd.Series:
    """
    Average True Range using Wilder's smoothing.

    True Range = max(H-L, |H-Cprev|, |L-Cprev|)
    """
    prev_close = close.shift(1)
    tr = pd.concat(
        [
            high - low,
            (high - prev_close).abs(),
            (low - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    return tr.ewm(com=period - 1, adjust=False, min_periods=period).mean().rename(f"ATR({period})")
