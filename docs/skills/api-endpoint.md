# api-endpoint

## Cuándo leer este skill

Leer **antes de**:

- Implementar cualquier endpoint REST.
- Modificar el shape de request o response de un endpoint existente.
- Agregar un nuevo `error.code` o cambiar status codes.

## Stack relevante

| Capa | Tecnología | Fuente |
|---|---|---|
| Framework | FastAPI 0.110+ | backend `CLAUDE.md` §3 |
| Validación | Pydantic v2 | backend `CLAUDE.md` §3 |
| Rate limiting | `slowapi` o equivalente (Redis token bucket) | backend `CLAUDE.md` §3, `_index.md` #28 |
| Prefijo API | `/v1` (no `/api/v1`) | `sdd_03` §"Convenciones Generales" |
| Path convention | kebab-case plural (`/work-orders`) | backend `CLAUDE.md` §5 |
| Auth | JWT RS256 en HttpOnly cookie + Bearer header para server-to-server | backend `CLAUDE.md` §3 |
| Pagination | cursor-based default (`?cursor=...&limit=20`); excepción audit (`?page=...&page_size=...`) | `sdd_03` §"Paginación" |
| Async pattern | `202 Accepted` + polling | `sdd_03` §"Convenciones Generales" |
| Format de error | **CUSTOM** `{ "error": { "code", "message", "field", "details" } }` (NO RFC 7807) | `sdd_03` §"Formato de respuesta" |

## SDDs de referencia

- `core/sdd_03_api_contracts.md` — fuente de verdad de cada endpoint, su path, método, request, response, errores y permiso requerido.
- `core/sdd_03_api_contracts.md` §"Catálogo de Permisos" — permisos atómicos del array `permissions` del JWT.
- `core/sdd_03_api_contracts.md` §"Códigos de Error Globales" — lista exhaustiva de `error.code`.
- `core/sdd_04_nonfunctional.md` §2.5 — rate limits por endpoint.
- `core/sdd_04_nonfunctional.md` §2.7 — security headers obligatorios.

## El patrón

### Estructura de un endpoint completo

Un endpoint canónico tiene **6 piezas** en este orden:

1. Path + método (exactamente como el SDD).
2. Status code de éxito (`sdd_03`).
3. `response_model` (Pydantic).
4. Dependencias: permiso requerido + `organization_id` del JWT + rate-limit.
5. Validación del body (Pydantic schema con constraints).
6. Llamada al service + mapeo de excepciones (ver `error-handling.md`).

```python
# src/adminprop/modules/contracts/router.py
# SDD: features/spec_module_03_contratos.md §RF-02 + sdd_03 §"3. Contratos"

from uuid import UUID
from fastapi import APIRouter, Depends, Request, status

from adminprop.shared.tenant import get_current_tenant
from adminprop.shared.rbac import requires_permission
from adminprop.shared.rate_limit import rate_limit
from adminprop.modules.contracts.schemas import (
    ContractCreate,
    ContractResponse,
)
from adminprop.modules.contracts.service import (
    ContractService,
    get_contract_service,
)


router = APIRouter(prefix="/v1/contracts", tags=["contracts"])


@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    response_model=ContractResponse,
    dependencies=[
        Depends(requires_permission("contract:manage")),        # ② permiso
        Depends(rate_limit("contract_create", "30/hour")),       # ③ rate-limit
    ],
)
async def create_contract(
    dto: ContractCreate,                                        # ④ body validado
    organization_id: UUID = Depends(get_current_tenant),
    service: ContractService = Depends(get_contract_service),
) -> ContractResponse:
    """SDD: features/spec_module_03_contratos.md §RF-02. Implements: CA-RF02-01..04, RN-01/03/05/06."""
    contract = await service.create(dto, organization_id)
    return ContractResponse.model_validate(contract)
```

### Schema de request (Pydantic v2)

```python
# src/adminprop/modules/contracts/schemas.py

from datetime import date, datetime
from uuid import UUID
from pydantic import BaseModel, ConfigDict, Field, field_validator


class ContractCreate(BaseModel):
    """Body de POST /v1/contracts. SDD: spec_module_03_contratos §RF-02."""

    property_id: UUID
    tenant_person_id: UUID
    start_date: date
    end_date: date
    monthly_amount: float = Field(..., gt=0)
    currency: str = Field(..., pattern=r"^(ARS|USD)$")

    @field_validator("end_date")
    @classmethod
    def end_after_start(cls, v: date, info) -> date:
        start = info.data.get("start_date")
        if start and v <= start:
            raise ValueError("end_date debe ser posterior a start_date.")
        return v


class ContractResponse(BaseModel):
    """Response de GET /v1/contracts/:id y de POST."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    property_id: UUID
    tenant_person_id: UUID
    start_date: date
    end_date: date
    monthly_amount: float
    currency: str
    status: str
    created_at: datetime
```

### Extracción de `organization_id` del JWT

`organization_id` **nunca** se acepta en body, path o query (excepto `/superadmin/*` como filtro opcional). Se extrae siempre del JWT vía dependency.

```python
# src/adminprop/shared/tenant.py
from uuid import UUID
from fastapi import Depends, HTTPException, status
from adminprop.shared.auth import decode_jwt, JWTPayload


async def get_current_tenant(payload: JWTPayload = Depends(decode_jwt)) -> UUID:
    """Retorna el organization_id del JWT activo. RN-D01."""
    if payload.is_super_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": "FORBIDDEN", "message": "Endpoint no disponible para Super Admin."},
        )
    return payload.org_id
```

Uso en un endpoint regular:

```python
@router.get("/{contract_id}", response_model=ContractResponse)
async def get_contract(
    contract_id: UUID,
    organization_id: UUID = Depends(get_current_tenant),
    service: ContractService = Depends(get_contract_service),
) -> ContractResponse:
    contract = await service.get(contract_id, organization_id)
    if contract is None:
        raise NotFoundException()  # → 404 NOT_FOUND
    return ContractResponse.model_validate(contract)
```

### Verificación de permisos

`sdd_03` usa permisos atómicos (`permissions[]` en JWT), no roles. La dependency `requires_permission` lee el permiso del JWT y rechaza con `403 FORBIDDEN` si no está presente.

```python
# src/adminprop/shared/rbac.py
from fastapi import Depends, HTTPException, status
from adminprop.shared.auth import decode_jwt, JWTPayload


def requires_permission(permission: str):
    """Factory de dependency: requiere que el JWT incluya `permission` en `permissions[]`."""

    async def _check(payload: JWTPayload = Depends(decode_jwt)) -> JWTPayload:
        if permission not in payload.permissions:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={"code": "FORBIDDEN", "message": f"Missing permission: {permission}"},
            )
        return payload

    return _check


async def requires_super_admin(payload: JWTPayload = Depends(decode_jwt)) -> JWTPayload:
    if not payload.is_super_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": "SUPERADMIN_REQUIRED", "message": "Endpoint requires is_super_admin."},
        )
    return payload
```

> Nota de roles: `maintenance` sólo tiene permisos atómicos del módulo de mantenimiento (órdenes de trabajo asignadas y sus cotizaciones); nunca `contract:*`, `payment:*` ni `settlement:*`. Ver `tenant-isolation.md` y `core/sdd_02_domain_model.md` §3.

### Rate limiting

`sdd_04` §2.5 documenta los límites por endpoint. Usar `slowapi` (o el wrapper del proyecto) con backend Redis.

```python
# src/adminprop/shared/rate_limit.py (esqueleto)
from fastapi import Depends, HTTPException, Request
from adminprop.shared.cache import redis_client


def rate_limit(key: str, limit: str):
    """
    Ej: rate_limit("contract_create", "30/hour")
    El key se concatena con `organization_id` o `ip` según la política del SDD.
    """
    # Implementación: token bucket en Redis con counter + TTL.
    # Si supera el límite: 429 + Retry-After.
    async def _check(request: Request):
        # ... ver _index #28 ...
        ...
    return _check
```

### Mapeo de excepciones de dominio → respuesta HTTP custom

Las excepciones específicas viven en `<module>/exceptions.py` (ver `error-handling.md`). El handler global las traduce al shape custom:

```python
# src/adminprop/shared/errors/handlers.py
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from adminprop.shared.errors.base import AdminPropException


def register_exception_handlers(app: FastAPI) -> None:

    @app.exception_handler(AdminPropException)
    async def handle_adminprop_exception(request: Request, exc: AdminPropException) -> JSONResponse:
        # SDD: sdd_03 §"Formato de respuesta" — formato CUSTOM (no RFC 7807)
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
        )
```

### Endpoint async (202 Accepted + polling)

Cuando la operación toma > 5 segundos (cálculo masivo de liquidaciones, obtención + aplicación de índice ICL/IPC sobre un lote de contratos, generación de comprobantes), retornar `202 Accepted` con un `job_id`. El cliente polea `GET /<resource>/:id` o espera notificación in-app.

```python
@router.post(
    "/calculate",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=SettlementCalculationAccepted,
    dependencies=[Depends(requires_permission("settlement:manage"))],
)
async def calculate_settlements(
    dto: SettlementCalculateRequest,
    organization_id: UUID = Depends(get_current_tenant),
    service: SettlementService = Depends(get_settlement_service),
) -> SettlementCalculationAccepted:
    """SDD: features/spec_module_05_liquidaciones.md §RF-01 + sdd_03 §"5. Liquidaciones"."""
    job = await service.enqueue_calculation(dto, organization_id)
    return SettlementCalculationAccepted(
        data={
            "settlement_batch_id": job.id,
            "status": "processing",
            "estimated_seconds": 15,
        }
    )
```

### Paginación cursor-based

```python
from typing import Annotated
from pydantic import Field

@router.get("", response_model=ContractListResponse)
async def list_contracts(
    organization_id: UUID = Depends(get_current_tenant),
    cursor: str | None = None,
    limit: Annotated[int, Field(ge=1, le=100)] = 20,
    property_id: UUID | None = None,
    service: ContractService = Depends(get_contract_service),
) -> ContractListResponse:
    """SDD: sdd_03 §"3. Contratos" GET /contracts. Permiso requerido: contract:read_all o contract:read_own."""
    items, next_cursor = await service.list(organization_id, cursor, limit, property_id=property_id)
    return ContractListResponse(
        data=[ContractResponse.model_validate(c) for c in items],
        meta={"next_cursor": next_cursor, "limit": limit},
    )
```

> `audit_logs` usa `page` + `page_size` por excepción (ver `sdd_03 §16`); el resto usa cursor.

### Anti-enumeration en endpoints públicos

`sdd_04` §2.2a y §2.2 — `forgot-password` siempre retorna 200; `login` no diferencia "email no existe" vs "password incorrecta". Estos comportamientos son **mensajes literales** del SDD.

```python
@router.post("/v1/auth/forgot-password", status_code=200)
async def forgot_password(dto: ForgotPasswordRequest):
    """SDD: sdd_03 §1 + sdd_04 §2.2a — anti-enumeration."""
    # SIEMPRE retorna 200 + mismo mensaje, exista el email o no.
    await auth_service.maybe_send_reset_email(dto.email)
    return {
        "data": {
            "message": (
                "Si el email está registrado, recibirás instrucciones para "
                "restablecer tu contraseña en los próximos minutos."
            )
        }
    }
```

```python
@router.post("/v1/auth/login")
async def login(dto: LoginRequest):
    user = await auth_service.authenticate(dto.email, dto.password)
    if user is None:
        # SDD: mismo mensaje para "no existe" y "password incorrecta"
        raise InvalidCredentialsException()   # → 401 UNAUTHORIZED
    # ... continuar con MFA si aplica
```

## Template

```python
# src/adminprop/modules/<module>/router.py
# SDD: <ruta-del-SDD>.md §<sección>
# Implements: <CA-XX...>, <RN-YY...>

from uuid import UUID
from fastapi import APIRouter, Depends, status

from adminprop.shared.tenant import get_current_tenant
from adminprop.shared.rbac import requires_permission
from adminprop.shared.rate_limit import rate_limit
from adminprop.modules.<module>.schemas import (
    <Resource>Create,
    <Resource>Response,
    <Resource>ListResponse,
)
from adminprop.modules.<module>.service import (
    <Resource>Service,
    get_<resource>_service,
)


router = APIRouter(prefix="/v1/<resource>s", tags=["<resource>s"])


# ─── Crear ─────────────────────────────────────────────────────────
@router.post(
    "",
    status_code=status.HTTP_201_CREATED,   # o 202 si es async
    response_model=<Resource>Response,
    dependencies=[
        Depends(requires_permission("<resource>:manage")),
        Depends(rate_limit("<resource>_create", "<N>/<periodo>")),
    ],
)
async def create_<resource>(
    dto: <Resource>Create,
    organization_id: UUID = Depends(get_current_tenant),
    service: <Resource>Service = Depends(get_<resource>_service),
) -> <Resource>Response:
    """SDD: <ruta>.md §<sección>. Implements: <CA-XX>, <RN-XX>."""
    obj = await service.create(dto, organization_id)
    return <Resource>Response.model_validate(obj)


# ─── Detalle ───────────────────────────────────────────────────────
@router.get(
    "/{resource_id}",
    response_model=<Resource>Response,
    dependencies=[Depends(requires_permission("<resource>:read"))],
)
async def get_<resource>(
    resource_id: UUID,
    organization_id: UUID = Depends(get_current_tenant),
    service: <Resource>Service = Depends(get_<resource>_service),
) -> <Resource>Response:
    """SDD: <ruta>.md §<sección>."""
    obj = await service.get(resource_id, organization_id)
    if obj is None:
        raise NotFoundException()   # → 404 NOT_FOUND (no 403, RN-D01)
    return <Resource>Response.model_validate(obj)


# ─── Listado paginado (cursor-based) ───────────────────────────────
@router.get(
    "",
    response_model=<Resource>ListResponse,
    dependencies=[Depends(requires_permission("<resource>:read"))],
)
async def list_<resource>s(
    organization_id: UUID = Depends(get_current_tenant),
    cursor: str | None = None,
    limit: int = 20,
    service: <Resource>Service = Depends(get_<resource>_service),
) -> <Resource>ListResponse:
    """SDD: <ruta>.md §<sección>."""
    items, next_cursor = await service.list(organization_id, cursor=cursor, limit=limit)
    return <Resource>ListResponse(
        data=[<Resource>Response.model_validate(i) for i in items],
        meta={"next_cursor": next_cursor, "limit": limit},
    )
```

## Checklist pre-commit

- [ ] El path y método HTTP coinciden **exactamente** con `sdd_03`.
- [ ] El path usa prefijo `/v1` y kebab-case plural.
- [ ] El status code de éxito coincide con el SDD (201 para create, 200 para read/update, 204 para delete, 202 para async).
- [ ] El body acepta sólo los campos del SDD (Pydantic los rechaza por default con `extra="forbid"` si está configurado en el `BaseModel` del proyecto).
- [ ] Las validaciones (longitud, regex, rango) reflejan las del SDD.
- [ ] `organization_id` se extrae del JWT vía `Depends(get_current_tenant)`, **no** del body/path/query.
- [ ] El endpoint declara el permiso requerido vía `Depends(requires_permission("..."))` (o `requires_super_admin` para `/superadmin/*`).
- [ ] El rate-limit del `sdd_04 §2.5` está aplicado si corresponde.
- [ ] Los errores se mapean a excepciones de dominio (`SlugTakenException`, `PeriodLockedException`, etc.), no a `HTTPException(detail="...")` directo.
- [ ] El response format es `{ "data": ... }` (más `{ "meta": ... }` si es lista paginada).
- [ ] El error format es custom: `{ "error": { "code", "message", "field", "details" } }`.
- [ ] Cross-tenant access retorna **404 NOT_FOUND**, no 403 (RN-D01 — no revelar existencia).
- [ ] Endpoints async retornan 202 + `{ data: { <job_id>, status, estimated_completion_seconds } }`.
- [ ] El docstring del endpoint cita el SDD por ruta + sección + lista los CA y RN implementados.

## Antipatrones

```python
# ❌ Aceptar organization_id en el body
@router.post("/contracts")
async def create_contract(dto: ContractCreate):
    # ContractCreate tiene organization_id como campo. ¡Manipulable!
    pass

# ✅ organization_id viene del JWT
@router.post("/contracts")
async def create_contract(
    dto: ContractCreate,
    organization_id: UUID = Depends(get_current_tenant),
):
    pass
```

```python
# ❌ Retornar HTTPException con detail=string
raise HTTPException(status_code=409, detail="Slug already taken")
# Causa: el frontend recibe { "detail": "Slug already taken" } y no
# tiene un error.code para discriminar.

# ✅ Excepción de dominio + handler global que produce el shape custom
raise SlugAlreadyTakenException(suggestions=["acme-corp", "acme-inc"])
# → { "error": { "code": "CONFLICT", "message": "...", "details": {"suggestions": [...]} } }
```

```python
# ❌ Retornar 403 cuando un usuario accede a un recurso de otro tenant
if contract.organization_id != current_org_id:
    raise HTTPException(status_code=403, detail="Forbidden")
# Causa: revela que el recurso existe.

# ✅ Retornar 404 — el repository ya filtra por organization_id
contract = await repo.get_by_id(contract_id, current_org_id)
if contract is None:
    raise NotFoundException()   # → 404
```

```python
# ❌ Endpoint sync para operación larga
@router.post("/settlements/calculate")
async def calculate_settlements(dto: SettlementCalculateRequest):
    batch = await db.insert(dto)
    index_value = await bcra_client.get_icl_index(batch.reference_date)   # 3-5 segundos
    await settlement_service.apply_adjustment(batch, index_value)          # cálculo masivo
    return batch
# Causa: bloquea la conexión HTTP, P95 muy alta.

# ✅ Endpoint encola el job y retorna 202
@router.post("/settlements/calculate", status_code=202)
async def calculate_settlements(
    dto: SettlementCalculateRequest,
    organization_id: UUID = Depends(get_current_tenant),
    service: SettlementService = Depends(get_settlement_service),
):
    batch = await service.create_pending(dto, organization_id)
    # service encola la tarea Celery calculate_settlement_batch(batch.id)
    return {
        "data": {
            "id": batch.id,
            "status": "pending",
            "estimated_processing_seconds": 15,
        }
    }
```

```python
# ❌ Aceptar campos extra silenciosamente
class ContractCreate(BaseModel):
    monthly_amount: float
# Si el cliente envía { "monthly_amount": 1000, "admin_override": true }, Pydantic
# lo ignora pero no rechaza → comportamiento opaco.

# ✅ extra="forbid" (configurar en el BaseModel del proyecto)
class ContractCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    monthly_amount: float
# Ahora el cliente recibe 422 VALIDATION_ERROR si manda campos no declarados.
```

```python
# ❌ Mensajes de error de seguridad inventados
raise InvalidCredentialsException(message="Email not found in our database.")
# Revela que el email no existe → enumeration attack.

# ✅ Mensaje exacto del SDD (anti-enumeration)
raise InvalidCredentialsException()   # mensaje fijo: "Credenciales incorrectas."
```

```python
# ❌ Diferenciar el mensaje según razón en login
if user is None:
    raise HTTPException(401, "Email no registrado")
if not verify_password(...):
    raise HTTPException(401, "Contraseña incorrecta")
# Permite enumeration por email.

# ✅ Mismo mensaje para ambos casos (sdd_04 §2.2)
if user is None or not verify_password(...):
    raise InvalidCredentialsException()
```

```python
# ❌ Path con prefijo /api/v1 (incorrecto en este proyecto)
router = APIRouter(prefix="/api/v1/contracts", tags=["contracts"])

# ✅ El prefijo es /v1 — sin /api
router = APIRouter(prefix="/v1/contracts", tags=["contracts"])
```

## Referencias

- `core/sdd_03_api_contracts.md` §"Convenciones Generales" — Base URL, formato de respuesta, paginación, formato de error CUSTOM (no RFC 7807).
- `core/sdd_03_api_contracts.md` §"Catálogo de Permisos" — lista exhaustiva de permisos atómicos.
- `core/sdd_03_api_contracts.md` §"Códigos de Error Globales" — `error.code` por escenario.
- `core/sdd_03_api_contracts.md` §"Resumen de Autorización por Recurso" — tabla de permisos por endpoint, baseline del checklist.
- `core/sdd_04_nonfunctional.md` §2.2 / §2.2a — anti-enumeration: forgot-password siempre 200, login con mensaje genérico.
- `core/sdd_04_nonfunctional.md` §2.5 — tabla de rate limits por endpoint.
- Backend `CLAUDE.md` §4 "organization_id siempre derivado del JWT" — invariante absoluta.
- Backend `CLAUDE.md` §6 "Convenciones" — prefijo `/v1`, kebab-case plural, formato `{ data, meta }`.
- `_index.md` §4 #20 — JWT en HttpOnly Secure cookies; el endpoint asume el JWT decodificado en el `Authorization` header internamente (el middleware lo extrae de la cookie).
- `_index.md` §4 #49 — no existe endpoint de switch de organización; el flujo es logout + login.
