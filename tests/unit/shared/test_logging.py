"""Issue #1 — CA: logging JSON estructurado con request_id y scrubbing."""

import json
import logging

from fastapi.testclient import TestClient

from adminprop.shared.logging.json_logger import (
    REDACTED,
    AppJsonFormatter,
    RequestIdFilter,
    ScrubbingFilter,
    request_id_var,
    scrub,
)


def _formatted_record(**extra) -> dict:
    record = logging.LogRecord("t", logging.INFO, __file__, 1, "hola", None, None)
    for key, value in extra.items():
        setattr(record, key, value)
    RequestIdFilter().filter(record)
    ScrubbingFilter().filter(record)
    return json.loads(AppJsonFormatter("adminprop-api").format(record))


def test_ca_log_es_json_con_campos_obligatorios():
    """CA #1-03: JSON con timestamp, level, service, request_id, message (sdd_04 §4.1)."""
    token = request_id_var.set("req-123")
    try:
        payload = _formatted_record()
    finally:
        request_id_var.reset(token)

    assert payload["level"] == "INFO"
    assert payload["service"] == "adminprop-api"
    assert payload["message"] == "hola"
    assert payload["request_id"] == "req-123"
    assert "timestamp" in payload


def test_ca_scrubbing_redacta_claves_sensibles():
    """CA #1-03: password/token/bank_info jamas aparecen en logs (sdd_04 §2.4)."""
    payload = _formatted_record(
        password="super-secreta",
        detalle={"bank_info": "CBU 000123", "renter": "Juan"},
    )
    assert payload["password"] == REDACTED
    assert payload["detalle"]["bank_info"] == REDACTED
    assert payload["detalle"]["renter"] == "Juan"


def test_scrub_recursivo_en_listas():
    result = scrub([{"token": "abc", "ok": 1}])
    assert result == [{"token": REDACTED, "ok": 1}]


def test_ca_request_id_se_propaga_en_header(client: TestClient, monkeypatch):
    """CA #1-03: el middleware devuelve X-Request-Id (propio o el recibido)."""

    async def fake_check(url: str, timeout: float) -> str:
        return "ok"

    monkeypatch.setattr("adminprop.modules.health.router.service.check_tcp", fake_check)

    respuesta = client.get("/health")
    assert respuesta.headers["X-Request-Id"]

    con_header = client.get("/health", headers={"X-Request-Id": "cliente-42"})
    assert con_header.headers["X-Request-Id"] == "cliente-42"
