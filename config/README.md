# Market Data Job Configs

- `market_data.backfill.yaml`: full-history load configuration.
- `market_data.daily.yaml`: incremental daily update configuration.

Both files are starter templates. Update symbols, exchange coverage, and IBKR modes before production use.

For Client Portal Gateway default local setup:
- `ibkr.host: 127.0.0.1`
- `ibkr.port: 5000`
- `ibkr.fallback_enabled: false` (fail fast instead of silently skipping data)
