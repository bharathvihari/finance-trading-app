"""
Cold archival job: move bars older than the hot-window boundary from Postgres
into the Parquet cold tier, then delete them from Postgres.

Safety guarantee
----------------
The DELETE only runs after the Parquet write returns successfully.  If the
process is killed between write and delete, the next run will:
  1. Re-read the same rows from Postgres (still present).
  2. Re-write them to a new Parquet part file (duplicate rows across files are
     harmless — BarReader deduplicates on timestamp when reading).
  3. Delete successfully.

Run this job periodically (weekly cron is sufficient) to keep the hot tier
bounded to the configured hot_window_months.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
from pathlib import Path

from market_data.config import load_job_config
from market_data.duckdb_meta import DuckDbMetaStore
from market_data.logger import StructuredLogger
from market_data.parquet_store import ParquetStore
from market_data.postgres_store import PostgresBarStore


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _hot_cutoff(hot_window_months: int) -> datetime:
    """Return the UTC month-boundary that separates hot from cold data.

    Mirrors _hot_window_cutoff() in backfill.py exactly so both jobs agree
    on where the tier boundary sits.
    """
    now = datetime.now(timezone.utc)
    month = now.month - hot_window_months
    year = now.year
    while month <= 0:
        month += 12
        year -= 1
    return datetime(year, month, 1, tzinfo=timezone.utc)


def run_archive(dry_run: bool | None = None) -> None:
    root = _repo_root()
    cfg = load_job_config(root / "config" / "market_data.archive.yaml")

    postgres_cfg = cfg.postgres
    if not postgres_cfg.enabled:
        print("Postgres hot store is disabled in config — nothing to archive.")
        return

    hot_window_months = int(postgres_cfg.hot_window_months)
    cutoff_utc = _hot_cutoff(hot_window_months)

    meta = DuckDbMetaStore(root / cfg.storage.duckdb_path)
    parquet_store = ParquetStore(root / cfg.storage.parquet_root)
    hot_store = PostgresBarStore.from_config(postgres_cfg)

    is_dry_run = getattr(cfg, "dry_run", False) if dry_run is None else dry_run

    run_id = "dry-run"
    if not is_dry_run:
        meta.init_schema()
        run_id = meta.start_job_run(job_name=cfg.job_name, mode=cfg.mode)

    logger = StructuredLogger(job_name=cfg.job_name, log_dir=root / "data" / "logs")
    logger.set_run_id(run_id)

    try:
        print(f"[{datetime.now(timezone.utc).isoformat()}] archive job started (run_id={run_id})")
        print(f"hot cutoff: {cutoff_utc.date().isoformat()} (hot_window_months={hot_window_months})")
        if is_dry_run:
            print("mode: DRY RUN (no parquet writes or deletes)")

        logger.log(
            "job_start",
            dry_run=is_dry_run,
            cutoff_utc=cutoff_utc.isoformat(),
            hot_window_months=hot_window_months,
        )

        # Discover which (asset_class, exchange, frequency, year) partitions
        # have bars older than the cutoff still sitting in Postgres.
        partitions = hot_store.fetch_cold_partition_keys(cutoff_utc)

        if not partitions:
            print("No cold bars found in Postgres — nothing to archive.")
            logger.log("job_end", status="complete", partitions_archived=0, rows_archived=0)
            if not is_dry_run:
                meta.finish_job_run(run_id=run_id, status="COMPLETE")
            return

        print(f"Found {len(partitions)} partition(s) to archive.")

        total_rows = 0
        total_partitions = 0
        failed_partitions = 0

        for part in partitions:
            asset_class = part["asset_class"]
            exchange = part["exchange"]
            frequency = part["frequency"]
            year = part["year"]
            expected_rows = part["row_count"]

            logger.log(
                "partition_start",
                asset_class=asset_class,
                exchange=exchange,
                frequency=frequency,
                year=year,
                expected_rows=expected_rows,
            )

            try:
                df = hot_store.read_bars_for_partition(
                    asset_class=asset_class,
                    exchange=exchange,
                    frequency=frequency,
                    year=year,
                    cutoff_utc=cutoff_utc,
                )

                if df.empty:
                    logger.log(
                        "partition_skip",
                        asset_class=asset_class, exchange=exchange,
                        frequency=frequency, year=year, reason="empty_read",
                    )
                    continue

                actual_rows = len(df)

                if not is_dry_run:
                    # --- Write to Parquet (must succeed before DELETE) ---
                    written_files = parquet_store.write_partition(df)

                    # --- Delete from Postgres ONLY after successful write ---
                    deleted = hot_store.delete_bars_for_partition(
                        asset_class=asset_class,
                        exchange=exchange,
                        frequency=frequency,
                        year=year,
                        cutoff_utc=cutoff_utc,
                    )
                else:
                    written_files = []
                    deleted = 0

                total_rows += actual_rows
                total_partitions += 1

                logger.log(
                    "partition_archived",
                    asset_class=asset_class,
                    exchange=exchange,
                    frequency=frequency,
                    year=year,
                    rows_read=actual_rows,
                    rows_deleted=deleted,
                    parquet_files_written=len(written_files),
                )
                print(
                    f"  archived {asset_class}/{exchange}/{frequency}/year={year}: "
                    f"{actual_rows} rows → {len(written_files)} file(s), deleted={deleted}"
                )

            except Exception as exc:
                failed_partitions += 1
                logger.log(
                    "partition_error",
                    asset_class=asset_class, exchange=exchange,
                    frequency=frequency, year=year, error=str(exc),
                )
                print(
                    f"  ERROR archiving {asset_class}/{exchange}/{frequency}/year={year}: {exc}"
                )
                if not is_dry_run:
                    meta.append_job_error(
                        run_id=run_id,
                        scope="partition",
                        exchange=exchange,
                        error_message=str(exc),
                    )
                # Continue — do not abort the whole job on one bad partition.

        final_status = "COMPLETE" if failed_partitions == 0 else "PARTIAL"
        logger.log(
            "job_end",
            status=final_status.lower(),
            partitions_archived=total_partitions,
            rows_archived=total_rows,
            failed_partitions=failed_partitions,
        )
        print(
            f"[{datetime.now(timezone.utc).isoformat()}] archive job {final_status.lower()} — "
            f"{total_partitions} partition(s), {total_rows} row(s) archived, "
            f"{failed_partitions} failure(s)"
        )

        if not is_dry_run:
            meta.finish_job_run(
                run_id=run_id,
                status=final_status,
                processed_count=total_rows,
                failed_count=failed_partitions,
            )

    except Exception as exc:
        logger.log("job_end", status="failed", error=str(exc))
        if not is_dry_run:
            meta.append_job_error(run_id=run_id, scope="job", error_message=str(exc))
            meta.finish_job_run(run_id=run_id, status="FAILED", processed_count=0, failed_count=1)
        raise
    finally:
        logger.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Archive cold bars from Postgres to Parquet.")
    parser.add_argument("--dry-run", action="store_true", help="Run without writing or deleting.")
    args = parser.parse_args()
    run_archive(dry_run=args.dry_run)
