from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str
    redis_url: str = "redis://redis:6379/0"
    secret_key: str
    algorithm: str = "HS256"
    access_token_expire_minutes: int = 30
    openai_api_key: str = ""
    data_provider: str = "yfinance"
    cache_ttl_current_price: int = 30
    cache_ttl_news: int = 900
    cache_ttl_fundamentals: int = 86400
    cache_ttl_historical: int = 3600
    app_env: str = "development"
    datadog_api_key: str = ""


@lru_cache
def get_settings() -> Settings:
    return Settings()