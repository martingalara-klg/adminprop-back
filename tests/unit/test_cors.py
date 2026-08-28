"""Issue #90 — infra: middleware CORS condicional (sdd_04 §2.4a).

Default (`CORS_ALLOWED_ORIGINS` sin definir): `CORSMiddleware` no se
registra en `create_app()` y el comportamiento actual (sin headers CORS)
queda intacto -- dev local sigue dependiendo del proxy de Vite. Con
origenes configurados: preflight OPTIONS correcto, `Access-Control-
Allow-Origin` con el origen EXACTO + `Access-Control-Allow-Credentials:
true` para un origen permitido, y ausencia de esos headers para un
origen no listado (CORSMiddleware no bloquea la respuesta, solo omite
los headers de permiso -- es el navegador el que la descarta).
"""

from collections.abc import Generator

import pytest
from fastapi.testclient import TestClient

from adminprop.config import Settings, get_settings
from adminprop.main import create_app

ALLOWED_ORIGIN = "https://app.adminprop.example.com"
OTHER_ALLOWED_ORIGIN = "https://admin.adminprop.example.com"
DISALLOWED_ORIGIN = "https://evil.example.com"


@pytest.fixture()
def cors_disabled_client() -> Generator[TestClient]:
    """`CORS_ALLOWED_ORIGINS` sin definir -- default del proyecto."""
    get_settings.cache_clear()
    client = TestClient(create_app())
    yield client
    get_settings.cache_clear()


@pytest.fixture()
def cors_enabled_client(monkeypatch: pytest.MonkeyPatch) -> Generator[TestClient]:
    monkeypatch.setenv("CORS_ALLOWED_ORIGINS", f"{ALLOWED_ORIGIN},{OTHER_ALLOWED_ORIGIN}")
    get_settings.cache_clear()
    client = TestClient(create_app())
    yield client
    get_settings.cache_clear()


def test_cors_allowed_origins_default_es_lista_vacia():
    """Sin CORS_ALLOWED_ORIGINS, Settings.cors_allowed_origins es [] (no None)."""
    settings = Settings(_env_file=None)

    assert settings.cors_allowed_origins == []


def test_cors_allowed_origins_parsea_lista_separada_por_comas(monkeypatch: pytest.MonkeyPatch):
    """La env var separada por comas se parsea a list[str], sin espacios sobrantes."""
    monkeypatch.setenv("CORS_ALLOWED_ORIGINS", f" {ALLOWED_ORIGIN} , {OTHER_ALLOWED_ORIGIN} ")

    settings = Settings(_env_file=None)

    assert settings.cors_allowed_origins == [ALLOWED_ORIGIN, OTHER_ALLOWED_ORIGIN]


def test_cors_deshabilitado_por_default_sin_env_var(cors_disabled_client: TestClient):
    """CORS_ALLOWED_ORIGINS sin definir -> sin headers CORS (comportamiento actual)."""
    response = cors_disabled_client.get("/health", headers={"Origin": ALLOWED_ORIGIN})

    assert "access-control-allow-origin" not in response.headers
    assert "access-control-allow-credentials" not in response.headers


def test_cors_deshabilitado_preflight_sin_headers_de_permiso(cors_disabled_client: TestClient):
    """Sin origenes configurados, un preflight OPTIONS tampoco recibe headers CORS."""
    response = cors_disabled_client.options(
        "/health",
        headers={
            "Origin": ALLOWED_ORIGIN,
            "Access-Control-Request-Method": "GET",
        },
    )

    assert "access-control-allow-origin" not in response.headers


def test_cors_preflight_options_origen_permitido(cors_enabled_client: TestClient):
    """Con origenes configurados, el preflight OPTIONS responde con el origen exacto."""
    response = cors_enabled_client.options(
        "/health",
        headers={
            "Origin": ALLOWED_ORIGIN,
            "Access-Control-Request-Method": "GET",
        },
    )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == ALLOWED_ORIGIN
    assert response.headers["access-control-allow-credentials"] == "true"


def test_cors_request_real_origen_permitido_incluye_credenciales(cors_enabled_client: TestClient):
    """Un GET real desde un origen listado recibe el origen exacto + credentials=true."""
    response = cors_enabled_client.get("/health", headers={"Origin": ALLOWED_ORIGIN})

    assert response.headers["access-control-allow-origin"] == ALLOWED_ORIGIN
    assert response.headers["access-control-allow-credentials"] == "true"
    # RN sdd_04 §2.4a: nunca "*" -- el origen devuelto es literal, no un wildcard.
    assert response.headers["access-control-allow-origin"] != "*"


def test_cors_request_real_segundo_origen_configurado_tambien_permitido(
    cors_enabled_client: TestClient,
):
    """La lista admite mas de un origen exacto (separados por coma en la env var)."""
    response = cors_enabled_client.get("/health", headers={"Origin": OTHER_ALLOWED_ORIGIN})

    assert response.headers["access-control-allow-origin"] == OTHER_ALLOWED_ORIGIN


def test_cors_origen_no_listado_sin_headers_de_permiso(cors_enabled_client: TestClient):
    """Un origen fuera de la lista no recibe Access-Control-Allow-Origin.

    `Access-Control-Allow-Origin` es el unico header que el navegador
    enforza para decidir si la respuesta es legible por el JS que la
    pidio (CORSMiddleware de Starlette manda Allow-Credentials/Expose-
    Headers en toda respuesta "simple" independientemente del origen --
    sin Allow-Origin el navegador descarta la respuesta igual).
    """
    response = cors_enabled_client.get("/health", headers={"Origin": DISALLOWED_ORIGIN})

    assert "access-control-allow-origin" not in response.headers


def test_cors_expone_headers_de_descarga_de_blobs(cors_enabled_client: TestClient):
    """Content-Disposition expuesto para que el front lea el filename de descargas.

    `Access-Control-Expose-Headers` solo viaja en la respuesta real (no
    en el preflight OPTIONS, que Starlette resuelve con un set de
    headers distinto -- ver CORSMiddleware.preflight_headers).
    """
    response = cors_enabled_client.get("/health", headers={"Origin": ALLOWED_ORIGIN})

    exposed = response.headers.get("access-control-expose-headers", "")
    assert "Content-Disposition" in exposed
    assert "X-Request-Id" in exposed
