from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_name: str = "finance-trading-app-api"
    api_host: str = "0.0.0.0"
    api_port: int = 8000
    redis_host: str = "localhost"
    redis_port: int = 6379


settings = Settings()
