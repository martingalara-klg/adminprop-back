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

    # Passwords de los roles PostgreSQL RLS (issue #3, sdd_04 §2.3).
    # Defaults de desarrollo local no sensibles — paridad con
    # POSTGRES_PASSWORD hardcodeado en docker/docker-compose.yml.
    app_role_password: str = "adminprop_app_local_only"
    superadmin_role_password: str = "adminprop_superadmin_local_only"

    # timeout de los checks de /health (sdd_04 §4.7)
    health_check_timeout_seconds: float = 1.0

    # issue #4 — Celery/Redis: mismo Redis para broker y result backend
    # (docs/skills/async-worker.md). Decision de implementacion: el volumen
    # de jobs del MVP (sdd_04 §1.2) no justifica una segunda variable de
    # entorno/instancia separada; separar si el volumen crece post-MVP.
    #
    # Resend (docs/skills/external-integrations.md, spec_notificaciones.md
    # §Email): API key en variable de entorno local (.env, no commiteado);
    # el placeholder no es un secreto real, replica el patron de
    # app_role_password/superadmin_role_password de arriba. El dominio del
    # sender es "provisorio hasta definir infra" (spec_notificaciones.md).
    resend_api_key: str = "re_local_dev_placeholder"
    resend_from_domain: str = "adminprop.local"


@lru_cache
def get_settings() -> Settings:
    return Settings()
