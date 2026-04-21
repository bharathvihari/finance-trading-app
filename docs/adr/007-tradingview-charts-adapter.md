# ADR-007: TradingView Lightweight Charts with Framework-Agnostic Adapter

## Status
Accepted

## Context
The dashboard needs professional-grade financial charting: candlestick, line, area, histogram
series; crosshair sync; event markers (dividends, splits, earnings); user annotations.

Options considered:
- **Plotly** — general-purpose, heavy, limited financial-specific features.
- **ECharts / ngx-echarts** — good but primarily a data-vis library, not a trading chart library.
- **TradingView Lightweight Charts** — purpose-built for financial time-series, MIT licensed,
  performant WebGL canvas rendering, first-class TypeScript support.

## Decision
Use **TradingView Lightweight Charts** as the charting library, accessed through a
**framework-agnostic adapter layer** defined in the Brain.

The Brain defines a unified `ChartSeries` response model (`apps/api/app/api/schemas/bars.py`):
```
ChartSeries {
  symbol, exchange, asset_class, frequency,
  series_type: "candlestick" | "line" | "area" | "histogram",
  bars: [{t, o, h, l, c, v}],
  markers: [{t, type, label, color}],       # dividends, splits, earnings, trades
  annotations: [{...}]                       # user trendlines / notes
}
```

The frontend adapter maps this model to TradingView's specific `ISeriesApi` and `SeriesMarker`
formats. No backend code changes are needed if the chart library is swapped.

## Consequences

### Good
- TradingView Lightweight Charts is the industry standard for web-based trading dashboards —
  candlestick rendering, crosshair, zoom/pan all work out of the box.
- The adapter layer means the API contract is frozen; swapping to Plotly or another library
  is a frontend-only change.
- `series_type` in the response model supports future non-candlestick series (indicator lines,
  equity curves, histograms for volume/RSI) without changing the API.

### Bad
- TradingView Lightweight Charts is designed for canvas rendering — it does not support
  React's virtual DOM model natively. Requires a React wrapper (`lightweight-charts` React
  bindings or a custom `useEffect`-based wrapper).
- Complex crosshair synchronisation across multiple chart panels requires manual subscriber wiring.
