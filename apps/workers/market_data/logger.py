"""Structured JSON logger for market data pipeline jobs."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path


class StructuredLogger:
    """Writes one JSON object per line to data/logs/{job_name}/{YYYY-MM-DD}.jsonl.

    Flushes after every write so logs are visible in real-time via tail -f.
    """

    def __init__(self, job_name: str, log_dir: Path) -> None:
        self.job_name = job_name
        self.log_dir = Path(log_dir) / job_name
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.run_id: str = ""
        self._file = None
        self._current_date: str | None = None

    def set_run_id(self, run_id: str) -> None:
        self.run_id = run_id

    def _get_file(self):
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        if today != self._current_date:
            if self._file:
                self._file.close()
            path = self.log_dir / f"{today}.jsonl"
            self._file = open(path, "a", encoding="utf-8")
            self._current_date = today
        return self._file

    def log(self, event: str, **fields) -> None:
        entry = {
            "ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z",
            "job": self.job_name,
            "run_id": self.run_id,
            "event": event,
        }
        for k, v in fields.items():
            if v is not None:
                entry[k] = v

        f = self._get_file()
        f.write(json.dumps(entry) + "\n")
        f.flush()

    def close(self) -> None:
        if self._file:
            self._file.close()
            self._file = None

    def __enter__(self) -> "StructuredLogger":
        return self

    def __exit__(self, *_) -> None:
        self.close()
