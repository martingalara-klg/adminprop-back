from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_name: str = "AdminProp API"
    environment: str = "local"  # local | staging | production
    log_level: str = "INFO"
    service_name: str = "adminprop-api"

    database_url: str = "postgresql://adminprop:adminprop@localhost:5432/adminprop"
    redis_url: str = "redis://localhost:6379/0"

    # timeout de los checks de /health (sdd_04 §4.7)
    health_check_timeout_seconds: float = 1.0


@lru_cache
def get_settings() -> Settings:
    return Settings()
