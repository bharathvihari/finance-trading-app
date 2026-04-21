from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

try:
    import duckdb  # type: ignore
except Exception:  # pragma: no cover - optional runtime dependency in lightweight envs
    duckdb = None  # type: ignore[assignment]


class DuckDbMetaStore:
    """DuckDB-backed metadata store for backfill and daily jobs."""

    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)

    def _connect(self) -> duckdb.DuckDBPyConnection:
        if duckdb is None:
            raise ModuleNotFoundError("duckdb is required for DuckDbMetaStore operations.")
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        return duckdb.connect(str(self.db_path))

    def init_schema(self) -> None:
        conn = self._connect()
        try:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS job_runs (
                    run_id TEXT PRIMARY KEY,
                    job_name TEXT NOT NULL,
                    mode TEXT NOT NULL,
                    status TEXT NOT NULL,
                    started_at TIMESTAMPTZ NOT NULL,
                    finished_at TIMESTAMPTZ,
                    processed_count BIGINT NOT NULL DEFAULT 0,
                    failed_count BIGINT NOT NULL DEFAULT 0,
                    notes TEXT
                );
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS backfill_slices (
                    symbol TEXT NOT NULL,
                    exchange TEXT NOT NULL,
                    asset_class TEXT NOT NULL,
                    frequency TEXT NOT NULL,
                    year INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    earliest_downloaded_ts TIMESTAMPTZ,
                    latest_downloaded_ts TIMESTAMPTZ,
                    last_success_request_at TIMESTAMPTZ,
                    last_error TEXT,
                    updated_at TIMESTAMPTZ NOT NULL,
                    PRIMARY KEY (symbol, exchange, asset_class, frequency, year)
                );
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS data_coverage (
                    symbol TEXT NOT NULL,
                    exchange TEXT NOT NULL,
                    asset_class TEXT NOT NULL,
                    frequency TEXT NOT NULL,
                    min_ts TIMESTAMPTZ,
                    max_ts TIMESTAMPTZ,
                    row_count BIGINT NOT NULL DEFAULT 0,
                    updated_at TIMESTAMPTZ NOT NULL,
                    PRIMARY KEY (symbol, exchange, asset_class, frequency)
                );
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS parquet_symbols (
                    symbol TEXT NOT NULL,
                    exchange TEXT NOT NULL,
                    asset_class TEXT NOT NULL,
                    frequency TEXT NOT NULL,
                    first_seen_at TIMESTAMPTZ NOT NULL,
                    last_seen_at TIMESTAMPTZ NOT NULL,
                    PRIMARY KEY (symbol, exchange, asset_class, frequency)
                );
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS exchange_trading_dates (
                    exchange TEXT NOT NULL,
                    frequency TEXT NOT NULL,
                    asset_class TEXT NOT NULL,
                    last_traded_ts TIMESTAMPTZ NOT NULL,
                    checked_at TIMESTAMPTZ NOT NULL,
                    PRIMARY KEY (exchange, frequency, asset_class)
                );
                """
            )
            # Migrate legacy schema (exchange, frequency) -> combo schema
            # without dropping user data.
            exchange_date_cols = {
                str(row[1]).lower()
                for row in conn.execute("PRAGMA table_info('exchange_trading_dates');").fetchall()
            }
            if "asset_class" not in exchange_date_cols:
                conn.execute("ALTER TABLE exchange_trading_dates RENAME TO exchange_trading_dates_legacy;")
                conn.execute(
                    """
                    CREATE TABLE exchange_trading_dates (
                        exchange TEXT NOT NULL,
                        frequency TEXT NOT NULL,
                        asset_class TEXT NOT NULL,
                        last_traded_ts TIMESTAMPTZ NOT NULL,
                        checked_at TIMESTAMPTZ NOT NULL,
                        PRIMARY KEY (exchange, frequency, asset_class)
                    );
                    """
                )
                conn.execute(
                    """
                    INSERT INTO exchange_trading_dates (
                        exchange, frequency, asset_class, last_traded_ts, checked_at
                    )
                    SELECT exchange, frequency, 'equity', last_traded_ts, checked_at
                    FROM exchange_trading_dates_legacy;
                    """
                )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS symbol_sync_status (
                    symbol TEXT NOT NULL,
                    exchange TEXT NOT NULL,
                    asset_class TEXT NOT NULL,
                    frequency TEXT NOT NULL,
                    status TEXT NOT NULL,
                    earliest_required_ts TIMESTAMPTZ,
                    earliest_ts TIMESTAMPTZ,
                    latest_ts TIMESTAMPTZ,
                    last_traded_ts TIMESTAMPTZ,
                    updated_at TIMESTAMPTZ NOT NULL,
                    PRIMARY KEY (symbol, exchange, asset_class, frequency)
                );
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS job_errors (
                    run_id TEXT NOT NULL,
                    created_at TIMESTAMPTZ NOT NULL,
                    scope TEXT NOT NULL,
                    symbol TEXT,
                    exchange TEXT,
                    year INTEGER,
                    error_message TEXT NOT NULL
                );
                """
            )
        finally:
            conn.close()

    def start_job_run(self, job_name: str, mode: str) -> str:
        run_id = str(uuid4())
        now = datetime.now(timezone.utc)

        conn = self._connect()
        try:
            conn.execute(
                """
                INSERT INTO job_runs (run_id, job_name, mode, status, started_at)
                VALUES (?, ?, ?, 'IN_PROGRESS', ?);
                """,
                [run_id, job_name, mode, now],
            )
        finally:
            conn.close()

        return run_id

    def finish_job_run(
        self,
        run_id: str,
        status: str,
        processed_count: int = 0,
        failed_count: int = 0,
        notes: str | None = None,
    ) -> None:
        finished_at = datetime.now(timezone.utc)

        conn = self._connect()
        try:
            conn.execute(
                """
                UPDATE job_runs
                SET status = ?,
                    finished_at = ?,
                    processed_count = ?,
                    failed_count = ?,
                    notes = ?
                WHERE run_id = ?;
                """,
                [status, finished_at, processed_count, failed_count, notes, run_id],
            )
        finally:
            conn.close()

    def append_job_error(
        self,
        run_id: str,
        scope: str,
        error_message: str,
        symbol: str | None = None,
        exchange: str | None = None,
        year: int | None = None,
    ) -> None:
        now = datetime.now(timezone.utc)

        conn = self._connect()
        try:
            conn.execute(
                """
                INSERT INTO job_errors (run_id, created_at, scope, symbol, exchange, year, error_message)
                VALUES (?, ?, ?, ?, ?, ?, ?);
                """,
                [run_id, now, scope, symbol, exchange, year, error_message],
            )
        finally:
            conn.close()

    def upsert_slice_progress(
        self,
        symbol: str,
        exchange: str,
        asset_class: str,
        frequency: str,
        year: int,
        status: str,
        earliest_downloaded_ts: datetime | None = None,
        latest_downloaded_ts: datetime | None = None,
        last_success_request_at: datetime | None = None,
        last_error: str | None = None,
    ) -> None:
        now = datetime.now(timezone.utc)

        conn = self._connect()
        try:
            conn.execute(
                """
                INSERT INTO backfill_slices (
                    symbol, exchange, asset_class, frequency, year, status,
                    earliest_downloaded_ts, latest_downloaded_ts, last_success_request_at,
                    last_error, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(symbol, exchange, asset_class, frequency, year)
                DO UPDATE SET
                    status = EXCLUDED.status,
                    earliest_downloaded_ts = COALESCE(
                        LEAST(backfill_slices.earliest_downloaded_ts, EXCLUDED.earliest_downloaded_ts),
                        backfill_slices.earliest_downloaded_ts,
                        EXCLUDED.earliest_downloaded_ts
                    ),
                    latest_downloaded_ts = COALESCE(
                        GREATEST(backfill_slices.latest_downloaded_ts, EXCLUDED.latest_downloaded_ts),
                        backfill_slices.latest_downloaded_ts,
                        EXCLUDED.latest_downloaded_ts
                    ),
                    last_success_request_at = COALESCE(EXCLUDED.last_success_request_at, backfill_slices.last_success_request_at),
                    last_error = EXCLUDED.last_error,
                    updated_at = EXCLUDED.updated_at;
                """,
                [
                    symbol,
                    exchange,
                    asset_class,
                    frequency,
                    year,
                    status,
                    earliest_downloaded_ts,
                    latest_downloaded_ts,
                    last_success_request_at,
                    last_error,
                    now,
                ],
            )
        finally:
            conn.close()

    def get_slice_state(
        self,
        symbol: str,
        exchange: str,
        asset_class: str,
        frequency: str,
        year: int,
    ) -> dict[str, Any] | None:
        conn = self._connect()
        try:
            row = conn.execute(
                """
                SELECT symbol, exchange, asset_class, frequency, year, status,
                       earliest_downloaded_ts, latest_downloaded_ts, last_success_request_at,
                       last_error, updated_at
                FROM backfill_slices
                WHERE symbol = ? AND exchange = ? AND asset_class = ? AND frequency = ? AND year = ?;
                """,
                [symbol, exchange, asset_class, frequency, year],
            ).fetchone()
            if not row:
                return None
            return {
                "symbol": row[0],
                "exchange": row[1],
                "asset_class": row[2],
                "frequency": row[3],
                "year": row[4],
                "status": row[5],
                "earliest_downloaded_ts": row[6],
                "latest_downloaded_ts": row[7],
                "last_success_request_at": row[8],
                "last_error": row[9],
                "updated_at": row[10],
            }
        finally:
            conn.close()

    def upsert_coverage(
        self,
        symbol: str,
        exchange: str,
        asset_class: str,
        frequency: str,
        min_ts: datetime | None,
        max_ts: datetime | None,
        row_count: int,
    ) -> None:
        now = datetime.now(timezone.utc)

        conn = self._connect()
        try:
            conn.execute(
                """
                INSERT INTO data_coverage (
                    symbol, exchange, asset_class, frequency, min_ts, max_ts, row_count, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(symbol, exchange, asset_class, frequency)
                DO UPDATE SET
                    min_ts = COALESCE(LEAST(data_coverage.min_ts, EXCLUDED.min_ts), data_coverage.min_ts, EXCLUDED.min_ts),
                    max_ts = COALESCE(GREATEST(data_coverage.max_ts, EXCLUDED.max_ts), data_coverage.max_ts, EXCLUDED.max_ts),
                    row_count = GREATEST(data_coverage.row_count, EXCLUDED.row_count),
                    updated_at = EXCLUDED.updated_at;
                """,
                [symbol, exchange, asset_class, frequency, min_ts, max_ts, row_count, now],
            )
        finally:
            conn.close()

    def get_coverage(
        self,
        symbol: str,
        exchange: str,
        asset_class: str,
        frequency: str,
    ) -> dict[str, Any] | None:
        conn = self._connect()
        try:
            row = conn.execute(
                """
                SELECT min_ts, max_ts, row_count, updated_at
                FROM data_coverage
                WHERE symbol = ? AND exchange = ? AND asset_class = ? AND frequency = ?;
                """,
                [symbol, exchange, asset_class, frequency],
            ).fetchone()
            if not row:
                return None
            return {
                "min_ts": row[0],
                "max_ts": row[1],
                "row_count": row[2],
                "updated_at": row[3],
            }
        finally:
            conn.close()

    def upsert_parquet_symbol(
        self,
        symbol: str,
        exchange: str,
        asset_class: str,
        frequency: str,
    ) -> None:
        now = datetime.now(timezone.utc)

        conn = self._connect()
        try:
            conn.execute(
                """
                INSERT INTO parquet_symbols (
                    symbol, exchange, asset_class, frequency, first_seen_at, last_seen_at
                )
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(symbol, exchange, asset_class, frequency)
                DO UPDATE SET
                    last_seen_at = EXCLUDED.last_seen_at;
                """,
                [symbol, exchange, asset_class, frequency, now, now],
            )
        finally:
            conn.close()

    def list_parquet_symbols(self, frequency: str | None = None) -> list[dict[str, Any]]:
        conn = self._connect()
        try:
            where: list[str] = []
            params: list[Any] = []
            if frequency is not None:
                where.append("frequency = ?")
                params.append(frequency)

            query = """
                SELECT symbol, exchange, asset_class, frequency, first_seen_at, last_seen_at
                FROM parquet_symbols
            """
            if where:
                query += " WHERE " + " AND ".join(where)
            query += " ORDER BY exchange, symbol;"

            rows = conn.execute(query, params).fetchall()
            out: list[dict[str, Any]] = []
            for row in rows:
                out.append(
                    {
                        "symbol": row[0],
                        "exchange": row[1],
                        "asset_class": row[2],
                        "frequency": row[3],
                        "first_seen_at": row[4],
                        "last_seen_at": row[5],
                    }
                )
            return out
        finally:
            conn.close()

    def upsert_exchange_last_traded_date(
        self,
        exchange: str,
        frequency: str,
        asset_class: str,
        last_traded_ts: datetime,
    ) -> None:
        now = datetime.now(timezone.utc)

        conn = self._connect()
        try:
            conn.execute(
                """
                INSERT INTO exchange_trading_dates (
                    exchange, frequency, asset_class, last_traded_ts, checked_at
                )
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(exchange, frequency, asset_class)
                DO UPDATE SET
                    last_traded_ts = EXCLUDED.last_traded_ts,
                    checked_at = EXCLUDED.checked_at;
                """,
                [exchange, frequency, asset_class, last_traded_ts, now],
            )
        finally:
            conn.close()

    def get_exchange_last_traded_date(
        self,
        exchange: str,
        frequency: str,
        asset_class: str,
    ) -> dict[str, Any] | None:
        conn = self._connect()
        try:
            row = conn.execute(
                """
                SELECT exchange, frequency, asset_class, last_traded_ts, checked_at
                FROM exchange_trading_dates
                WHERE exchange = ? AND frequency = ? AND asset_class = ?;
                """,
                [exchange, frequency, asset_class],
            ).fetchone()
            if not row:
                return None
            return {
                "exchange": row[0],
                "frequency": row[1],
                "asset_class": row[2],
                "last_traded_ts": row[3],
                "checked_at": row[4],
            }
        finally:
            conn.close()

    def get_combo_parquet_sync_ts(
        self,
        exchange: str,
        frequency: str,
        asset_class: str,
    ) -> datetime | None:
        """Return the latest common parquet sync date across all symbols in a combo.

        The value is MIN(max_ts) across all symbols present in parquet_symbols
        for the combo. If any symbol lacks coverage.max_ts, returns None.
        """
        conn = self._connect()
        try:
            row = conn.execute(
                """
                SELECT
                    COUNT(*) AS symbol_count,
                    COUNT(c.max_ts) AS covered_count,
                    MIN(c.max_ts) AS combo_sync_ts
                FROM parquet_symbols p
                LEFT JOIN data_coverage c
                    ON c.symbol = p.symbol
                   AND c.exchange = p.exchange
                   AND c.asset_class = p.asset_class
                   AND c.frequency = p.frequency
                WHERE p.exchange = ?
                  AND p.frequency = ?
                  AND p.asset_class = ?;
                """,
                [exchange, frequency, asset_class],
            ).fetchone()
        finally:
            conn.close()

        if not row:
            return None

        symbol_count, covered_count, combo_sync_ts = row
        if symbol_count == 0 or covered_count < symbol_count or combo_sync_ts is None:
            return None
        return combo_sync_ts

    def upsert_symbol_sync_status(
        self,
        symbol: str,
        exchange: str,
        asset_class: str,
        frequency: str,
        status: str,
        earliest_required_ts: datetime | None,
        earliest_ts: datetime | None,
        latest_ts: datetime | None,
        last_traded_ts: datetime | None,
    ) -> None:
        now = datetime.now(timezone.utc)

        conn = self._connect()
        try:
            conn.execute(
                """
                INSERT INTO symbol_sync_status (
                    symbol, exchange, asset_class, frequency, status,
                    earliest_required_ts, earliest_ts, latest_ts, last_traded_ts, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(symbol, exchange, asset_class, frequency)
                DO UPDATE SET
                    status = EXCLUDED.status,
                    earliest_required_ts = COALESCE(EXCLUDED.earliest_required_ts, symbol_sync_status.earliest_required_ts),
                    earliest_ts = COALESCE(EXCLUDED.earliest_ts, symbol_sync_status.earliest_ts),
                    latest_ts = COALESCE(EXCLUDED.latest_ts, symbol_sync_status.latest_ts),
                    last_traded_ts = COALESCE(EXCLUDED.last_traded_ts, symbol_sync_status.last_traded_ts),
                    updated_at = EXCLUDED.updated_at;
                """,
                [
                    symbol,
                    exchange,
                    asset_class,
                    frequency,
                    status,
                    earliest_required_ts,
                    earliest_ts,
                    latest_ts,
                    last_traded_ts,
                    now,
                ],
            )
        finally:
            conn.close()

    def get_symbol_sync_status(
        self,
        symbol: str,
        exchange: str,
        asset_class: str,
        frequency: str,
    ) -> dict[str, Any] | None:
        conn = self._connect()
        try:
            row = conn.execute(
                """
                SELECT symbol, exchange, asset_class, frequency, status,
                       earliest_required_ts, earliest_ts, latest_ts, last_traded_ts, updated_at
                FROM symbol_sync_status
                WHERE symbol = ? AND exchange = ? AND asset_class = ? AND frequency = ?;
                """,
                [symbol, exchange, asset_class, frequency],
            ).fetchone()
            if not row:
                return None
            return {
                "symbol": row[0],
                "exchange": row[1],
                "asset_class": row[2],
                "frequency": row[3],
                "status": row[4],
                "earliest_required_ts": row[5],
                "earliest_ts": row[6],
                "latest_ts": row[7],
                "last_traded_ts": row[8],
                "updated_at": row[9],
            }
        finally:
            conn.close()

    def get_latest_covered_timestamp(
        self,
        symbol: str,
        exchange: str,
        asset_class: str,
        frequency: str,
    ) -> datetime | None:
        conn = self._connect()
        try:
            row = conn.execute(
                """
                SELECT max_ts
                FROM data_coverage
                WHERE symbol = ? AND exchange = ? AND asset_class = ? AND frequency = ?;
                """,
                [symbol, exchange, asset_class, frequency],
            ).fetchone()
            if not row:
                return None
            return row[0]
        finally:
            conn.close()

    def init_split_check_schema(self) -> None:
        conn = self._connect()
        try:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS split_checks (
                    symbol TEXT NOT NULL,
                    exchange TEXT NOT NULL,
                    asset_class TEXT NOT NULL,
                    last_checked_at TIMESTAMPTZ NOT NULL,
                    last_split_date TIMESTAMPTZ,
                    PRIMARY KEY (symbol, exchange, asset_class)
                );
                """
            )
        finally:
            conn.close()

    def get_last_split_check(
        self,
        symbol: str,
        exchange: str,
        asset_class: str,
    ) -> dict[str, Any] | None:
        conn = self._connect()
        try:
            row = conn.execute(
                """
                SELECT symbol, exchange, asset_class, last_checked_at, last_split_date
                FROM split_checks
                WHERE symbol = ? AND exchange = ? AND asset_class = ?;
                """,
                [symbol, exchange, asset_class],
            ).fetchone()
            if not row:
                return None
            return {
                "symbol": row[0],
                "exchange": row[1],
                "asset_class": row[2],
                "last_checked_at": row[3],
                "last_split_date": row[4],
            }
        finally:
            conn.close()

    def upsert_split_check(
        self,
        symbol: str,
        exchange: str,
        asset_class: str,
        last_checked_at: datetime,
        last_split_date: datetime | None = None,
    ) -> None:
        conn = self._connect()
        try:
            conn.execute(
                """
                INSERT INTO split_checks (symbol, exchange, asset_class, last_checked_at, last_split_date)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(symbol, exchange, asset_class)
                DO UPDATE SET
                    last_checked_at = EXCLUDED.last_checked_at,
                    last_split_date = COALESCE(EXCLUDED.last_split_date, split_checks.last_split_date);
                """,
                [symbol, exchange, asset_class, last_checked_at, last_split_date],
            )
        finally:
            conn.close()

    def reset_slices_for_symbol(
        self,
        symbol: str,
        exchange: str,
        asset_class: str,
        frequency: str,
    ) -> None:
        conn = self._connect()
        try:
            conn.execute(
                """
                DELETE FROM backfill_slices
                WHERE symbol = ? AND exchange = ? AND asset_class = ? AND frequency = ?;
                """,
                [symbol, exchange, asset_class, frequency],
            )
        finally:
            conn.close()

    def list_backfill_slices(self, status: str | None = None, frequency: str | None = None) -> list[dict[str, Any]]:
        conn = self._connect()
        try:
            where: list[str] = []
            params: list[Any] = []
            if status is not None:
                where.append("status = ?")
                params.append(status)
            if frequency is not None:
                where.append("frequency = ?")
                params.append(frequency)

            query = """
                SELECT symbol, exchange, asset_class, frequency, year, status,
                       earliest_downloaded_ts, latest_downloaded_ts, last_success_request_at,
                       last_error, updated_at
                FROM backfill_slices
            """
            if where:
                query += " WHERE " + " AND ".join(where)
            query += " ORDER BY updated_at DESC;"

            rows = conn.execute(query, params).fetchall()
            out: list[dict[str, Any]] = []
            for row in rows:
                out.append(
                    {
                        "symbol": row[0],
                        "exchange": row[1],
                        "asset_class": row[2],
                        "frequency": row[3],
                        "year": row[4],
                        "status": row[5],
                        "earliest_downloaded_ts": row[6],
                        "latest_downloaded_ts": row[7],
                        "last_success_request_at": row[8],
                        "last_error": row[9],
                        "updated_at": row[10],
                    }
                )
            return out
        finally:
            conn.close()
