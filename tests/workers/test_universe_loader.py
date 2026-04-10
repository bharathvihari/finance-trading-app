from pathlib import Path
import sys

# Allow importing worker package when running tests from repo root.
sys.path.append(str(Path(__file__).resolve().parents[2] / "apps" / "workers"))

from market_data.config import JobConfig  # noqa: E402
from market_data.universe_loader import load_universe  # noqa: E402


def _job_cfg(payload: dict) -> JobConfig:
    base = {
        "job_name": "test",
        "mode": "backfill",
        "universe": {"exchanges": {}},
    }
    base.update(payload)
    return JobConfig(**base)


def test_priority_first_and_ordering() -> None:
    cfg = _job_cfg(
        {
            "universe": {
                "exchanges": {
                    "NASDAQ": {
                        "priority_symbols": ["aapl"],
                        "priority_indices": ["ndx"],
                        "symbols": ["msft"],
                        "indices": ["spx"],
                    }
                }
            }
        }
    )

    universe = load_universe(cfg)
    rows = [(i.symbol, i.asset_class, i.priority) for i in universe.instruments]

    assert rows == [
        ("AAPL", "equity", True),
        ("NDX", "index", True),
        ("MSFT", "equity", False),
        ("SPX", "index", False),
    ]


def test_dedup_and_priority_promotion() -> None:
    cfg = _job_cfg(
        {
            "universe": {
                "exchanges": {
                    "NYSE": {
                        "symbols": ["jpm", "  ", "JPM"],
                        "priority_symbols": ["jpm"],
                    }
                }
            }
        }
    )

    universe = load_universe(cfg)
    rows = [(i.symbol, i.exchange, i.asset_class, i.priority) for i in universe.instruments]

    assert rows == [("JPM", "NYSE", "equity", True)]
