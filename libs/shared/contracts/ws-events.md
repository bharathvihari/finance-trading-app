# WebSocket Event Contract (Draft)

## Channels

- `market.{symbol}`: live ticks/bars by symbol
- `portfolio.{userId}`: user positions, pnl, risk updates
- `orders.{userId}`: order status updates
- `alerts.{userId}`: strategy and risk alerts

## Envelope

```json
{
  "channel": "portfolio.user-123",
  "type": "pnl_update",
  "timestamp": "2026-04-09T08:00:00Z",
  "payload": {}
}
```

## Recommended event types

- `bar_update`
- `tick_update`
- `position_update`
- `pnl_update`
- `order_update`
- `alert_triggered`
