import os
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field


class RateLimitConfig(BaseModel):
    max_requests_per_window: int = Field(default=60)
    window_seconds: int = Field(default=600)
    identical_request_cooldown_seconds: int = Field(default=15)
    utilization_target_pct: int = Field(default=65)
    base_delay_seconds: float = Field(default=0.8)
    jitter_seconds: float = Field(default=0.6)
    max_retries: int = Field(default=3)
    backoff_base_seconds: float = Field(default=1.0)
    max_backoff_seconds: float = Field(default=30.0)
    backoff_jitter_seconds: float = Field(default=0.5)


class FrequencyConfig(BaseModel):
    name: Literal["daily"] = "daily"
    ibkr_bar_size: str = "1 day"


class StorageConfig(BaseModel):
    parquet_root: str = "data/parquet/price-data"
    duckdb_path: str = "data/duckdb/market_data.duckdb"


class PostgresConfig(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    enabled: bool = False
    host: str = Field(default_factory=lambda: os.getenv("POSTGRES_HOST", "127.0.0.1"))
    port: int = Field(default_factory=lambda: int(os.getenv("POSTGRES_PORT", "5432")))
    database: str = Field(default_factory=lambda: os.getenv("POSTGRES_DB", "trading_app"))
    user: str = Field(default_factory=lambda: os.getenv("POSTGRES_USER", "trading_user"))
    password: str = Field(default_factory=lambda: os.getenv("POSTGRES_PASSWORD", "trading_pass"))
    schema_name: str = Field(default="market_data", alias="schema")
    bars_table: str = "daily_bars"
    hot_window_months: int = Field(default=6, ge=1, le=24)


class IbkrConfig(BaseModel):
    host: str = "127.0.0.1"
    port: int = 7497
    client_id: int = 1001
    account: str | None = None
    gateway_mode: Literal["paper", "live"] = "paper"
    connect_timeout_seconds: float = 10.0
    fallback_enabled: bool = True
    what_to_show: Literal["TRADES", "ADJUSTED_LAST"] = "TRADES"
    use_regular_trading_hours: bool = True
    timezone: str = "UTC"


class ExchangeConfig(BaseModel):
    reference_symbol: str | None = None
    symbols: list[str] = Field(default_factory=list)
    priority_symbols: list[str] = Field(default_factory=list)
    indices: list[str] = Field(default_factory=list)
    priority_indices: list[str] = Field(default_factory=list)


class UniverseConfig(BaseModel):
    exchanges: dict[str, ExchangeConfig] = Field(default_factory=dict)


class JobConfig(BaseModel):
    job_name: str
    mode: Literal["backfill", "daily", "archive"]
    dry_run: bool = False
    fail_on_unresolved_exchange_last_traded: bool = False
    frequency: FrequencyConfig = Field(default_factory=FrequencyConfig)
    storage: StorageConfig = Field(default_factory=StorageConfig)
    postgres: PostgresConfig = Field(default_factory=PostgresConfig)
    ibkr: IbkrConfig = Field(default_factory=IbkrConfig)
    rate_limits: RateLimitConfig = Field(default_factory=RateLimitConfig)
    universe: UniverseConfig = Field(default_factory=UniverseConfig)


def load_job_config(path: str | Path) -> JobConfig:
    config_path = Path(path)
    with config_path.open("r", encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}
    return JobConfig.model_validate(raw)
