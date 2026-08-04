# tenant-isolation

## Cuándo leer este skill

Leer **antes de**:

- Escribir cualquier query que toque una tabla con `organization_id`.
- Implementar un endpoint que retorne datos de un tenant.
- Configurar el middleware de FastAPI o el pool de conexiones.
- Implementar una query con joins o agregaciones sobre varias tablas tenant-scoped.
- Cualquier operación del Super Admin (`/superadmin/*`).

Este skill define los **invariantes absolutos** del aislamiento multi-tenant. Romperlos es un breach de seguridad inter-organizacional.

## Stack relevante

| Capa | Tecnología | Fuente |
|---|---|---|
| Modelo multi-tenant | Shared schema con `organization_id` en cada tabla tenant-scoped | backend `CLAUDE.md` §4 |
| Aislamiento físico | Row-Level Security (RLS) PostgreSQL | backend `CLAUDE.md` §4 |
| Contexto de tenant | `SET LOCAL app.current_tenant_id = '<jwt.org>'` en cada request | backend `CLAUDE.md` §4 |
| Roles DB | `adminprop_app` (default, sujeto a RLS) + `adminprop_superadmin` (BYPASSRLS) | backend `CLAUDE.md` §3, `_index.md` #42 |
| Conexión | PgBouncer transaction-scoped | backend `CLAUDE.md` §3 |
| Auditoría Super Admin | `superadmin_audit_logs` separado | backend `CLAUDE.md` §3 |

## SDDs de referencia

- `core/sdd_02_domain_model.md` §3 RN-D01 — "Los datos de un tenant nunca son accesibles desde otro tenant".
- `core/sdd_04_nonfunctional.md` §2.1 — modelo de amenazas "Acceso cross-tenant" — vector + mitigaciones.
- `core/sdd_04_nonfunctional.md` §2.3 — autorización + RLS.
- `infrastructure/spec_data_model.md` §"Principios Arquitectónicos" — implementación canónica de RLS.
- `core/spec_module_00_superadmin.md` §RN-01/RN-06 — Super Admin no tiene `org_id`; opera con rol `adminprop_superadmin`.
- `_index.md` §4 #42 — decisión sobre el rol `adminprop_superadmin`.

## El patrón

### Las cinco invariantes

```
1. Todo tabla con organization_id tiene RLS habilitado + política + FORCE.
2. Toda request HTTP setea app.current_tenant_id antes de la primera query.
3. organization_id se extrae del JWT — nunca de body/path/query.
4. Toda query del repository filtra explícitamente por organization_id
   (defense in depth — RLS es la segunda capa, no la primera).
5. Acceso cross-tenant retorna 404, no 403 (no revela existencia).
```

### Setear el contexto del tenant por request

El middleware FastAPI lo hace al inicio. **Antes** de cualquier query.

```python
# src/adminprop/shared/tenant/middleware.py
from uuid import UUID
from fastapi import Request
from sqlalchemy import text

from adminprop.db.session import async_session_factory
from adminprop.shared.auth import decode_jwt_from_cookie_or_header, JWTPayload


async def tenant_middleware(request: Request, call_next):
    """
    Setear app.current_tenant_id al inicio de cada request, antes
    de cualquier query a una tabla tenant-scoped.

    SDD: backend CLAUDE.md §4, sdd_02 §3 RN-D01.
    """
    # 1. Decodificar el JWT (de la HttpOnly cookie o del header Authorization)
    payload: JWTPayload | None = await decode_jwt_from_cookie_or_header(request)

    # 2. Adjuntar la sesión DB + setear el contexto
    if payload is not None:
        async with async_session_factory() as session:
            if payload.is_super_admin:
                # Super Admin: cambiar el rol de DB a adminprop_superadmin (BYPASSRLS).
                # PgBouncer es transaction-scoped → SET ROLE vive en la transacción.
                await session.execute(text("SET ROLE adminprop_superadmin"))
                request.state.is_super_admin = True
                request.state.organization_id = None
            else:
                # Usuario regular: rol adminprop_app (default) + setear tenant_id.
                await session.execute(
                    text("SELECT set_config('app.current_tenant_id', :tid, true)"),
                    {"tid": str(payload.org_id)},
                )
                request.state.is_super_admin = False
                request.state.organization_id = payload.org_id

            request.state.db_session = session
            response = await call_next(request)
            return response

    # Sin JWT: queda al endpoint decidir (public endpoints no requieren contexto)
    return await call_next(request)
```

> En la práctica, la sesión DB suele inyectarse vía `Depends(get_db_session)` (no en `request.state`), pero el patrón conceptual es el mismo: **el contexto se setea antes de cualquier query**.

### Filtro explícito en cada query del repository

```python
# src/adminprop/modules/payments/repository.py
from uuid import UUID
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from adminprop.modules.payments.models import Payment


class PaymentRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_by_id(self, payment_id: UUID, organization_id: UUID) -> Payment | None:
        """
        RN-D01 enforcement.
        Filtro explícito (defense in depth) + RLS = doble capa.
        """
        stmt = select(Payment).where(
            Payment.id == payment_id,
            Payment.organization_id == organization_id,    # ← filtro EXPLÍCITO
            Payment.deleted_at.is_(None),
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def list(self, organization_id: UUID, cursor: str | None, limit: int) -> list[Payment]:
        stmt = (
            select(Payment)
            .where(Payment.organization_id == organization_id)
            .where(Payment.deleted_at.is_(None))
            .order_by(Payment.created_at.desc())
            .limit(limit)
        )
        if cursor:
            stmt = stmt.where(Payment.created_at < decode_cursor(cursor))
        result = await self._session.execute(stmt)
        return list(result.scalars().all())
```

### Queries con join/agregación: filtrar `organization_id` antes de combinar tablas

Cualquier query que combine varias tablas tenant-scoped (joins, subqueries, agregaciones) debe filtrar `organization_id` **en cada tabla involucrada**, no confiar en que el filtro de una sola tabla "arrastre" al resto.

```python
# src/adminprop/modules/maintenance/repository.py
from uuid import UUID
from sqlalchemy import text


class WorkOrderRepository:
    async def list_assigned_to_maintenance_user(
        self,
        organization_id: UUID,
        user_id: UUID,
    ) -> list[dict]:
        """
        RN-A: el rol `maintenance` sólo ve órdenes de trabajo asignadas
        a sí mismo, dentro de su propia organización.
        Filtro EXPLÍCITO de organization_id en ambas tablas del join.
        """
        sql = """
            SELECT
                wo.id AS work_order_id,
                wo.status,
                wo.property_id,
                q.amount AS quote_amount
            FROM work_orders wo
            LEFT JOIN work_order_quotes q ON q.work_order_id = wo.id
                AND q.organization_id = :org_id
            WHERE wo.organization_id = :org_id
              AND wo.assigned_to_user_id = :user_id
              AND wo.deleted_at IS NULL
            ORDER BY wo.created_at DESC
        """
        result = await self._session.execute(
            text(sql), {"org_id": str(organization_id), "user_id": str(user_id)}
        )
        return [dict(row._mapping) for row in result]
```

### Acceso cross-tenant → 404, no 403

`RN-D01` + convención de no revelar existencia → la respuesta para "recurso no existe" y "recurso pertenece a otro tenant" es **idéntica**: 404 NOT_FOUND. Esto se enforza por el filtro explícito del repository (devuelve `None` en ambos casos) + el handler del router que mapea `None` a 404.

```python
# src/adminprop/modules/payments/router.py
@router.get("/{payment_id}", response_model=PaymentResponse)
async def get_payment(
    payment_id: UUID,
    organization_id: UUID = Depends(get_current_tenant),
    service: PaymentService = Depends(get_payment_service),
):
    payment = await service.get(payment_id, organization_id)
    if payment is None:
        # No diferenciamos "no existe" de "es de otro tenant".
        raise NotFoundException()   # → 404 NOT_FOUND
    return PaymentResponse.model_validate(payment)
```

### Validar que el JWT corresponde a un miembro activo del tenant

Un JWT válido con `org_id` no es suficiente: el usuario podría haber sido desactivado (`organization_members.is_active = false`) entre la emisión del JWT y la request actual. Validar la membresía activa en operaciones sensibles.

```python
# src/adminprop/shared/auth/membership.py
from uuid import UUID

from adminprop.modules.users.repository import OrganizationMemberRepository


async def verify_active_membership(
    user_id: UUID,
    organization_id: UUID,
    repo: OrganizationMemberRepository,
) -> None:
    """
    Backend CLAUDE.md §4 — "verificar que el JWT activo corresponde a un
    miembro activo del tenant antes de cualquier operación sobre datos
    del tenant".
    """
    membership = await repo.get_active(user_id, organization_id)
    if membership is None:
        # Membresía revocada, suspendida o nunca existió → tratar como no autenticado
        raise UnauthorizedException()   # → 401 UNAUTHORIZED
```

Este check no se hace en **cada** request (sería caro). Se hace al inicio de la sesión (login + refresh) y en endpoints sensibles (operaciones financieras, eliminación de datos).

### Super Admin: rol DB privilegiado

`/superadmin/*` opera bajo el rol PostgreSQL `adminprop_superadmin` que tiene atributo `BYPASSRLS`. El middleware conmuta el rol vía `SET ROLE` al detectar JWT con `is_super_admin: true`. Como PgBouncer es transaction-scoped, el cambio queda confinado a la transacción del request.

```python
# Middleware (fragmento ya mostrado arriba)
if payload.is_super_admin:
    await session.execute(text("SET ROLE adminprop_superadmin"))
    # Queries siguientes ven todos los tenants. Auditadas en superadmin_audit_logs.
```

Las queries del rol `adminprop_superadmin` se loguean en `superadmin_audit_logs` (no en `audit_logs` regular) para trazabilidad del uso del bypass.

### Storage de archivos con aislamiento per-tenant

En MVP, los archivos (comprobantes de pago, PDFs de recibos y liquidaciones, fotos de órdenes de trabajo) viven en **filesystem local vía un volumen Docker**, no en un bucket cloud. La convención de rutas replica el mismo aislamiento que exigiría un bucket per-tenant:

```python
# src/adminprop/shared/storage/local.py
from pathlib import Path

STORAGE_ROOT = Path("/data/adminprop-storage")   # volumen Docker montado


def build_tenant_path(organization_slug: str, purpose: str) -> Path:
    """
    Convención de aislamiento por tenant en MVP (filesystem local).
    Migrar a storage cloud post-infra sin cambiar esta convención de rutas.
    """
    path = STORAGE_ROOT / organization_slug / purpose
    path.mkdir(parents=True, exist_ok=True)
    return path
```

## Template

Test de aislamiento multi-tenant (obligatorio en cada módulo):

```python
# tests/integration/<modulo>/test_tenant_isolation.py
# Invariante: RN-D01

import pytest
from httpx import AsyncClient
from uuid import UUID


@pytest.mark.asyncio
class TestTenantIsolation:
    """RN-D01 enforcement: tenant A no accede a recursos del tenant B."""

    async def test_get_other_tenant_resource_returns_404(
        self,
        client: AsyncClient,
        tenant_a_jwt: str,
        tenant_b_resource_id: UUID,
    ):
        response = await client.get(
            f"/v1/<recurso>/{tenant_b_resource_id}",
            headers={"Authorization": f"Bearer {tenant_a_jwt}"},
        )
        assert response.status_code == 404
        assert response.json()["error"]["code"] == "NOT_FOUND"

    async def test_list_resources_returns_only_own_tenant(
        self,
        client: AsyncClient,
        tenant_a_jwt: str,
        tenant_a_resource_ids: list[UUID],
        tenant_b_resource_ids: list[UUID],
    ):
        response = await client.get(
            "/v1/<recurso>",
            headers={"Authorization": f"Bearer {tenant_a_jwt}"},
        )
        assert response.status_code == 200
        returned_ids = {item["id"] for item in response.json()["data"]}
        assert set(map(str, tenant_a_resource_ids)) <= returned_ids
        assert not (set(map(str, tenant_b_resource_ids)) & returned_ids)

    async def test_update_other_tenant_resource_returns_404(
        self,
        client: AsyncClient,
        tenant_a_jwt: str,
        tenant_b_resource_id: UUID,
    ):
        response = await client.patch(
            f"/v1/<recurso>/{tenant_b_resource_id}",
            json={"name": "hacked"},
            headers={"Authorization": f"Bearer {tenant_a_jwt}"},
        )
        assert response.status_code == 404
        # Confirmar también que el recurso NO se modificó (otra query)
```

## Checklist pre-commit

- [ ] Todas las tablas con `organization_id` tienen RLS habilitado + política + `FORCE ROW LEVEL SECURITY`.
- [ ] El middleware setea `app.current_tenant_id` al inicio de cada request HTTP.
- [ ] Los workers Celery setean `app.current_tenant_id` antes de cualquier query.
- [ ] `organization_id` se extrae **siempre** del JWT (`Depends(get_current_tenant)`), nunca de body/path/query.
- [ ] Todos los métodos del repository reciben `organization_id` como parámetro **y lo aplican en el WHERE**.
- [ ] Las queries con join/agregación entre tablas tenant-scoped filtran `organization_id` en **cada** tabla del join, no sólo en la principal.
- [ ] Acceso cross-tenant retorna **404 NOT_FOUND** (vía `service.get(...) is None → raise NotFoundException`), no 403.
- [ ] Cada módulo tenant-scoped tiene un test de aislamiento que verifica GET, LIST, PATCH, DELETE cross-tenant.
- [ ] El JWT se valida contra membresía activa al login + refresh + operaciones sensibles.
- [ ] `/superadmin/*` cambia al rol `adminprop_superadmin` (BYPASSRLS) y registra cada query en `superadmin_audit_logs`.

## Antipatrones

```python
# ❌ Tomar organization_id del body o path
@router.get("/payments/{org_id}/{payment_id}")
async def get_payment(org_id: UUID, payment_id: UUID):
    ...
# Cliente manipula la URL → cross-tenant leak.

# ✅ organization_id sólo del JWT
@router.get("/v1/payments/{payment_id}")
async def get_payment(
    payment_id: UUID,
    organization_id: UUID = Depends(get_current_tenant),
):
    ...
```

```python
# ❌ Query sin filtro explícito; depende sólo de RLS
async def get_by_id(self, payment_id: UUID) -> Payment | None:
    return (await self._session.execute(
        select(Payment).where(Payment.id == payment_id)
    )).scalar_one_or_none()
# Si el middleware no seteó app.current_tenant_id (bug, test, worker mal
# configurado) → query retorna la fila sin chequear tenant. Leak.

# ✅ Filtro explícito + RLS como segunda capa
async def get_by_id(self, payment_id: UUID, organization_id: UUID) -> Payment | None:
    return (await self._session.execute(
        select(Payment).where(
            Payment.id == payment_id,
            Payment.organization_id == organization_id,
        )
    )).scalar_one_or_none()
```

```python
# ❌ Join entre tablas tenant-scoped filtrando organization_id sólo en una
SELECT wo.*, q.amount
FROM work_orders wo
LEFT JOIN work_order_quotes q ON q.work_order_id = wo.id
WHERE wo.organization_id = :org_id
# q no filtra organization_id: si el modelo de datos permitiera una fila
# huérfana o mal referenciada, el join podría exponer datos de otro tenant.

# ✅ Filtro explícito en cada tabla del join
SELECT wo.*, q.amount
FROM work_orders wo
LEFT JOIN work_order_quotes q ON q.work_order_id = wo.id
    AND q.organization_id = :org_id
WHERE wo.organization_id = :org_id
```

```python
# ❌ Devolver 403 cuando el recurso es de otro tenant
payment = await db.get(Payment, payment_id)
if payment.organization_id != current_org_id:
    raise HTTPException(403, "Forbidden")
# Revela que el recurso existe → enumeration cross-tenant.

# ✅ El filtro del repository ya retorna None → 404
payment = await repo.get_by_id(payment_id, current_org_id)
if payment is None:
    raise NotFoundException()   # 404
```

```python
# ❌ Confiar en property_id del cliente para scoping de mantenimiento
@router.post("/maintenance/work-orders")
async def list_by_property(dto: WorkOrderFilterRequest):
    # dto.filters.property_id viene del cliente; un usuario `maintenance`
    # podría poner cualquier UUID de otra organización.
    orders = await repo.list(property_id=dto.filters.property_id)
    ...

# ✅ Resolver el scope de `maintenance` en backend; nunca confiar en el cliente
@router.get("/maintenance/work-orders")
async def list_assigned_work_orders(
    payload: JWTPayload = Depends(decode_jwt),
    organization_id: UUID = Depends(get_current_tenant),
):
    if "maintenance" in payload.roles:
        orders = await repo.list_assigned_to_maintenance_user(organization_id, payload.sub)
    else:
        orders = await repo.list(organization_id)   # owner/admin: sin restricción
```

```python
# ❌ Setear app.current_tenant_id sin missing_ok en la policy
# (problema en migraciones, ver database-migration.md)
CREATE POLICY payments_iso ON payments
USING (organization_id = current_setting('app.current_tenant_id')::uuid)
# Si un worker olvida set_tenant_context: error críptico en la query.

# ✅ Política con missing_ok=true → setting ausente equivale a NULL → 0 filas
CREATE POLICY payments_iso ON payments
USING (organization_id = current_setting('app.current_tenant_id', true)::uuid)
```

```python
# ❌ Permitir que adminprop_app lea audit_logs sin discriminar tenant
# adminprop_app es el rol del backend en runtime. Si la query a audit_logs
# no setea tenant, ¿qué retorna? Depende de la política — si no hay,
# cualquier query lee todo.

# ✅ audit_logs tiene RLS habilitado igual que el resto + el endpoint
# requiere permiso audit:read y filtra por organization_id explícito.
```

## Referencias

- `core/sdd_02_domain_model.md` §3 RN-D01 — el invariante absoluto.
- `core/sdd_04_nonfunctional.md` §2.1 — modelo de amenazas y mitigaciones.
- `core/sdd_04_nonfunctional.md` §2.3 — autorización + RLS.
- `infrastructure/spec_data_model.md` §"Principios Arquitectónicos" — implementación canónica de RLS con `adminprop_app` + `adminprop_superadmin`.
- `core/spec_module_00_superadmin.md` §RN-01, §RN-06 — `is_super_admin = true` sin `org_id`.
- Backend `CLAUDE.md` §4 "Aislamiento entre tenants — invariantes" — las 5 reglas que este skill formaliza.
- `_index.md` §4 #2, #3, #42 — decisiones arquitectónicas: shared schema con RLS, `organization_id` siempre del JWT, bypass del Super Admin.
