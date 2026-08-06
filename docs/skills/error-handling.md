# error-handling (backend)

## Cuándo leer este skill

Leer **antes de**:

- Crear o usar una excepción de dominio.
- Definir un `error.code` nuevo.
- Mapear un error de proveedor externo (Resend) a una respuesta HTTP.
- Configurar el exception handler global.

## Stack relevante

| Item | Valor | Fuente |
|---|---|---|
| Formato de error | **CUSTOM** `{ "error": { "code", "message", "field", "details" } }` — NO RFC 7807 | `sdd_03` §"Formato de respuesta" |
| Logger | `python-json-logger` (JSON estructurado, `request_id` propagado) | backend `CLAUDE.md` §3 |
| Tracker de errores | Sentry | backend `CLAUDE.md` §3 |
| Política de scrubbing | Nunca aparecen `password`, `password_hash`, `access_token`, `refresh_token`, `bank_info`, `billing_info` | backend `CLAUDE.md` §7 |

## SDDs de referencia

- `core/sdd_03_api_contracts.md` §"Formato de respuesta" — formato CUSTOM oficial.
- `core/sdd_03_api_contracts.md` §"Códigos de Error Globales" — catálogo completo de `error.code`.
- `core/sdd_04_nonfunctional.md` §4.1 — campos obligatorios de cada log entry + regla de scrubbing.
- `core/sdd_04_nonfunctional.md` §2.1 — modelo de amenazas (exfiltración via logs).

## El patrón

### Formato de error (CUSTOM, no RFC 7807)

```json
{
  "error": {
    "code": "CONTRACT_LOCKED",
    "message": "El contrato #1234 está bloqueado y no acepta modificaciones.",
    "field": "contract_id",
    "details": {
      "contract_id": "uuid",
      "locked_reason": "settlement_in_progress"
    }
  }
}
```

Reglas:

- `code` es un identificador estable (snake_case mayúsculas), usable como discriminador en el frontend (ver `error-handling.md` del frontend).
- `message` es legible para humanos en español (locale por default).
- `field` apunta al campo del request que causó el error (opcional). Usado para resaltar el input en UI.
- `details` es un objeto JSON con metadatos específicos del error (opcional).

### Jerarquía de excepciones de dominio

Base abstracta + una subclase por `error.code` del SDD.

```python
# src/adminprop/shared/errors/base.py

class AdminPropException(Exception):
    """Base de todas las excepciones de dominio."""

    status_code: int = 500
    error_code: str = "INTERNAL_ERROR"
    message: str = "Ocurrió un error inesperado. Por favor intenta nuevamente."

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
```

### Subclases — una por `error.code` del SDD

```python
# src/adminprop/shared/errors/codes.py
# SDD: sdd_03 §"Códigos de Error Globales"


class ValidationError(AdminPropException):
    status_code = 400
    error_code = "VALIDATION_ERROR"


class InvalidDateRange(AdminPropException):
    status_code = 400
    error_code = "INVALID_DATE_RANGE"
    message = "El rango de fechas es inválido (end_date < start_date)."


class UnauthorizedException(AdminPropException):
    status_code = 401
    error_code = "UNAUTHORIZED"
    message = "Token ausente, expirado o inválido."


class ForbiddenException(AdminPropException):
    status_code = 403
    error_code = "FORBIDDEN"
    message = "No tenés permiso para realizar esta acción."


class RoleRequiredException(AdminPropException):
    status_code = 403
    error_code = "ROLE_REQUIRED"


class ContractOverlapException(AdminPropException):
    status_code = 409
    error_code = "CONTRACT_OVERLAP"
    message = "La propiedad ya tiene un contrato vigente en ese rango de fechas."


class SuperAdminRequiredException(AdminPropException):
    status_code = 403
    error_code = "SUPERADMIN_REQUIRED"


class AccountLockedException(AdminPropException):
    status_code = 403
    error_code = "ACCOUNT_LOCKED"


class AdjustmentPendingExistsException(AdminPropException):
    status_code = 409
    error_code = "ADJUSTMENT_PENDING_EXISTS"
    message = "Ya existe un ajuste pendiente para este contrato."


class NotFoundException(AdminPropException):
    status_code = 404
    error_code = "NOT_FOUND"
    message = "El recurso solicitado no existe."


class ConflictException(AdminPropException):
    status_code = 409
    error_code = "CONFLICT"


class SettlementAlreadyExistsException(AdminPropException):
    status_code = 409
    error_code = "SETTLEMENT_ALREADY_EXISTS"
    message = "Ya existe una liquidación para este propietario y período."


class AdjustmentAlreadyAppliedException(AdminPropException):
    status_code = 409
    error_code = "ADJUSTMENT_ALREADY_APPLIED"
    message = "El ajuste ya fue aplicado y no puede volver a aplicarse."


class EntityHasDependenciesException(AdminPropException):
    status_code = 409
    error_code = "ENTITY_HAS_DEPENDENCIES"


class UserAlreadyMemberException(AdminPropException):
    status_code = 409
    error_code = "USER_ALREADY_MEMBER"


class InvitationPendingExistsException(AdminPropException):
    status_code = 409
    error_code = "INVITATION_PENDING_EXISTS"


class WorkOrderAlreadyClosedException(AdminPropException):
    status_code = 409
    error_code = "WORK_ORDER_ALREADY_CLOSED"


class DeletionAlreadyRequestedException(AdminPropException):
    status_code = 409
    error_code = "DELETION_ALREADY_REQUESTED"


class BusinessRuleViolation(AdminPropException):
    status_code = 422
    error_code = "BUSINESS_RULE_VIOLATION"


class LastOwnerRequiredException(AdminPropException):
    status_code = 422
    error_code = "LAST_OWNER_REQUIRED"
    message = "Debe quedar al menos un owner activo en la organización."


class InvitationExpiredException(AdminPropException):
    status_code = 422
    error_code = "INVITATION_EXPIRED"


class InvitationAlreadyAcceptedException(AdminPropException):
    status_code = 422
    error_code = "INVITATION_ALREADY_ACCEPTED"


class PaymentExceedsContractBalanceException(AdminPropException):
    status_code = 422
    error_code = "PAYMENT_EXCEEDS_CONTRACT_BALANCE"
    message = "El monto del cobro excede el saldo pendiente del contrato."


class InvalidStatusTransitionException(AdminPropException):
    status_code = 422
    error_code = "INVALID_STATUS_TRANSITION"


class ExchangeRateRequiredException(AdminPropException):
    status_code = 400
    error_code = "EXCHANGE_RATE_REQUIRED"
    message = "Se requiere el tipo de cambio porque la moneda del pago difiere de la del contrato."


class SettlementExchangeRateRequiredException(AdminPropException):
    status_code = 400
    error_code = "SETTLEMENT_EXCHANGE_RATE_REQUIRED"
    message = "Se requiere el tipo de cambio para generar la liquidación en USD."


class RentPeriodAlreadyPaidException(AdminPropException):
    status_code = 422
    error_code = "RENT_PERIOD_ALREADY_PAID"
    message = "El período ya fue pagado en su totalidad."


class RateLimitExceededException(AdminPropException):
    status_code = 429
    error_code = "RATE_LIMIT_EXCEEDED"
    message = "Demasiadas solicitudes. Esperá unos segundos e intentá nuevamente."


class InternalError(AdminPropException):
    status_code = 500
    error_code = "INTERNAL_ERROR"
    message = "Ocurrió un error inesperado. El equipo fue notificado."
```

> El catálogo completo está en `sdd_03 §"Códigos de Error Globales"` (y los códigos específicos por dominio en cada sección). Este archivo es el espejo Python.

### Handler global de excepciones

```python
# src/adminprop/shared/errors/handlers.py
import logging
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import ValidationError as PydanticValidationError

from adminprop.shared.errors.base import AdminPropException
from adminprop.shared.errors.codes import RateLimitExceededException


logger = logging.getLogger(__name__)


def register_exception_handlers(app: FastAPI) -> None:

    @app.exception_handler(AdminPropException)
    async def handle_adminprop_exception(request: Request, exc: AdminPropException) -> JSONResponse:
        headers = {}
        if isinstance(exc, RateLimitExceededException):
            retry_after = exc.details.get("retry_after_seconds", 60)
            headers["Retry-After"] = str(retry_after)

        # SDD: sdd_03 §"Formato de respuesta" — formato CUSTOM
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
    async def handle_validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
        # Mapear los errores de Pydantic al shape custom
        # Tomar el primer error como el "principal" (el frontend muestra uno por campo)
        first = exc.errors()[0] if exc.errors() else {}
        field = ".".join(str(x) for x in first.get("loc", []) if x not in ("body", "query", "path"))
        return JSONResponse(
            status_code=400,
            content={
                "error": {
                    "code": "VALIDATION_ERROR",
                    "message": first.get("msg", "Validación fallida."),
                    "field": field or None,
                    "details": {"errors": exc.errors()},
                }
            },
        )

    @app.exception_handler(Exception)
    async def handle_unhandled(request: Request, exc: Exception) -> JSONResponse:
        # Catch-all: log con stack trace, retornar INTERNAL_ERROR genérico.
        # Sentry lo captura via su middleware.
        logger.error(
            "unhandled exception",
            exc_info=exc,
            extra={
                "request_id": getattr(request.state, "request_id", None),
                "organization_id": str(getattr(request.state, "organization_id", None)) if getattr(request.state, "organization_id", None) else None,
                "path": str(request.url.path),
            },
        )
        return JSONResponse(
            status_code=500,
            content={
                "error": {
                    "code": "INTERNAL_ERROR",
                    "message": "Ocurrió un error inesperado. El equipo fue notificado.",
                    "field": None,
                    "details": {},
                }
            },
        )
```

### Uso desde el service

```python
# src/adminprop/modules/contracts/service.py
from adminprop.shared.errors.codes import (
    PaymentExceedsContractBalanceException,
    ExchangeRateRequiredException,
    InvalidStatusTransitionException,
)


class PaymentService:

    async def create(self, dto: PaymentCreate, organization_id: UUID) -> Payment:
        # RN-P03
        contract = await self._contracts_repo.get_by_id(dto.contract_id, organization_id)
        pending_balance = await self._repo.pending_balance(organization_id, dto.contract_id)
        if dto.amount > pending_balance:
            raise PaymentExceedsContractBalanceException(
                field="amount",
                details={
                    "pending_balance": float(pending_balance),
                    "requested_amount": float(dto.amount),
                    "contract_id": str(dto.contract_id),
                },
            )

        # sdd_03 §9: exchange_rate obligatorio si la moneda del pago difiere de la del contrato
        if dto.currency != contract.currency and dto.exchange_rate is None:
            raise ExchangeRateRequiredException(
                field="exchange_rate",
                details={
                    "contract_currency": contract.currency,
                    "payment_currency": dto.currency,
                },
            )

        return await self._repo.insert(dto, organization_id)
```

### Logging estructurado y scrubbing

Configuración del logger:

```python
# src/adminprop/shared/logging/config.py
import logging
from pythonjsonlogger import jsonlogger


SENSITIVE_KEYS = {
    "password",
    "password_hash",
    "access_token",
    "refresh_token",
    "authorization",
    "bank_info",
    "billing_info",
    "card_number",
}


class ScrubFilter(logging.Filter):
    """Reemplaza valores de claves sensibles por '<SCRUBBED>' en cada log entry."""

    def filter(self, record: logging.LogRecord) -> bool:
        for attr in vars(record):
            value = getattr(record, attr, None)
            if isinstance(value, dict):
                setattr(record, attr, self._scrub_dict(value))
        return True

    def _scrub_dict(self, d: dict) -> dict:
        result: dict = {}
        for k, v in d.items():
            if k.lower() in SENSITIVE_KEYS:
                result[k] = "<SCRUBBED>"
            elif isinstance(v, dict):
                result[k] = self._scrub_dict(v)
            else:
                result[k] = v
        return result


def configure_logging() -> None:
    handler = logging.StreamHandler()
    formatter = jsonlogger.JsonFormatter(
        "%(asctime)s %(levelname)s %(name)s %(message)s",
        json_indent=None,
    )
    handler.setFormatter(formatter)
    handler.addFilter(ScrubFilter())

    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(logging.INFO)
```

Uso en código:

```python
logger.info(
    "payment received",
    extra={
        "request_id": request_id,
        "organization_id": str(organization_id),
        "user_id": str(user_id),
        "payment_id": str(payment.id),
        "contract_id": str(payment.contract_id),
        "duration_ms": duration_ms,
        "service": "payments",
    },
)
```

`sdd_04 §4.1` exige los campos `timestamp`, `level`, `request_id`, `organization_id`, `user_id`, `service`, `message`, `duration_ms` en cada log entry. El JsonFormatter los serializa automáticamente desde `extra`.

## Template

Skeleton de un nuevo `error.code`:

```python
# 1. En src/adminprop/shared/errors/codes.py, agregar la subclase:

class <Caso>Exception(AdminPropException):
    """SDD: <ruta-del-SDD>.md §<sección>."""

    status_code = <code>           # 400, 403, 404, 409, 422, etc.
    error_code = "<UPPER_SNAKE_CASE>"
    message = "<mensaje legible en español (o EN si es de seguridad y el SDD lo exige textual)>"
```

```python
# 2. En el service, lanzarla con field/details específicos:

raise <Caso>Exception(
    field="<campo-del-request>",
    details={
        "<clave-relevante>": <valor>,
        ...
    },
)
```

```python
# 3. En tests/integration/..., verificar el código y el detalle:

response = await client.post(...)
assert response.status_code == <code>
body = response.json()
assert body["error"]["code"] == "<UPPER_SNAKE_CASE>"
assert body["error"]["details"]["<clave>"] == <valor_esperado>
```

## Checklist pre-commit

- [ ] Toda excepción de dominio hereda de `AdminPropException`.
- [ ] Cada `error.code` está documentado en `sdd_03` (o se agregó a `sdd_03` antes de implementar; ver "Regla de oro" — el SDD se actualiza primero).
- [ ] El `error.code` está en `UPPER_SNAKE_CASE`.
- [ ] El `message` por default está en español, salvo que el SDD lo especifique textual en EN por razones de seguridad.
- [ ] El handler global `AdminPropException` se registra en `app.add_exception_handler` (o vía `register_exception_handlers(app)`).
- [ ] Errores de Pydantic (`RequestValidationError`) se mapean al formato custom con `code = "VALIDATION_ERROR"`.
- [ ] El catch-all `Exception` retorna `INTERNAL_ERROR` genérico (no expone `str(exc)`).
- [ ] El `Retry-After` header se setea cuando se lanza `RateLimitExceededException`.
- [ ] El logger usa `python-json-logger` y todos los logs incluyen `request_id`, `organization_id`, `user_id`, `service`.
- [ ] El `ScrubFilter` (o equivalente) elimina campos sensibles de cualquier dict que termine en un log.
- [ ] Las stack traces se loguean con `logger.error("...", exc_info=exc, extra={...})` (no `print(traceback)`).

## Antipatrones

```python
# ❌ Usar HTTPException con detail=string
raise HTTPException(status_code=409, detail="Contract overlap")
# El frontend recibe { "detail": "Contract overlap" } — sin error.code, sin field.

# ✅ Excepción de dominio que el handler global traduce al shape custom
raise ContractOverlapException(field="start_date", details={"conflicting_contract_id": str(other.id)})
```

```python
# ❌ Exponer str(exc) en el response
try:
    ...
except Exception as exc:
    raise HTTPException(500, detail=str(exc))
# Filtra stack traces, errores de DB, internals del sistema.

# ✅ Catch-all genérico, log con exc_info, response sanitizado
@app.exception_handler(Exception)
async def handle_unhandled(request, exc):
    logger.error("unhandled exception", exc_info=exc, extra={"request_id": ...})
    return JSONResponse(500, content={"error": {"code": "INTERNAL_ERROR", ...}})
```

```python
# ❌ Inventar un error.code que no está en sdd_03
raise AdminPropException(error_code="WEIRD_THING_HAPPENED", message="...")
# Causa: el frontend no tiene cómo discriminar; la doc miente.

# ✅ Primero actualizar el SDD ("Regla de oro"), después implementar.
```

```python
# ❌ Loggear el body completo del request sin scrubbing
logger.info("login attempt", extra={"body": request_body})
# Body incluye "password" → fuga en logs (CloudWatch, Sentry).

# ✅ Loggear sólo campos no-sensibles + confiar en el ScrubFilter
logger.info("login attempt", extra={"email": email, "ip": request.client.host})
```

```python
# ❌ Diferenciar mensaje según razón en login
if user is None:
    raise UnauthorizedException(message="Email no registrado")
if not verify_password(...):
    raise UnauthorizedException(message="Contraseña incorrecta")
# Permite enumeration.

# ✅ Mismo mensaje para ambos casos (sdd_04 §2.2)
if user is None or not verify_password(...):
    raise UnauthorizedException(message="Credenciales incorrectas.")
```

```python
# ❌ Loguear token JWT
logger.warning("refresh failed", extra={"refresh_token": refresh_token})
# El token queda en logs accesibles a developers, sysadmin, Sentry.

# ✅ Loguear sólo el jti o no loguear el token
logger.warning("refresh failed", extra={"jti": jwt_jti, "user_id": user_id})
```

## Referencias

- `core/sdd_03_api_contracts.md` §"Formato de respuesta" — formato custom canónico.
- `core/sdd_03_api_contracts.md` §"Códigos de Error Globales" — catálogo completo (incluye códigos por dominio: contratos, cobros, liquidaciones, mantenimiento, invitaciones, etc.).
- `core/sdd_04_nonfunctional.md` §4.1 — formato de log JSON + campos obligatorios + regla de scrubbing.
- `core/sdd_04_nonfunctional.md` §2.1 — exposición de secretos en logs como vector de amenaza.
- Backend `CLAUDE.md` §6 "Códigos de error transversales" — listado por dominio.
- Backend `CLAUDE.md` §7 "Restricciones de seguridad transversales" — scrubbing automático y campos prohibidos en logs.
- Backend `CLAUDE.md` §8 — "escribir el JSON estructurado en logs con `python-json-logger` incluyendo `request_id`, `organization_id`, `user_id`, `service`, `duration_ms`".
- `_index.md` §4 #24 — decisión `python-json-logger`.
