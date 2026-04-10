from __future__ import annotations

import argparse
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from market_data.config import load_job_config
from market_data.reporter import (
    build_run_summary,
    build_symbol_report,
    write_csv_report,
    write_json_report,
)
from market_data.universe_loader import load_universe
from market_data.validator import validate_daily_bars, validation_metrics


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _load_symbol_bars(
    parquet_root: Path,
    asset_class: str,
    exchange: str,
    frequency: str,
    symbol: str,
) -> pd.DataFrame:
    base = (
        parquet_root
        / f"asset_class={asset_class}"
        / f"exchange={exchange}"
        / f"frequency={frequency}"
    )
    files = sorted(base.glob("year=*/part-*.parquet"))
    if not files:
        return pd.DataFrame()

    frames = []
    for file in files:
        frame = pd.read_parquet(file)
        if "symbol" in frame.columns:
            frame = frame[frame["symbol"] == symbol]
        if not frame.empty:
            frames.append(frame)

    if not frames:
        return pd.DataFrame()

    out = pd.concat(frames, ignore_index=True)
    if "timestamp" in out.columns:
        out["timestamp"] = pd.to_datetime(out["timestamp"], utc=True, errors="coerce")
        out = out.sort_values("timestamp")
    return out


def run_validate_history(dry_run: bool | None = None) -> dict:
    root = _repo_root()
    cfg = load_job_config(root / "config" / "market_data.backfill.yaml")
    is_dry_run = getattr(cfg, "dry_run", False) if dry_run is None else dry_run
    universe = load_universe(cfg)

    parquet_root = root / cfg.storage.parquet_root
    report_dir = root / "data" / "reports"
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")

    symbol_reports: list[dict] = []
    csv_rows: list[dict] = []
    failed = 0
    issue_count = 0

    for instrument in universe.instruments:
        df = _load_symbol_bars(
            parquet_root=parquet_root,
            asset_class=instrument.asset_class,
            exchange=instrument.exchange,
            frequency=cfg.frequency.name,
            symbol=instrument.symbol,
        )
        issues = validate_daily_bars(df)
        metrics = validation_metrics(df)

        issue_count += len(issues)
        if issues:
            failed += 1

        symbol_reports.append(
            build_symbol_report(
                symbol=instrument.symbol,
                exchange=instrument.exchange,
                asset_class=instrument.asset_class,
                frequency=cfg.frequency.name,
                issues=issues,
                metrics=metrics,
            )
        )
        csv_rows.append(
            {
                "symbol": instrument.symbol,
                "exchange": instrument.exchange,
                "asset_class": instrument.asset_class,
                "frequency": cfg.frequency.name,
                "issue_count": len(issues),
                "issues": ";".join(issues),
                "row_count": metrics["row_count"],
                "min_timestamp": metrics["min_timestamp"],
                "max_timestamp": metrics["max_timestamp"],
            }
        )

    summary = build_run_summary(
        job_name="validate_history",
        processed=len(universe.instruments),
        failed=failed,
        issue_count=issue_count,
    )
    payload = {"summary": summary, "symbols": symbol_reports}

    if is_dry_run:
        print(f"[{datetime.now(timezone.utc).isoformat()}] validation complete (DRY RUN)")
        print(f"summary: {summary}")
        return payload

    json_path = write_json_report(report_dir / f"validation-{stamp}.json", payload)
    csv_path = write_csv_report(report_dir / f"validation-{stamp}.csv", csv_rows)

    print(f"[{datetime.now(timezone.utc).isoformat()}] validation complete")
    print(f"json report: {json_path}")
    print(f"csv report:  {csv_path}")
    print(f"summary: {summary}")
    return payload


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Validate historical parquet datasets.")
    parser.add_argument("--dry-run", action="store_true", help="Run validations without writing report files.")
    args = parser.parse_args()
    run_validate_history(dry_run=args.dry_run)
