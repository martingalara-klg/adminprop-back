import logging
from contextvars import ContextVar

from pythonjsonlogger import jsonlogger

# request_id propagado a todo el request (sdd_04 §4.6)
request_id_var: ContextVar[str | None] = ContextVar("request_id", default=None)

# sdd_04 §2.4: estas claves jamas aparecen en logs
SENSITIVE_KEYS = {
    "password",
    "password_hash",
    "token",
    "access_token",
    "refresh_token",
    "authorization",
    "cookie",
    "secret",
    "bank_info",
}

REDACTED = "[REDACTED]"


def scrub(value: object) -> object:
    """Redacta recursivamente los valores de claves sensibles (sdd_04 §2.4)."""
    if isinstance(value, dict):
        return {
            key: REDACTED if str(key).lower() in SENSITIVE_KEYS else scrub(inner)
            for key, inner in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [scrub(item) for item in value]
    return value


class ScrubbingFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        for attr, value in list(record.__dict__.items()):
            if attr.lower() in SENSITIVE_KEYS:
                setattr(record, attr, REDACTED)
            elif isinstance(value, dict):
                setattr(record, attr, scrub(value))
        return True


class RequestIdFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = request_id_var.get()
        return True


class AppJsonFormatter(jsonlogger.JsonFormatter):
    """Campos obligatorios de sdd_04 §4.1."""

    def __init__(self, service_name: str) -> None:
        super().__init__(
            fmt="%(asctime)s %(levelname)s %(name)s %(message)s %(request_id)s",
            rename_fields={"asctime": "timestamp", "levelname": "level", "name": "logger"},
        )
        self._service_name = service_name

    def add_fields(self, log_record, record, message_dict):  # type: ignore[no-untyped-def]
        super().add_fields(log_record, record, message_dict)
        log_record["service"] = self._service_name


def setup_logging(service_name: str, level: str = "INFO") -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(AppJsonFormatter(service_name))
    handler.addFilter(RequestIdFilter())
    handler.addFilter(ScrubbingFilter())

    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level.upper())
