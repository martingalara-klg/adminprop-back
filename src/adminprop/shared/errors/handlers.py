"""Handler global de excepciones de dominio (issue #6).

SDD: core/sdd_03_api_contracts.md parrafo "Formato de respuesta" (CUSTOM,
no RFC 7807). Skill: docs/skills/error-handling.md.
"""

from __future__ import annotations

import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from adminprop.shared.errors.base import AdminPropException
from adminprop.shared.errors.codes import RateLimitExceededException

logger = logging.getLogger("adminprop.errors")


def register_exception_handlers(app: FastAPI) -> None:
    """Registra los handlers globales. Llamado desde `main.create_app`."""

    @app.exception_handler(AdminPropException)
    async def handle_adminprop_exception(request: Request, exc: AdminPropException) -> JSONResponse:
        headers: dict[str, str] = {}
        if isinstance(exc, RateLimitExceededException):
            retry_after = exc.details.get("retry_after_seconds", 60)
            headers["Retry-After"] = str(retry_after)
        elif exc.error_code == "ACCOUNT_LOCKED" and "retry_after_seconds" in exc.details:
            # sdd_03 §"Codigos de Error Globales": countdown en el body,
            # no en un header -- ACCOUNT_LOCKED no es un rate-limit HTTP.
            pass

        return JSONResponse(
            status_code=exc.status_code,
            content={
                "error": {
                    "code": exc.error_code,
                    "message": exc.message,
                    "field": exc.field,
                    "details": exc.details,
                }
            },
            headers=headers,
        )

    @app.exception_handler(RequestValidationError)
    async def handle_validation_error(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        errors = exc.errors()
        first = errors[0] if errors else {}
        field = ".".join(
            str(part) for part in first.get("loc", []) if part not in ("body", "query", "path")
        )
        return JSONResponse(
            status_code=400,
            content={
                "error": {
                    "code": "VALIDATION_ERROR",
                    "message": first.get("msg", "Validacion fallida."),
                    "field": field or None,
                    "details": {"errors": errors},
                }
            },
        )

    @app.exception_handler(Exception)
    async def handle_unhandled(request: Request, exc: Exception) -> JSONResponse:
        logger.error(
            "unhandled exception",
            exc_info=exc,
            extra={"path": str(request.url.path)},
        )
        return JSONResponse(
            status_code=500,
            content={
                "error": {
                    "code": "INTERNAL_ERROR",
                    "message": "Ocurrio un error inesperado. El equipo fue notificado.",
                    "field": None,
                    "details": {},
                }
            },
        )
