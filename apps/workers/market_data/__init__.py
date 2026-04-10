"""Market data pipeline package.

Step 1 scaffold:
- Config loading
- Domain models
- IBKR/Nautilus client abstraction (placeholder)
- DuckDB metadata helpers (placeholder)
- Parquet storage helpers (placeholder)
"""

from .config import JobConfig, load_job_config
from .ibkr_client import HistoricalRequest, IbkrHistoricalClient
from .windowing import TimeWindow, paginated_windows_backward, yearly_windows_newest_to_oldest
from .universe_loader import load_universe

try:
    from .parquet_store import ParquetStore
except Exception:  # pragma: no cover - allows lightweight imports without optional deps
    ParquetStore = None  # type: ignore[assignment]

__all__ = [
    "JobConfig",
    "HistoricalRequest",
    "IbkrHistoricalClient",
    "ParquetStore",
    "TimeWindow",
    "load_job_config",
    "load_universe",
    "paginated_windows_backward",
    "yearly_windows_newest_to_oldest",
]
