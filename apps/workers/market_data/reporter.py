import csv
import json
from datetime import datetime, timezone
from pathlib import Path


def build_run_summary(job_name: str, processed: int, failed: int, issue_count: int = 0) -> dict:
    return {
        "job_name": job_name,
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "processed": processed,
        "failed": failed,
        "issue_count": issue_count,
    }


def build_symbol_report(
    symbol: str,
    exchange: str,
    asset_class: str,
    frequency: str,
    issues: list[str],
    metrics: dict,
) -> dict:
    return {
        "symbol": symbol,
        "exchange": exchange,
        "asset_class": asset_class,
        "frequency": frequency,
        "issues": issues,
        "metrics": metrics,
    }


def write_json_report(path: str | Path, payload: dict) -> Path:
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return out


def write_csv_report(path: str | Path, rows: list[dict]) -> Path:
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        out.write_text("", encoding="utf-8")
        return out

    fieldnames = list(rows[0].keys())
    with out.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)
    return out
