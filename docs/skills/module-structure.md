# module-structure (backend)

## Cuándo leer este skill

Leer **antes de**:

- Crear un módulo nuevo en `src/adminprop/modules/`.
- Reorganizar la estructura interna de un módulo existente.
- Decidir dónde poner una capa (router/service/repository/schema/model).

Es el contrato de arquitectura interna de cada módulo backend.

## Stack relevante

| Capa | Tecnología | Fuente |
|---|---|---|
| Lenguaje | Python 3.11+ | backend `CLAUDE.md` §3 |
| Framework HTTP | FastAPI 0.110+ | backend `CLAUDE.md` §3 |
| Validación / DTOs | Pydantic (incluido en FastAPI), PascalCase singular | backend `CLAUDE.md` §3, §5 |
| ORM | SQLAlchemy 2.0 (estilo 2.0, sesiones explícitas, sin lazy loading) | backend `CLAUDE.md` §3 |
| Inyección de dependencias | `fastapi.Depends` | backend `CLAUDE.md` §4 |
| Tests | pytest + pytest-asyncio + httpx | backend `CLAUDE.md` §3 |

## SDDs de referencia

- Backend `CLAUDE.md` §4 "Arquitectura de backend" — patrón router → service → repository.
- Backend `CLAUDE.md` §9 "Estructura del repositorio".
- `core/sdd_03_api_contracts.md` §"Convenciones Generales" — formato de respuesta, paginación, prefijo `/v1`.

## El patrón

### Árbol de archivos de un módulo

```
src/adminprop/
└── modules/
    └── <module_name>/                    ← snake_case, ej: properties, contracts, maintenance
        ├── __init__.py
        ├── router.py                     ← endpoints, validación de entrada, HTTP
        ├── service.py                    ← lógica de negocio, implementa RN-XX
        ├── repository.py                 ← acceso a BD; siempre filtra por organization_id
        ├── schemas.py                    ← Pydantic schemas (PascalCase singular)
        ├── models.py                     ← SQLAlchemy models (PascalCase singular)
        ├── exceptions.py                 ← excepciones de dominio (una por error.code)
        └── tests/
            ├── __init__.py
            ├── conftest.py               ← fixtures específicas del módulo
            ├── test_<feature>_unit.py    ← tests unitarios de service
            └── test_<feature>_integration.py  ← tests httpx contra el router

```

Lista de módulos de ejemplo del proyecto: `properties`, `people`, `contracts`, `payments`, `settlements`, `maintenance`, `admin`, `superadmin`, `notifications`.

Si el módulo es grande (ej: `payments`), se permite anidamiento:

```
modules/payments/
├── __init__.py
├── receipts/
│   ├── __init__.py
│   ├── router.py
│   ├── service.py
│   ├── repository.py
│   ├── schemas.py
│   ├── models.py
│   └── tests/
├── payment_plans/
│   └── ...
├── reconciliations/
│   └── ...
└── payment_vouchers/
    └── ...
```

### Responsabilidades por capa

| Capa | Hace | NO hace |
|---|---|---|
| `router.py` | Define rutas, valida entrada con Pydantic, extrae `organization_id` del JWT, llama al service, mapea excepciones de dominio a HTTPException con formato custom (ver `error-handling.md`), declara permiso requerido | SQL, lógica de negocio, llamadas a servicios externos |
| `service.py` | Implementa RN-XX (reglas de negocio), orquesta múltiples repositories, encola tareas Celery, llama a integraciones externas vía wrappers de `shared/` | SQL directo, manipular request/response HTTP, decidir status codes |
| `repository.py` | Queries SQL vía SQLAlchemy, **siempre filtra por `organization_id`** (defense in depth sobre RLS), retorna modelos ORM o None | Lógica de negocio, reglas RN-XX, llamadas HTTP |
| `schemas.py` | Define `<Resource>Create`, `<Resource>Update`, `<Resource>Response`, `<Resource>ListResponse` con Pydantic — PascalCase singular | Modelos SQLAlchemy, lógica de validación compleja (eso va en `model_validator` puro) |
| `models.py` | Define los modelos SQLAlchemy 2.0 (`class Contract(Base): ...`) con columnas, índices, relaciones — PascalCase singular | Lógica de negocio en `@property`, queries |
| `exceptions.py` | Subclases de `AdminPropException` con `status_code` y `error_code` por cada FA del SDD (ver `error-handling.md`) | — |

### Patrón de inyección de dependencias

FastAPI usa `Depends` para inyectar el service en el router:

```python
# router.py
from fastapi import APIRouter, Depends, status
from adminprop.shared.tenant import get_current_tenant
from adminprop.shared.rbac import requires_permission
from adminprop.modules.onboarding.service import OrganizationService, get_organization_service
from adminprop.modules.onboarding.schemas import OrganizationCreate, OrganizationResponse

router = APIRouter(prefix="/v1/superadmin/organizations", tags=["superadmin"])


@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    response_model=OrganizationResponse,
    dependencies=[Depends(requires_permission("superadmin"))],
)
async def create_organization(
    dto: OrganizationCreate,
    service: OrganizationService = Depends(get_organization_service),
) -> OrganizationResponse:
    """SDD: spec_module_00_superadmin.md §RF-02."""
    org = await service.create(dto)
    return OrganizationResponse.model_validate(org)
```

```python
# service.py
from adminprop.modules.onboarding.repository import OrganizationRepository

class OrganizationService:
    def __init__(self, repo: OrganizationRepository) -> None:
        self._repo = repo

    async def create(self, dto: OrganizationCreate) -> Organization:
        # RN-03: slug es inmutable post-creación, validamos formato y unicidad
        self._validate_slug_format(dto.slug)
        if await self._repo.slug_exists(dto.slug):
            raise SlugAlreadyTakenException(suggestions=await self._suggest_slugs(dto.slug))

        return await self._repo.insert(dto)
```

```python
# repository.py
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from adminprop.modules.onboarding.models import Organization

class OrganizationRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def slug_exists(self, slug: str) -> bool:
        stmt = select(Organization).where(Organization.slug == slug)
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none() is not None

    async def insert(self, dto: OrganizationCreate) -> Organization:
        org = Organization(
            slug=dto.slug,
            name=dto.name,
            tipo_organizacion=dto.tipo_organizacion,
            plan=dto.plan,
            timezone=dto.timezone,
        )
        self._session.add(org)
        await self._session.flush()   # genera id sin commit (commit lo maneja el middleware)
        return org
```

### Registro del módulo en la app

```python
# src/adminprop/main.py
from fastapi import FastAPI
from adminprop.modules.onboarding.router import router as onboarding_router
from adminprop.modules.contracts.router import router as contracts_router
# ...

app = FastAPI(title="AdminProp API", version="1.0.0")

app.include_router(onboarding_router)
app.include_router(contracts_router)
# ...
```

El prefijo `/v1` se declara en el `APIRouter(prefix=...)` de cada módulo, no en `app.include_router`.

### Convenciones de nombres dentro del módulo

| Artefacto | Convención | Ejemplo |
|---|---|---|
| Carpeta del módulo | `snake_case` | `modules/work_orders/` |
| Path REST | `kebab-case` plural | `/v1/work-orders` |
| Modelos SQLAlchemy | `PascalCase` singular | `class WorkOrder(Base)` |
| Tablas DB | `snake_case` plural | `work_orders` |
| Schemas Pydantic | `PascalCase` singular con sufijo de propósito | `WorkOrderCreate`, `WorkOrderResponse`, `WorkOrderListResponse` |
| Service | `<Module>Service` | `WorkOrderService` |
| Repository | `<Module>Repository` | `WorkOrderRepository` |
| Excepciones | `<Caso>Exception` | `WorkOrderAlreadyClosedException` |

## Template

Plantilla completa para crear un módulo nuevo:

```python
# src/adminprop/modules/<module_name>/__init__.py
"""<Module> module — <descripción breve, link al SDD>."""

from adminprop.modules.<module_name>.router import router

__all__ = ["router"]
```

```python
# src/adminprop/modules/<module_name>/schemas.py
"""Pydantic schemas — PascalCase singular."""

from datetime import datetime
from uuid import UUID
from pydantic import BaseModel, ConfigDict, Field


class <Resource>Create(BaseModel):
    """Body del POST. Sólo campos que el cliente provee."""

    name: str = Field(..., min_length=1, max_length=255)
    # ...


class <Resource>Response(BaseModel):
    """Shape del response. Refleja la fila de DB + relaciones expandidas."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    created_at: datetime
    # ...


class <Resource>ListResponse(BaseModel):
    """Paginación cursor-based (sdd_03 §convenciones)."""

    data: list[<Resource>Response]
    meta: dict   # { "next_cursor": "...", "limit": 20 }
```

```python
# src/adminprop/modules/<module_name>/models.py
"""SQLAlchemy 2.0 models — PascalCase singular."""

from datetime import datetime
from uuid import UUID, uuid4
from sqlalchemy import String, ForeignKey, Index, text
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from adminprop.db.base import Base


class <Resource>(Base):
    __tablename__ = "<resource>s"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    organization_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(server_default=text("now()"))
    updated_at: Mapped[datetime] = mapped_column(server_default=text("now()"), onupdate=text("now()"))

    __table_args__ = (
        Index("idx_<resource>_organization_id", "organization_id"),
    )
```

```python
# src/adminprop/modules/<module_name>/repository.py
from uuid import UUID
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from fastapi import Depends

from adminprop.db.session import get_db_session
from adminprop.modules.<module_name>.models import <Resource>


class <Resource>Repository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_by_id(self, resource_id: UUID, organization_id: UUID) -> <Resource> | None:
        """RN-D01: filtro explícito por organization_id; RLS es la segunda capa."""
        stmt = select(<Resource>).where(
            <Resource>.id == resource_id,
            <Resource>.organization_id == organization_id,
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()


def get_<resource>_repository(
    session: AsyncSession = Depends(get_db_session),
) -> <Resource>Repository:
    return <Resource>Repository(session)
```

```python
# src/adminprop/modules/<module_name>/service.py
from uuid import UUID
from fastapi import Depends

from adminprop.modules.<module_name>.repository import (
    <Resource>Repository,
    get_<resource>_repository,
)


class <Resource>Service:
    def __init__(self, repo: <Resource>Repository) -> None:
        self._repo = repo

    async def get(self, resource_id: UUID, organization_id: UUID) -> <Resource> | None:
        return await self._repo.get_by_id(resource_id, organization_id)


def get_<resource>_service(
    repo: <Resource>Repository = Depends(get_<resource>_repository),
) -> <Resource>Service:
    return <Resource>Service(repo)
```

```python
# src/adminprop/modules/<module_name>/router.py
from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException, status

from adminprop.shared.tenant import get_current_tenant
from adminprop.shared.rbac import requires_permission
from adminprop.modules.<module_name>.schemas import <Resource>Response
from adminprop.modules.<module_name>.service import (
    <Resource>Service,
    get_<resource>_service,
)


router = APIRouter(prefix="/v1/<resource>s", tags=["<resource>s"])


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
    """SDD: <ruta-del-SDD>.md §<sección>."""
    resource = await service.get(resource_id, organization_id)
    if resource is None:
        # RN-D01: 404, no 403 (no revela existencia cross-tenant)
        raise HTTPException(status_code=404, detail={"code": "NOT_FOUND"})
    return <Resource>Response.model_validate(resource)
```

## Checklist pre-commit

- [ ] La carpeta del módulo está en `src/adminprop/modules/<snake_case>/`.
- [ ] Existen los 6 archivos canónicos: `__init__.py`, `router.py`, `service.py`, `repository.py`, `schemas.py`, `models.py` (más `exceptions.py` si el módulo define códigos de error de dominio).
- [ ] `router.py` no contiene SQL ni lógica de negocio (sólo orquesta llamadas al service).
- [ ] `service.py` no contiene SQL (usa el repository).
- [ ] `repository.py` filtra **explícitamente** por `organization_id` en cada query.
- [ ] Los nombres siguen la tabla de convenciones (snake_case para módulos y tablas, PascalCase para modelos/schemas/excepciones, kebab-case para paths).
- [ ] El router se importa en `src/adminprop/main.py`.
- [ ] El módulo tiene su carpeta `tests/` con un test de aislamiento multi-tenant.

## Antipatrones

```python
# ❌ Lógica de negocio en el router
@router.post("/organizations")
async def create_org(dto: OrganizationCreate):
    if dto.slug.startswith("-") or "-" * 2 in dto.slug:  # ¡RN-03!
        raise HTTPException(400, "Invalid slug")
    if await db.execute(select(Organization).where(Organization.slug == dto.slug)).scalar():
        raise HTTPException(409, "Slug taken")
    # ...

# ✅ Router delega al service; service implementa la RN-XX
@router.post("/organizations")
async def create_org(
    dto: OrganizationCreate,
    service: OrganizationService = Depends(get_organization_service),
):
    """SDD: spec_module_00_superadmin.md §RF-02. Enforces RN-03."""
    org = await service.create(dto)
    return OrganizationResponse.model_validate(org)
```

```python
# ❌ SQL directo en el service
class OrganizationService:
    async def create(self, dto):
        await db.execute(
            "INSERT INTO organizations (slug, name) VALUES ($1, $2)",
            dto.slug, dto.name,
        )
# El service no debería conocer el modelo de DB.

# ✅ Service usa el repository
class OrganizationService:
    def __init__(self, repo: OrganizationRepository) -> None:
        self._repo = repo

    async def create(self, dto: OrganizationCreate) -> Organization:
        await self._validate_slug(dto.slug)   # RN-03
        return await self._repo.insert(dto)
```

```python
# ❌ Repository sin filtro de organization_id
class WorkOrderRepository:
    async def get_by_id(self, work_order_id: UUID) -> WorkOrder | None:
        stmt = select(WorkOrder).where(WorkOrder.id == work_order_id)
        return (await self._session.execute(stmt)).scalar_one_or_none()
# Depende sólo de RLS. Si el middleware no seteó app.current_tenant_id, leak.

# ✅ Repository filtra explícitamente — defense in depth
class WorkOrderRepository:
    async def get_by_id(self, work_order_id: UUID, organization_id: UUID) -> WorkOrder | None:
        stmt = select(WorkOrder).where(
            WorkOrder.id == work_order_id,
            WorkOrder.organization_id == organization_id,   # RN-D01
        )
        return (await self._session.execute(stmt)).scalar_one_or_none()
```

```python
# ❌ Schemas con sufijos inconsistentes
class OrganizationDto(BaseModel): ...
class OrganizationDTOResponse(BaseModel): ...
class OrgInDB(BaseModel): ...

# ✅ Convención uniforme
class OrganizationCreate(BaseModel): ...      # body de POST
class OrganizationUpdate(BaseModel): ...      # body de PATCH
class OrganizationResponse(BaseModel): ...    # shape del response
class OrganizationListResponse(BaseModel): ... # response paginado
```

```python
# ❌ Modelo sin organization_id en una tabla tenant-scoped
class WorkOrder(Base):
    __tablename__ = "work_orders"
    id: Mapped[UUID] = mapped_column(...)
    description: Mapped[str] = mapped_column(...)
    # ¡Falta organization_id! RLS no puede aplicarse.

# ✅ Toda tabla tenant-scoped tiene organization_id NOT NULL + FK
class WorkOrder(Base):
    __tablename__ = "work_orders"
    id: Mapped[UUID] = mapped_column(...)
    organization_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
    )
    description: Mapped[str] = mapped_column(...)
```

## Referencias

- Backend `CLAUDE.md` §4 "Componentes" — diagrama de FastAPI App + módulos + workers.
- Backend `CLAUDE.md` §9 "Estructura del repositorio" — tree de referencia con `modules/`, `workers/`, `shared/`, `db/`.
- `core/sdd_03_api_contracts.md` §"Convenciones Generales" — prefijo `/v1`, paginación cursor-based, shape `{ data, meta }`.
- `core/sdd_02_domain_model.md` §3 "RN-D01" — los datos de un tenant nunca son accesibles desde otro; el repository es la última defensa antes de RLS.
- `infrastructure/spec_data_model.md` §Apéndice A — convenciones de nomenclatura (DB snake_case plural, Pydantic PascalCase singular).
