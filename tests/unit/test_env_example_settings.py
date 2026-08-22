"""Issue #38 — bugfix: variables de rol en .env.example no coinciden con los
campos de Settings.

`ADMINPROP_APP_ROLE_PASSWORD` y `ADMINPROP_SUPERADMIN_ROLE_PASSWORD` fueron
introducidas en #3 con un prefijo `ADMINPROP_` que ninguna otra variable del
archivo usa; pydantic-settings mapea cada campo de `Settings` a la variable
de entorno homónima en mayúsculas (sin prefijo, `SettingsConfigDict` no
declara `env_prefix`), así que overridearlas vía `.env` no tenía efecto
(fallo silencioso, ver PR #37).

Este test previene la regresión en general: parsea `.env.example` (incluidas
las variables de override comentadas, que documentan defaults ya cubiertos
por `Settings`) y verifica que cada nombre listado coincide 1:1 con un campo
de `Settings` una vez normalizado a mayúsculas.
"""

import re
from pathlib import Path

import pytest

from adminprop.config import Settings

REPO_ROOT = Path(__file__).resolve().parents[2]
ENV_EXAMPLE_PATH = REPO_ROOT / ".env.example"

# Igual que tests/unit/infra/test_docker_compose.py (issue #13): cuando la
# suite corre DENTRO del contenedor de la API (docker/docker-compose.yml),
# `.env.example` vive en el host y no se copia a la imagen (docker/
# Dockerfile.api solo hace COPY de pyproject.toml/README.md/alembic.ini/src/
# tests) porque la app no lo necesita en runtime — solo el desarrollador al
# crear su `.env` local. Se saltea (no se debilita la aserción) cuando el
# archivo no está presente; en CI (`actions/checkout` + pytest directo,
# .github/workflows/ci.yml) el archivo sí existe y el test corre.
_skip_if_env_example_missing = pytest.mark.skipif(
    not ENV_EXAMPLE_PATH.exists(),
    reason=(
        ".env.example no esta presente en este filesystem (suite corriendo "
        "dentro del contenedor de la API, que no lo copia a la imagen)."
    ),
)

# Matchea tanto `KEY=value` como `# KEY=value` (overrides opcionales
# documentados pero comentados porque coinciden con el default de Settings).
_ENV_VAR_LINE = re.compile(r"^#?\s*([A-Z][A-Z0-9_]*)=")


def _documented_env_vars() -> list[str]:
    names = []
    for line in ENV_EXAMPLE_PATH.read_text(encoding="utf-8").splitlines():
        match = _ENV_VAR_LINE.match(line)
        if match:
            names.append(match.group(1))
    return names


def _settings_env_var_names() -> set[str]:
    # SettingsConfigDict no declara env_prefix ni los campos usan
    # validation_alias -> pydantic-settings usa field_name.upper() literal.
    return {field_name.upper() for field_name in Settings.model_fields}


@_skip_if_env_example_missing
def test_ca_38_01_env_example_declara_al_menos_una_variable():
    """Guarda contra un `.env.example` vacío o con el regex roto en silencio."""
    assert len(_documented_env_vars()) >= 10


@_skip_if_env_example_missing
def test_ca_38_02_cada_variable_de_env_example_es_un_campo_de_settings():
    """CA #38-01: los nombres en `.env.example` coinciden 1:1 con los campos
    de `Settings` (previene regresiones como `ADMINPROP_APP_ROLE_PASSWORD`
    que no correspondía a ningún campo real y no tenía efecto al overridear).
    """
    documented = _documented_env_vars()
    settings_vars = _settings_env_var_names()

    unknown = [name for name in documented if name not in settings_vars]

    assert not unknown, (
        f"Variables en .env.example sin campo correspondiente en Settings: "
        f"{unknown}. Si un override no tiene efecto silenciosamente, el "
        f"nombre de la variable no matchea field_name.upper() en Settings."
    )


@_skip_if_env_example_missing
def test_ca_38_03_variables_de_rol_matchean_los_campos_reales():
    """CA #38-01 (regresión puntual): `APP_ROLE_PASSWORD` y
    `SUPERADMIN_ROLE_PASSWORD` (sin prefijo `ADMINPROP_`) son las variables
    documentadas, y matchean `Settings.app_role_password` /
    `Settings.superadmin_role_password`.
    """
    documented = set(_documented_env_vars())

    assert "APP_ROLE_PASSWORD" in documented
    assert "SUPERADMIN_ROLE_PASSWORD" in documented
    assert "ADMINPROP_APP_ROLE_PASSWORD" not in documented
    assert "ADMINPROP_SUPERADMIN_ROLE_PASSWORD" not in documented
