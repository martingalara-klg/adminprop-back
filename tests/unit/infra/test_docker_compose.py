"""Issue #2 — Docker Compose local: Postgres 16 + Redis 7 + API + workers + Beat.

Validación estática de la topología del compose y de las extensiones de
Postgres. La validación funcional real (containers arriba, /health en 200,
`\\dx` con pgcrypto + btree_gist) se corrió manualmente contra Docker real
antes de abrir el PR (ver PR — sección "Decisiones de implementación");
estos tests fijan esa topología en CI, donde levantar el compose completo
está fuera de alcance del job de tests unitarios.
"""

from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[3]
COMPOSE_PATH = REPO_ROOT / "docker" / "docker-compose.yml"
INIT_SQL_PATH = REPO_ROOT / "docker" / "postgres" / "init.sql"

# Issue #13 (fix menor autorizado, declarado en la seccion "Decisiones" del
# PR): estos 4 tests fallan cuando la suite corre DENTRO del contenedor de
# la API, porque `docker/docker-compose.yml` y `docker/postgres/init.sql`
# viven en el host y no se copian/montan dentro de la imagen -- no son
# artefactos que la app necesite en runtime, solo insumos de este test de
# topologia. Se saltean (no se borran ni se debilitan las aserciones)
# cuando los archivos no existen en el filesystem que corre pytest.
_COMPOSE_FILES_MISSING = not (COMPOSE_PATH.exists() and INIT_SQL_PATH.exists())
_skip_if_compose_files_missing = pytest.mark.skipif(
    _COMPOSE_FILES_MISSING,
    reason=(
        "docker/docker-compose.yml o docker/postgres/init.sql no estan "
        "presentes en este filesystem (suite corriendo dentro del "
        "contenedor de la API, sin el contexto de build montado)."
    ),
)


def _load_compose() -> dict:
    return yaml.safe_load(COMPOSE_PATH.read_text(encoding="utf-8"))


@_skip_if_compose_files_missing
def test_ca_2_01_compose_declara_postgres_redis_y_api_sin_profile():
    """CA #2-01: `docker compose up` (sin flags) levanta Postgres, Redis y API.

    Estos tres servicios no tienen `profiles`, por lo que `docker compose up`
    los levanta por default.
    """
    services = _load_compose()["services"]

    for name in ("postgres", "redis", "api"):
        assert name in services, f"falta el servicio {name}"
        assert "profiles" not in services[name], f"{name} no debe requerir --profile"


@_skip_if_compose_files_missing
def test_ca_2_01_workers_y_beat_declarados_bajo_profile_workers():
    """CA #2-01 (decisión de implementación): notification_worker,
    documents_worker y beat existen en el compose (issue #4 los activa en
    código), pero quedan detrás de `profiles: [workers]` para que
    `docker compose up` no falle mientras Celery no exista.
    """
    services = _load_compose()["services"]

    for name in ("notification_worker", "documents_worker", "beat"):
        assert name in services, f"falta el servicio {name}"
        assert services[name].get("profiles") == ["workers"], (
            f"{name} debe estar bajo el profile 'workers' hasta el issue #4"
        )


@_skip_if_compose_files_missing
def test_ca_2_01_api_depende_de_postgres_y_redis_healthy():
    """CA #2-01: la API espera a que Postgres y Redis estén healthy antes de
    arrancar, para que `docker compose up` sea reproducible sin condiciones
    de carrera.
    """
    api = _load_compose()["services"]["api"]

    assert api["depends_on"]["postgres"]["condition"] == "service_healthy"
    assert api["depends_on"]["redis"]["condition"] == "service_healthy"


@_skip_if_compose_files_missing
def test_ca_2_02_init_sql_habilita_pgcrypto_y_btree_gist():
    """CA #2-02: las extensiones `pgcrypto` y `btree_gist` quedan disponibles
    en el Postgres del compose vía script de init montado en
    /docker-entrypoint-initdb.d.
    """
    sql = INIT_SQL_PATH.read_text(encoding="utf-8")

    assert "CREATE EXTENSION IF NOT EXISTS pgcrypto" in sql
    assert "CREATE EXTENSION IF NOT EXISTS btree_gist" in sql

    postgres_volumes = _load_compose()["services"]["postgres"]["volumes"]
    assert any(
        "init.sql" in volume and "docker-entrypoint-initdb.d" in volume
        for volume in postgres_volumes
    )
