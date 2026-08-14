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

    # ─── issue #6 — Auth: JWT RS256, cookies, lockout, rate limit ──────────
    # sdd_04 §2.2 — JWT asimetrico; claves en filesystem local (gestor de
    # secretos post-infra-cloud, CLAUDE.md §3/§8). RUNBOOK-LOCAL-001 §2.3
    # ya documenta `openssl genrsa` generando estos paths por default.
    jwt_private_key_path: str = "keys/private.pem"
    jwt_public_key_path: str = "keys/public.pem"
    jwt_algorithm: str = "RS256"
    # sdd_03 §1 / sdd_04 §2.2: access 8h, refresh 30 dias rotativo single-use.
    jwt_access_token_ttl_seconds: int = 8 * 60 * 60
    jwt_refresh_token_ttl_days: int = 30

    # Cookies HttpOnly+Secure+SameSite=Lax (sdd_03 §"Convenciones Generales",
    # sdd_04 §2.2/§2.4). `cookie_secure` es override solo para que la propia
    # suite de tests pueda ejercitar el flujo completo sin TLS real cuando
    # el ASGI transport no negocia https (ver tests/integration/auth/conftest.py);
    # nunca se desactiva por ambiente en runtime (siempre True fuera de tests).
    cookie_secure: bool = True
    cookie_domain: str | None = None

    # sdd_04 §2.2 "Fuerza bruta de login": 5 intentos / 10 min -> lock 30 min.
    login_lockout_max_attempts: int = 5
    login_lockout_window_seconds: int = 10 * 60
    login_lockout_duration_seconds: int = 30 * 60

    # sdd_04 §2.5 — Redis token bucket (ventana fija, ver
    # docs/skills/api-endpoint.md "Rate limiting").
    login_rate_limit_max: int = 10
    login_rate_limit_window_seconds: int = 10 * 60
    forgot_password_rate_limit_max: int = 5
    forgot_password_rate_limit_window_seconds: int = 60 * 60
    refresh_rate_limit_max: int = 60
    refresh_rate_limit_window_seconds: int = 60 * 60


@lru_cache
def get_settings() -> Settings:
    return Settings()
