from datetime import datetime, timezone
from pathlib import Path
import json
import sys

import pandas as pd

# Allow importing worker package when running tests from repo root.
sys.path.append(str(Path(__file__).resolve().parents[2] / "apps" / "workers"))

from market_data.reporter import build_run_summary, write_json_report  # noqa: E402
from market_data.validator import validate_daily_bars, validation_metrics  # noqa: E402


def test_validate_daily_bars_detects_duplicates_and_negative_values() -> None:
    ts = datetime(2025, 1, 1, tzinfo=timezone.utc)
    df = pd.DataFrame(
        [
            {"timestamp": ts, "open": 10, "high": 11, "low": 9, "close": 10, "volume": 100},
            {"timestamp": ts, "open": 10, "high": 11, "low": 9, "close": -1, "volume": -5},
        ]
    )

    issues = validate_daily_bars(df)
    assert "duplicate_timestamps" in issues
    assert "negative_close_detected" in issues
    assert "negative_volume_detected" in issues


def test_validation_metrics_and_json_report(tmp_path: Path) -> None:
    df = pd.DataFrame(
        [
            {"timestamp": "2025-01-01T00:00:00Z", "open": 10, "high": 11, "low": 9, "close": 10, "volume": 100},
            {"timestamp": "2025-01-02T00:00:00Z", "open": 11, "high": 12, "low": 10, "close": 11, "volume": 90},
        ]
    )
    metrics = validation_metrics(df)

    assert metrics["row_count"] == 2
    assert metrics["min_timestamp"] is not None
    assert metrics["max_timestamp"] is not None

    payload = {"summary": build_run_summary("validate_history", processed=2, failed=0)}
    out = write_json_report(tmp_path / "report.json", payload)
    loaded = json.loads(out.read_text(encoding="utf-8"))
    assert loaded["summary"]["job_name"] == "validate_history"
