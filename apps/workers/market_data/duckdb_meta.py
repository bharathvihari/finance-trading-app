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
                    earliest_downloaded_ts = COALESCE(EXCLUDED.earliest_downloaded_ts, backfill_slices.earliest_downloaded_ts),
                    latest_downloaded_ts = COALESCE(EXCLUDED.latest_downloaded_ts, backfill_slices.latest_downloaded_ts),
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
