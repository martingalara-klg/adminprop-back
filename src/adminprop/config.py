from functools import lru_cache
from typing import Annotated

from pydantic import field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_name: str = "AdminProp API"
    environment: str = "local"  # local | staging | production
    log_level: str = "INFO"
    service_name: str = "adminprop-api"

    # issue #42 -- runtime (pool de la API + workers Celery) conecta como
    # `adminprop_app` (NOSUPERUSER, NOBYPASSRLS, sujeto a RLS FORCE): sin
    # esto el superusuario de Postgres bypasseaba RLS y el aislamiento
    # fisico multi-tenant (sdd_04 §2.3, RN-D01) nunca estaba efectivamente
    # activo, solo la defensa app-level (filtro organization_id explicito).
    database_url: str = (
        "postgresql://adminprop_app:adminprop_app_local_only@localhost:5432/adminprop"
    )
    # Conexion de superusuario, EXCLUSIVA para Alembic (`db/migrations/env.py`).
    # Las migraciones necesitan DDL (CREATE TABLE/ROLE, ALTER DEFAULT
    # PRIVILEGES) que `adminprop_app` no tiene ni debe tener -- separarla de
    # `database_url` es lo que permite que el runtime conecte con el rol
    # RLS sin dejar de poder migrar el schema.
    migrations_database_url: str = "postgresql://adminprop:adminprop@localhost:5432/adminprop"
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

    # ─── issue #7 — Superadmin: organizaciones + invitacion de owner ───────
    # spec_module_00_superadmin.md "Flujo de Activacion de Cuenta": el link
    # de invitacion apunta al frontend (`/accept-invitation?token=...`), no
    # a este backend. Sin infra cloud todavia (CLAUDE.md §10), el default
    # local apunta al puerto de Vite (`adminprop-front`, RUNBOOK-LOCAL-001).
    frontend_base_url: str = "http://localhost:5173"
    # RF-03: expiracion de la invitacion de owner, 72 horas.
    invitation_ttl_hours: int = 72

    # ─── issue #8 — Activacion de cuenta + forgot/reset password ──────────
    # sdd_03 §1 no fija un TTL explicito para el token de reset (a
    # diferencia de la invitacion, 72h); se adopta 1h -- ventana corta
    # acorde a un secreto de un solo uso enviado por email bajo demanda
    # (decision documentada en el PR del issue #8).
    password_reset_token_ttl_seconds: int = 60 * 60
    # Ventana de retencion fisica en Redis, mayor a la ventana logica de
    # arriba: permite que GET /auth/reset-password/:token distinga "el
    # token nunca existio / ya fue consumido" (404) de "existio pero
    # vencio" (410, RESET_TOKEN_EXPIRED) incluso despues de pasada la hora
    # de validez -- si Redis borrara la key exactamente al vencer, ambos
    # casos lucirian identicos.
    password_reset_token_grace_seconds: int = 24 * 60 * 60

    # ─── issue #12 — Cifrado columnar pgcrypto (landlords.bank_info) ──────
    # sdd_04 §2.4: AES-256 columnar via pgcrypto (shared/encryption/pgcrypto.py).
    # Default de desarrollo local no sensible (mismo criterio que
    # app_role_password/resend_api_key de arriba); migra a un gestor de
    # secretos cuando exista infra cloud (CLAUDE.md §3/§8).
    encryption_key: str = "local_dev_encryption_key_change_me"

    # ─── issue #90 — CORS (dominios reales de front + API) ─────────────────
    # sdd_04 §2.4a: deshabilitado por default (lista vacia) -- mientras
    # front y API compartan origen local (proxy de Vite en dev), no hace
    # falta declarar origenes; CORS_ALLOWED_ORIGINS sin definir mantiene
    # el comportamiento actual intacto (CORSMiddleware no se registra en
    # main.py). Con origenes configurados, main.py registra el
    # middleware con allow_credentials=True y esta lista EXACTA (nunca
    # "*", incompatible con credenciales). CORS solo no habilita
    # dominios cruzados: las cookies son SameSite=Lax (decision #43) y
    # no viajan cross-site aunque CORS lo permita -- el despliegue
    # recomendado sigue siendo mismo origen (proxy en el server del
    # front); esta variable queda para origenes adicionales explicitos.
    # NoDecode: sin esto, pydantic-settings intenta json.loads() el valor
    # de la env var para cualquier campo list[...] antes de que el
    # field_validator de abajo lo vea, y "a,b" no es JSON valido ->
    # SettingsError. NoDecode entrega el string crudo al validator.
    cors_allowed_origins: Annotated[list[str], NoDecode] = []

    @field_validator("cors_allowed_origins", mode="before")
    @classmethod
    def _split_cors_allowed_origins(cls, value: object) -> object:
        if isinstance(value, str):
            return [origin.strip() for origin in value.split(",") if origin.strip()]
        return value

    # ─── issue #26 — Mantenimiento: storage local de adjuntos ──────────────
    # docs/skills/tenant-isolation.md §"Storage de archivos con aislamiento
    # per-tenant" + spec_data_model.md §Capa 5 "attachments": filesystem
    # local (volumen Docker en MVP, docker/docker-compose.yml) via
    # shared/storage/local.py. Default coincide con el volumen montado en
    # el contenedor api; overridable para tests (tmp_path).
    attachments_dir: str = "/data/adminprop-storage"


@lru_cache
def get_settings() -> Settings:
    return Settings()
