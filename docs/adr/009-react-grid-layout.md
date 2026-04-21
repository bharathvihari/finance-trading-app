# ADR-009: react-grid-layout for Dashboard Panel Layout

## Status
Accepted (planned — implementation in frontend Phase)

## Context
Dashboard requirements §2 specify multi-panel layouts with drag-and-drop, resizing, and
per-widget full-screen mode. The layout must be user-defined and persisted to Postgres
(`dashboard_layouts`, `widget_configs`).

Options considered:
- **gridstack.js** — mentioned in the original architecture doc, framework-agnostic, but
  React integration is via a wrapper with known lifecycle issues.
- **dnd-kit** — excellent drag-and-drop primitives, but resize support requires custom
  implementation. Good for lists; more work for 2D grid.
- **react-grid-layout** — purpose-built for draggable, resizable 2D grid layouts in React.
  Used by many trading dashboard products. Layout is a JSON array of `{i, x, y, w, h}` objects —
  trivially serialisable to Postgres JSONB.

## Decision
Use **react-grid-layout**.

Layout state is `GridLayout.Layout[]` — an array of `{i: widgetId, x, y, w, h, ...}`.
This maps directly to the `position_json` column in `widget_configs`:
```sql
position_json JSONB  -- {"x": 0, "y": 0, "w": 6, "h": 4, "minW": 2, "minH": 2}
```

On layout change (`onLayoutChange` callback), the frontend PATCHes the new positions to
`PATCH /api/v1/dashboards/{id}/widgets/{widgetId}` — a single bulk update per drag operation.

## Consequences

### Good
- `layout` is a plain JSON array — persisted to Postgres as JSONB and restored on page load.
- Responsive breakpoints (xl / lg / md / sm) are built-in — works on tablet and smaller screens.
- Per-widget `minW`, `minH`, `maxW`, `maxH` constraints prevent nonsensical layouts.
- Full-screen mode is a separate concern (CSS `position: fixed` overlay) that does not affect
  the underlying grid layout state.

### Bad
- `react-grid-layout` does not support touch drag natively on mobile — requires
  `react-grid-layout`'s touch patch or a separate mobile layout strategy.
- Column count is fixed per breakpoint — very narrow screens may need a stacked single-column
  fallback rather than a scaled-down grid.
