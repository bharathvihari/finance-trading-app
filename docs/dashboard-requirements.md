**Advanced Dashboard Requirements (Condensed, TradingView Lightweight Charts–First)**

---

**0. Architecture Philosophy: Brain vs. Skin**

Data app work splits into two layers: **The Brain** (data & logic — ~80% of total effort) and **The Skin** (the UI framework). Choosing Streamlit, React, or Angular for the Skin never requires redoing the Brain. Specifically, what is permanently stable regardless of UI choice:

- **The Database** — Postgres schemas, tables, and historical Parquet data remain untouched.
- **The Data Pipeline** — Python scripts that pull daily incremental data from IBKR (CPG) stay exactly the same.
- **The Logic** — Calculations (moving averages, P&L, risk metrics) are pure Python functions. These are exposed as an API that any frontend (Streamlit today, React tomorrow) can call without modification.

This is why the adapter layer in Section 8 exists: the unified chart/series abstraction is the contract between the permanent Brain and the replaceable Skin.

---

**1. Scope & Platforms**  
- Multi-user, internet-accessible dashboards for desktop browser + iOS + Android.  
- Supports securities history, multi-broker portfolios, and backtest/strategy results.  
- Current chart UI: **TradingView Lightweight Charts**, via a framework-agnostic adapter layer.

---

**2. Dashboard UX & Layout**  
- **User-defined dashboards**: create, save, edit, and delete custom dashboards and custom charts (widgets).  
- **Fast context switching** between standard views:  
  - Market Overview (indices, heatmaps or equivalent)  
  - Portfolio Overview  
  - Single-Asset Analysis  
  - Strategy / Backtest Results  
  - News & Events (when data source is added)  
- Multi-panel layouts with drag-and-drop, resizing, and per-widget full-screen mode.  
- Linked symbol/time context: selecting a symbol or time range updates relevant widgets.  
- Global time range control: `1D / 5D / 1M / 6M / 1Y / 5Y / All`.

---

**3. Charting, Events & Indicators**  
- Core chart types (as supported by Lightweight Charts): candlestick, line, area, histogram; plus scatter via adapter if/when needed.  
- Crosshair, zoom/pan, synchronized tooltips across charts.  
- **Pluggable indicator system** (per-chart configuration stored with dashboard): at least SMA, EMA, WMA, Bollinger Bands, RSI, MACD, ATR; extensible.  
- **Event markers on charts** with per-type visibility toggles:  
  - Dividends  
  - Splits  
  - Earnings  
  - Trades / buy-sell signals  
- User annotations tied to symbol + timestamp (trendlines, notes).

---

**4. Portfolio & Performance Analytics**  
- Aggregate across **multiple brokers and currencies**:  
  - Total equity, cash, unrealized/realized P&L.  
  - Time-weighted returns (TWRR) and money-weighted returns (MWRR/IRR).  
  - Benchmark comparison vs. configurable indices (e.g., SPX, NIFTY, STOXX).  
- Support for multiple portfolios and **pseudo-accounts**: personal, demo, paper-trading, custom sub-portfolios.  

---

**5. Backtests & Strategy Views**  
- Standard backtest dashboard: equity curve, drawdown curve, key metrics (CAGR, Sharpe, Sortino, max DD, hit rate), and trade list.  
- Compare strategies (equity curve overlays and metric tables) within dashboards.

---

**6. Personalization & Security**  
- Per-user preferences: base currency, date/time format, and display time zone (data stored in UTC, rendered in user’s TZ).  
- **Hard data isolation**: each user only sees their own portfolios, brokers, backtests, and dashboard configs.  
- Support for multiple per-user “accounts” (live, demo, paper, imported portfolios).

---

**7. Performance & Data Layer Behavior**  
- Latency targets (p95 under normal load):  
  - Historical chart load from Parquet: **< 500–800 ms** for typical ranges.  
  - Portfolio overview (aggregations across brokers): **< 1–2 seconds**.  
- **Pre-aggregated metrics** for expensive calculations (rolling performance, risk metrics) stored or cached server-side.  
- **API-level caching** for frequent queries (e.g., last 1Y daily data for popular indices, user’s main portfolio curve).  
- Dashboard data source order:  
  1. Load from local Parquet if available.  
  2. If missing, pull from IBKR via NautilusTrader (dashboard calls take priority over backfill).  
  3. Serve data to UI, then persist to Parquet/DuckDB.  

---

**8. Framework-Agnostic Presentation Layer (Adapter)**  
- **Unified “chart/series” abstraction** as the internal contract between back end and any UI framework:  
  - Series types: line, candlestick, area, histogram, scatter.  
  - Events: markers (dividends, splits, earnings, trades), user annotations.  
- Per-framework adapter (starting with **TradingView Lightweight Charts**) maps this unified model to specific chart options/series/marker formats.  
- All dashboard and widget definitions refer only to the unified model, enabling future replacement or addition of React/Angular/Plotly/etc. without changing business logic.