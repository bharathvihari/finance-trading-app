For the dashboards you described, you minimally need these datasets in Parquet for **NASDAQ equities**:

1. **Instrument master (static data)**
   - `symbol`, `exchange`, `asset_class`, `instrument_type`
   - `name`, `currency`, `primary_mic`
   - `sector`, `industry`, `country`
   - `listing_date`, `delisting_date` (if any)
   - Stable IDs if available: `isin`, `cusip` (optional but useful)

2. **Daily price history (bars, per symbol)**
   - `timestamp` (session close, UTC)
   - `symbol`
   - `open`, `high`, `low`, `close`
   - `adjusted_close` (or equivalent adjusted prices)
   - `volume`
   - Optional but useful: `vwap`, `trade_count`

3. **Corporate actions**
   - **Splits**: `symbol`, `ex_date`, `split_ratio` (old:new or factor)
   - **Dividends**: `symbol`, `ex_date`, `record_date`, `pay_date`, `dividend_amount`, `dividend_type`

4. **FX rates (for multi-currency reporting)**
   - Daily FX rates vs user/base currency:
     - `date`, `from_currency`, `to_currency`, `fx_rate`

5. **Trading calendar (optional but helpful)**
   - NASDAQ trading days + holidays:
     - `date`, `is_trading_day`, `session_open`, `session_close`