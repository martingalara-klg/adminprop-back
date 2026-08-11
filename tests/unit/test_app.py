"""Issue #1 — CA: la app arranca y la configuracion sale de Pydantic Settings."""

from adminprop.config import Settings, get_settings
from adminprop.main import create_app


def test_ca_app_arranca_sin_errores():
    """CA #1-01: create_app construye la aplicacion y expone /health en el schema."""
    app = create_app()
    assert "/health" in app.openapi()["paths"]


def test_ca_configuracion_via_pydantic_settings(monkeypatch):
    """CA #1-04: las variables de entorno pisan los defaults."""
    monkeypatch.setenv("APP_NAME", "AdminProp Test")
    settings = Settings(_env_file=None)
    assert settings.app_name == "AdminProp Test"
    assert get_settings().service_name == "adminprop-api"
