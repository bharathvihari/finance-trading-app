PROJECT: Historical Market Data Backfill + Daily Incremental Updates  
TARGET STACK:  
- Data store: DuckDB + Parquet (on disk)  
- Data processing: pandas  
- Broker data source: IBKR Client Portal Gateway via NautilusTrader  
- Timezone standard: UTC  

HIGH-LEVEL OBJECTIVES  
1. Build a robust, restartable backfill process to download all available historical data for many instruments and store it locally.  
2. Build a daily incremental job that keeps this local history up to date.  
3. Ensure IBKR API usage stays safely within rate limits (use ~60–70% of allowed capacity).  
4. Make the solution reusable across multiple stock exchanges and instrument types (equities + indices).  
5. Initial implementation: daily bar data only (no intraday/minute/tick data), but design the system so additional frequencies (e.g., hourly, 1-minute, ticks) can be added later and stored alongside daily data.  

-------------------------------------------------------------------------------  
A. BACKFILL PIPELINE (ONE-TIME / OCCASIONAL FULL HISTORY LOAD)  
-------------------------------------------------------------------------------  

Goal:  
- Download full historical daily bar data for a configurable universe of tickers (equities and indices) from IBKR via NautilusTrader.  
- Store the data locally as Parquet in a layout that is easy to query and to import into NautilusTrader.  
- The architecture must be extensible to support additional frequencies (e.g., 1-hour, 1-minute, ticks) in the future, without redesigning the whole system.  

Functional requirements:  
1. Input universe:  
   - Accept a configuration that defines:  
     - Exchanges (e.g., NYSE, NASDAQ, LSE, etc.).  
     - Symbols/tickers per exchange.  
     - A priority list of tickers.  
     - A priority list of indices.  
   - Priority tickers and indices must be processed completely before non-priority ones.  

2. Historical range discovery:  
   - For each contract, use `reqHeadTimeStamp` (or equivalent in NautilusTrader/IBKR) to discover the earliest available data point.  
   - Backfill from the latest data backward to the earliest available date.  

3. Year-by-year filling strategy:  
   - For each instrument (stock or index):  
     - Download data in yearly “windows”, starting from the most recent year and moving backward year by year until the earliest year is reached.  
     - Ensure that each year is fully covered before moving to the next earlier year.  

4. Pagination within a year (IBKR per-request data limits):  
   - IBKR limits data returned per request (e.g., ~1,000 ticks or a limited time span).  
   - Within each year-window, chunk the data by:  
     - Requesting a segment (e.g., some time interval).  
     - Using the returned end timestamp to request the next segment.  
   - Continue iterating until the entire year is filled.  
   - Avoid overlapping requests and keep them strictly ordered in time.  

5. Restartability:  
   - The backfill job must be resumable:  
     - If the process stops mid-way (e.g., after N instruments or midway through a given ticker/year), a restart should:  
       - Detect already completed (ticker, year) data slices in the relevant Parquet dataset and skip them.  
       - Detect partially completed segments and continue from the last successfully written timestamp.  
   - Maintain a simple metadata/index table (e.g., in DuckDB or a small metadata file) tracking:  
     - Ticker  
     - Year  
     - Earliest and latest timestamps downloaded  
     - Completion status (NOT_STARTED / IN_PROGRESS / COMPLETE)  
     - Last successful request time, error state if any  

-------------------------------------------------------------------------------  
B. DAILY INCREMENTAL UPDATE JOB  
-------------------------------------------------------------------------------  

Goal:  
- At least once per day, fetch the latest **daily** bar data for each instrument and append it to the historical dataset.  
- Design the logic so that in the future the same pattern could be applied to other frequencies (e.g., hourly) using separate datasets.  

Functional requirements:  
1. Scheduling:  
   - Implement as a separate script or entry point that can be scheduled via cron/systemd/Task Scheduler.  
   - Runs at least once per day (configurable).  

2. Incremental logic:  
   - For each instrument:  
     - Detect the latest timestamp available in the local **daily** data (from Parquet and/or DuckDB metadata).  
     - Request new daily data from IBKR starting from (latest_local_timestamp + small_delta) up to “now”.  
   - Append new data to the latest year’s daily Parquet partition directory or create a new year partition if we cross into a new calendar year.  
   - For the daily partition layout, write new parquet part files under paths such as:  
     - `price-data/asset_class={ASSET_CLASS}/exchange=NASDAQ/frequency=daily/year=2023/part-000.parquet`.  

3. Restartability and idempotence:  
   - Multiple runs should not create duplicate data.  
   - Use primary key-like criteria (ticker, timestamp, possibly exchange) to deduplicate:  
     - Either on write (enforce uniqueness) or via post-processing compaction step.  

-------------------------------------------------------------------------------  
C. DATA MODEL, STORAGE, AND FILE LAYOUT  
-------------------------------------------------------------------------------  

1. Parquet storage layout:  
   - Default approach:  
     - Directory structure (include asset_class, exchange, frequency, and year as partition keys):  
       - `price-data/asset_class={ASSET_CLASS}/exchange={EXCHANGE}/frequency={FREQUENCY}/year={YEAR}/part-000.parquet`  
       - Example for daily bars:  
         - `price-data/asset_class=equity/exchange=NASDAQ/frequency=daily/year=2023/part-000.parquet`  
   - Frequency separation:  
     - Each frequency (e.g., `daily`, `1h`, `1min`, `ticks`) must have its **own set of Parquet files/directories** via the `frequency=...` partition.  
     - The initial implementation uses only `FREQUENCY = "daily"`.  
   - Partitioning strategy:  
     - Partition by asset_class, exchange, frequency, and year.  
     - Store symbol as a regular column inside the Parquet files within each partition.  
     - If helpful, consider an additional level by data type within a frequency in the future (e.g., `bars_1min`, `ticks`), but keep daily bars straightforward.  
   - Requirements:  
     - Fast load for a specific symbol and date range.  
     - Easy discovery of available instruments and time ranges.  
   - (Optional / open to improvement):  
     - Use a single partitioned Parquet dataset with partition columns: asset_class, exchange, frequency, year, while keeping symbol as a data column inside each file.  
     - Evaluate tradeoff: yearly part-file sizing and compaction strategy vs too many small files.  

2. DuckDB integration:  
   - Use DuckDB to:  
     - Query Parquet files directly.  
     - Maintain metadata tables for:  
       - Job progress (backfill state).  
       - Data coverage per symbol/year.  
     - Potentially materialize certain views or aggregates for faster lookups.  

3. Writing Parquet:  
   - Use `pyarrow` or DuckDB to write parquet from pandas DataFrames.  
   - Ensure:  
     - Consistent schema across all files for a given frequency.  
     - Appropriate data types for timestamps (timezone-aware, converted to UTC).  
     - Column naming consistent with NautilusTrader expectations (or clearly mapped).  
     - Required partition columns (`asset_class`, `exchange`, `frequency`, `year`) and symbol column are written consistently with the dataset layout.  

4. Timezone:  
   - Convert all timestamps to UTC before storage.  
   - Make timezone conversion explicit and centralized in the code.  
   - Document the source timezone per exchange if needed, but always persist as UTC.  

-------------------------------------------------------------------------------  
D. IBKR / NAUTILUSTRADER INTEGRATION & RATE LIMITS  
-------------------------------------------------------------------------------  

1. Data source:  
   - Use NautilusTrader to interact with IBKR Client Portal Gateway.  
   - Use the appropriate NautilusTrader APIs to request historical **daily bars**.  
   - Design the code so that other bar sizes (e.g., hourly, minute) can be enabled in the future by changing configuration and adding frequency-specific queries.  

2. whatToShow / data adjustment modes:  
   - Use:  
     - `whatToShow='TRADES'` for standard unadjusted trade data.  
     - `whatToShow='ADJUSTED_LAST'` for data adjusted for splits and dividends.  
   - Make this choice configurable per job or per instrument universe.  

3. Regular trading hours:  
   - Use `use_regular_trading_hours=True` when only standard session data is desired.  
   - Make this flag configurable (e.g., allow extended-hours data when needed).  

4. Rate limiting and pacing (IBKR safety buffer):  
   - Always target using only ~60–70% of IBKR allowed limits for safety.  
   - Implement a rate limiter with the following rules (configurable constants):  
     - No more than 60 historical data requests within any rolling 10-minute window.  
     - Do not make identical requests for the same instrument/timeframe within 15 seconds.  
   - Randomized pacing:  
     - Introduce jitter/randomization in inter-request delays to simulate human-like usage patterns.  
     - Example: base delay + random jitter in a range.  

5. Error handling and backoff:  
   - On rate-limit or temporary errors:  
     - Implement exponential backoff with a maximum backoff limit.  
     - Log errors, update job metadata, and continue with other instruments where possible.  
   - On persistent errors for a symbol:  
     - Mark symbol/year as failed in metadata and continue.  
     - Provide a way to retry failed partitions later.  

-------------------------------------------------------------------------------  
E. DATA QUALITY, VALIDATION, AND NAUTILUSTRADER IMPORT  
-------------------------------------------------------------------------------  

1. Import into NautilusTrader:  
   - Create a separate script/module that:  
     - Loads **daily** Parquet data (by symbol and date range) from the partitioned dataset.  
     - Maps the records into NautilusTrader’s native `Bar` objects (daily bars).  
     - Handles any schema conversions (column names, dtypes, etc.).  
   - Design the import pipeline so that additional frequencies (e.g., hourly, 1-minute, ticks) can be supported later by:  
     - Adding frequency-specific mapping logic.  
     - Ensuring frequency is part of the dataset path/config.  

2. Validation:  
   - Use methods from NautilusTrader (e.g., Chapter 4 tools) to:  
     - Detect missing daily bars or gaps.  
     - Check for time synchronization issues or duplicate timestamps.  
   - Implement:  
     - Basic sanity checks (no negative prices, volumes ≥ 0, timestamps strictly increasing within a symbol).  
     - Summary statistics per symbol/year (e.g., record counts, min/max timestamps).  

3. Corporate actions and adjustments:  
   - In addition to initial backfill:  
     - Implement a separate, periodic job to adjust historical data for splits and similar corporate actions.  
       - This may involve:  
         - Fetching `ADJUSTED_LAST` data.  
         - Reconciling with previously stored `TRADES` data.  
         - Either:  
           - Storing separate adjusted datasets, or  
           - Re-writing partitions with adjusted prices (controlled by config).  
   - Ensure that the adjustment job is idempotent and clearly logged.  

-------------------------------------------------------------------------------  
F. CONFIGURATION & EXTENSIBILITY  
-------------------------------------------------------------------------------  

1. Configuration:  
   - Use a YAML/JSON/toml config file (or environment variables) for:  
     - Exchange and symbol lists.  
     - Priority tickers and indices.  
     - **Data frequency**:  
       - For now, support and use only `daily` (e.g., `1D` bars).  
       - Design this as a configurable field so additional frequencies (e.g., `1H`, `1Min`, `Tick`) can be enabled in the future.  
       - Each frequency must map to its own Parquet directory tree (e.g., `price-data/asset_class=equity/exchange=NASDAQ/frequency=daily/...`, `price-data/asset_class=equity/exchange=NASDAQ/frequency=1H/...`).  
     - whatToShow (TRADES vs ADJUSTED_LAST).  
     - use_regular_trading_hours flag.  
     - Exchange `reference_symbol` used to infer each exchange's latest traded date.  
     - Exchange reference fallback order when inferring latest traded date: configured `reference_symbol`, then first configured index, then first configured stock symbol.  
     - `fail_on_unresolved_exchange_last_traded` guard to fail the backfill job if an exchange latest traded date cannot be resolved from all fallbacks.  
     - Rate limit parameters (requests per window, window size, jitter).  
     - Paths for Parquet storage and DuckDB database.  

2. Reusability for multiple exchanges:  
   - Abstract exchange-specific details into config or small adapter classes:  
     - Symbol formats, trading hours, etc.  
   - Ensure the backfill and daily jobs can:  
     - Loop over all configured exchanges and their symbols.  
     - Treat indices similarly to stocks, with their own priority lists.  

3. Logging and observability:  
   - Provide structured logs:  
     - Start/finish events for each ticker/year.  
     - Rate-limiting events and backoff.  
     - Errors and validation failures.  
   - Request-level logging (at DEBUG level via Python `logging`):  
     - **HTTP request logs** in `NautilusIbkrBackend._request_json()`:  
       - Method (GET/POST), endpoint path, query params, JSON payload  
       - Captures every call to IBKR Client Portal Gateway  
     - **Contract resolution logs** in `_resolve_conid()`:  
       - Symbol, exchange, asset_class  
       - Cache hits vs. fresh lookups  
       - Resolved conid values  
     - **History fetch logs** in `_fetch_history()`:  
       - conid, period (e.g., "1y"), bar_size, RTH flag, end timestamp  
       - Response row count  
     - **Head timestamp discovery logs** in `NautilusIbkrBackend.get_head_timestamp()`:  
       - Page number, cursor timestamp  
       - Oldest timestamp found per page  
       - Progress across paginated requests  
     - **High-level bar request logs** in `IbkrHistoricalClient.fetch_bars()`:  
       - Full request parameters: symbol, exchange, asset_class, frequency, bar_size, what_to_show, use_rth, date range  
       - Normalized row count in response  
     - **Market snapshot logs** in `IbkrHistoricalClient.fetch_market_snapshot()`:  
       - List of conids, instrument count  
       - Row count returned  
   - Optional: Generate a small report (e.g., JSON/HTML/CSV) summarizing:  
     - Coverage per symbol.  
     - Last updated time.  
     - Any failed or incomplete partitions.
