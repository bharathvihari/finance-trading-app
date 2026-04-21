from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_name: str = "finance-trading-app-api"
    api_host: str = "0.0.0.0"
    api_port: int = 8000
    redis_host: str = "localhost"
    redis_port: int = 6379

    # --- Parquet / DuckDB (cold tier) ---
    parquet_root: str = "data/parquet/price-data"
    duckdb_path: str = "data/duckdb/market_data.duckdb"

    # --- Postgres (hot tier) ---
    postgres_enabled: bool = False
    postgres_host: str = "127.0.0.1"
    postgres_port: int = 5432
    postgres_db: str = "trading_app"
    postgres_user: str = "trading_user"
    postgres_password: str = "trading_pass"
    postgres_schema: str = "market_data"
    postgres_bars_table: str = "daily_bars"

    # Must match the value used in the backfill job so the tier boundary aligns.
    hot_window_months: int = 6

    # --- Auth / JWT ---
    jwt_secret_key: str = "change-me-in-production"
    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = 43_200   # 30 days — long-lived for a personal app

    # --- Cache (Redis) ---
    cache_enabled: bool = True


settings = Settings()
