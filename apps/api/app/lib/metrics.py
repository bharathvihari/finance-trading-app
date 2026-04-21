"""
Pure performance and risk metrics.

All functions:
  - Accept pandas Series or plain scalars.
  - Return a float (or pd.Series for equity_curve).
  - Have no side effects, no I/O, no global state.
  - Return float("nan") when inputs are insufficient rather than raising.

Conventions:
  - `returns`  — a pd.Series of period returns (e.g. daily: (p1-p0)/p0).
  - `values`   — a pd.Series of portfolio values over time (absolute, e.g. USD).
  - `periods_per_year` — 252 for daily bars, 52 for weekly, 12 for monthly.
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _returns_from_values(values: pd.Series) -> pd.Series:
    """Convert a value series to a period-return series."""
    return values.pct_change().dropna()


# ---------------------------------------------------------------------------
# Return metrics
# ---------------------------------------------------------------------------

def total_return(start_value: float, end_value: float) -> float:
    """Simple total return: (end - start) / start."""
    if start_value == 0:
        return float("nan")
    return (end_value - start_value) / start_value


def cagr(start_value: float, end_value: float, years: float) -> float:
    """
    Compound Annual Growth Rate.

    CAGR = (end / start) ^ (1 / years) - 1
    """
    if start_value <= 0 or end_value <= 0 or years <= 0:
        return float("nan")
    return (end_value / start_value) ** (1.0 / years) - 1.0


def cagr_from_values(values: pd.Series, periods_per_year: int = 252) -> float:
    """Compute CAGR directly from a value series."""
    if values.empty or len(values) < 2:
        return float("nan")
    years = (len(values) - 1) / periods_per_year
    return cagr(float(values.iloc[0]), float(values.iloc[-1]), years)


# ---------------------------------------------------------------------------
# Risk / drawdown metrics
# ---------------------------------------------------------------------------

def max_drawdown(values: pd.Series) -> float:
    """
    Maximum drawdown as a negative fraction (e.g. -0.35 means −35%).

    MDD = max over all windows of (trough - peak) / peak
    """
    if values.empty or len(values) < 2:
        return float("nan")
    peak = values.cummax()
    dd = (values - peak) / peak
    return float(dd.min())


def max_drawdown_from_returns(returns: pd.Series) -> float:
    """Compute max drawdown from a returns series."""
    if returns.empty:
        return float("nan")
    values = (1 + returns).cumprod()
    return max_drawdown(values)


def volatility(returns: pd.Series, periods_per_year: int = 252) -> float:
    """Annualised standard deviation of returns."""
    if returns.empty or len(returns) < 2:
        return float("nan")
    return float(returns.std(ddof=1) * math.sqrt(periods_per_year))


# ---------------------------------------------------------------------------
# Risk-adjusted return metrics
# ---------------------------------------------------------------------------

def sharpe(
    returns: pd.Series,
    risk_free_rate: float = 0.0,
    periods_per_year: int = 252,
) -> float:
    """
    Annualised Sharpe ratio.

    Sharpe = mean(excess_returns) / std(returns) * sqrt(periods_per_year)
    where excess_returns = returns − risk_free_rate / periods_per_year
    """
    if returns.empty or len(returns) < 2:
        return float("nan")
    rf_period = risk_free_rate / periods_per_year
    excess = returns - rf_period
    std = excess.std(ddof=1)
    if std == 0 or np.isnan(std):
        return float("nan")
    return float(excess.mean() / std * math.sqrt(periods_per_year))


def sortino(
    returns: pd.Series,
    risk_free_rate: float = 0.0,
    periods_per_year: int = 252,
) -> float:
    """
    Annualised Sortino ratio (uses downside deviation in the denominator).

    Sortino = mean(excess_returns) / downside_std * sqrt(periods_per_year)
    where downside_std = std of negative excess returns only
    """
    if returns.empty or len(returns) < 2:
        return float("nan")
    rf_period = risk_free_rate / periods_per_year
    excess = returns - rf_period
    downside = excess[excess < 0]
    if downside.empty or downside.std(ddof=1) == 0:
        return float("nan")
    return float(excess.mean() / downside.std(ddof=1) * math.sqrt(periods_per_year))


# ---------------------------------------------------------------------------
# Equity curve utilities
# ---------------------------------------------------------------------------

def equity_curve(values: pd.Series, base: float = 100.0) -> pd.Series:
    """
    Normalise a value series so the first point equals `base` (default 100).

    Useful for overlaying portfolio vs benchmark on the same chart.
    """
    if values.empty or float(values.iloc[0]) == 0:
        return values.copy()
    return values / float(values.iloc[0]) * base


def build_portfolio_curve(
    positions: list[dict],
    reader,  # BarReader — typed as Any to avoid circular import
    start_utc=None,
    end_utc=None,
) -> pd.Series:
    """
    Construct a daily portfolio value series from a list of open positions.

    Each dict in `positions` must have:
      symbol, exchange, asset_class, quantity (float)

    Returns pd.Series {timestamp (UTC) → total_portfolio_value}.
    Uses forward-fill to handle symbols with different trading calendars.
    """
    frames: dict[str, pd.Series] = {}

    for pos in positions:
        df = reader.read(
            symbol=pos["symbol"],
            exchange=pos["exchange"],
            asset_class=pos["asset_class"],
            frequency="daily",
            start_utc=start_utc,
            end_utc=end_utc,
        )
        if df.empty:
            continue
        df = df.set_index("timestamp")["close"].sort_index()
        frames[pos["symbol"]] = df * float(pos["quantity"])

    if not frames:
        return pd.Series(dtype=float)

    combined = pd.DataFrame(frames)
    combined = combined.ffill().dropna(how="all")
    return combined.sum(axis=1).rename("portfolio_value")


# ---------------------------------------------------------------------------
# Summary convenience function
# ---------------------------------------------------------------------------

def compute_metrics(
    values: pd.Series,
    risk_free_rate: float = 0.0,
    periods_per_year: int = 252,
) -> dict:
    """
    Compute the full set of metrics from a portfolio value series.

    Returns a dict with keys:
      total_return, cagr, max_drawdown, volatility, sharpe, sortino
    All values are floats (nan when insufficient data).
    """
    if values.empty or len(values) < 2:
        return {
            "total_return": float("nan"),
            "cagr": float("nan"),
            "max_drawdown": float("nan"),
            "volatility": float("nan"),
            "sharpe": float("nan"),
            "sortino": float("nan"),
        }

    rets = _returns_from_values(values)

    return {
        "total_return": total_return(float(values.iloc[0]), float(values.iloc[-1])),
        "cagr": cagr_from_values(values, periods_per_year),
        "max_drawdown": max_drawdown(values),
        "volatility": volatility(rets, periods_per_year),
        "sharpe": sharpe(rets, risk_free_rate, periods_per_year),
        "sortino": sortino(rets, risk_free_rate, periods_per_year),
    }
