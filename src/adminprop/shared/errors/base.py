"""Base de las excepciones de dominio (issue #6).

SDD: core/sdd_03_api_contracts.md §"Formato de respuesta" (formato CUSTOM,
no RFC 7807). Skill: docs/skills/error-handling.md.

Ninguna ruta de este proyecto usaba todavia excepciones de dominio (los
modulos existentes -- health -- no tienen error.code propios). El modulo
`auth` es el primero que los necesita (UNAUTHORIZED, ACCOUNT_LOCKED,
MEMBERSHIP_INACTIVE, RATE_LIMIT_EXCEEDED), asi que este issue crea el
framework base descripto en el skill para que los modulos siguientes
(#7, #9, ...) lo reutilicen sin reinventarlo.
"""

from __future__ import annotations


class AdminPropException(Exception):
    """Base de todas las excepciones de dominio.

    Subclases declaran `status_code` + `error_code` (uno por `error.code`
    del catalogo -- sdd_03 parrafo "Codigos de Error Globales"). El handler
    global (`shared/errors/handlers.py`) las traduce al shape custom
    `{ "error": { code, message, field, details } }`.
    """

    status_code: int = 500
    error_code: str = "INTERNAL_ERROR"
    message: str = "Ocurrio un error inesperado. Por favor intenta nuevamente."

    def __init__(
        self,
        *,
        message: str | None = None,
        field: str | None = None,
        details: dict | None = None,
    ) -> None:
        self.message = message or self.message
        self.field = field
        self.details = details or {}
        super().__init__(self.message)
