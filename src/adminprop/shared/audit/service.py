"""AuditService transversal (issue #10).

SDD: core/sdd_02_domain_model.md §2.17 "Log de Auditoria (AuditLog)" +
infrastructure/spec_data_model.md §Capa 7 "audit_logs".
Implements: RN-D03 (append-only), RN-D04 (correcciones de cobros y
            liquidaciones siempre trazadas), RN-A04 (accesos denegados
            auditados).

API deliberadamente simple (funcion, no clase con estado): `audit()`
recibe la MISMA `session` que la operacion de negocio del caller y NO
hace commit -- si esa operacion hace rollback, el INSERT del audit
tambien, porque vive en la misma transaccion (mismo criterio que
`docs/skills/module-structure.md`: el caller confirma con su propio
`repo.commit()` una vez que terminaron todas las escrituras). Usable
por cualquier modulo: solo necesita su `session` (via `repo.session`,
ver `modules/*/repository.py`) y los datos ya resueltos del contexto
(organization_id del tenant, user_id/request_id del JWT/request).

El unico caso sin transaccion de negocio es `access.denied`: las
dependencies de auth (`shared/rbac.py`, `shared/auth/dependencies.py`)
cortan el request con 403 ANTES de llegar a un service/repository, asi
que no hay una `session` de negocio para reusar. `record_access_denied`
abre su propia sesion y hace su propio `commit()`.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from adminprop.shared.audit.repository import AuditLogRepository
from adminprop.shared.logging.json_logger import request_id_var, scrub

# RN-A04: nombre de accion estandar para todo intento de acceso no autorizado.
ACCESS_DENIED_ACTION = "access.denied"


async def audit(
    session: AsyncSession,
    *,
    organization_id: UUID,
    action: str,
    entity_type: str,
    entity_id: UUID | None = None,
    before: dict | None = None,
    after: dict | None = None,
    user_id: UUID | None = None,
    request_id: str | None = None,
) -> None:
    """Registra un evento en `audit_logs`, en la transaccion de `session`.

    `before`/`after` se escrudinan (`shared/logging/json_logger.scrub`)
    antes de persistir -- las mismas `SENSITIVE_KEYS` que los logs
    (`password_hash`, tokens, `bank_info`, sdd_04 §2.4) nunca se vuelcan.
    `user_id=None` para acciones del sistema (sdd_02 §2.17). `request_id`
    se toma del contextvar de `shared/logging` si no se pasa explicito
    (`RequestContextMiddleware` lo setea en todo request HTTP).
    """
    repo = AuditLogRepository(session)
    await repo.insert(
        organization_id=organization_id,
        user_id=user_id,
        action=action,
        entity_type=entity_type,
        entity_id=entity_id,
        before_state=scrub(before) if before is not None else None,
        after_state=scrub(after) if after is not None else None,
        request_id=request_id if request_id is not None else request_id_var.get(),
    )


async def record_access_denied(
    *,
    organization_id: UUID | None,
    user_id: UUID,
    entity_type: str = "access",
    details: dict | None = None,
) -> None:
    """RN-A04: audita un intento de acceso no autorizado.

    Se ejecuta fuera de cualquier transaccion de negocio (la dependency
    de FastAPI corta el request con 403 antes de llegar a un
    service/repository) -- abre su propia sesion y hace su propio
    `commit()`, a diferencia de `audit()`.

    `organization_id=None` (JWT de Super Admin, sin `org`, que intenta
    un endpoint tenant-scoped) no puede atribuirse a ningun tenant de
    `audit_logs` (columna NOT NULL + politica RLS) -- se omite en
    silencio; ese caso queda fuera de alcance de este issue (ver
    `superadmin_audit_logs` en `docs/skills/tenant-isolation.md`, tabla
    todavia no implementada).
    """
    if organization_id is None:
        return

    # Import diferido: `db.session` importa `shared.tenant`, que a su vez
    # importa `shared.auth.dependencies` (uno de los dos callers de esta
    # funcion) -- un import a nivel de modulo aca crearia un ciclo
    # (`auth.dependencies -> audit.service -> db.session -> shared.tenant
    # -> auth.dependencies`), confirmado con
    # `python -c "import adminprop.main"` fallando con ImportError.
    from adminprop.db.session import get_session_factory, set_tenant_context

    session_factory = get_session_factory()
    async with session_factory() as session:
        await set_tenant_context(session, organization_id)
        await audit(
            session,
            organization_id=organization_id,
            action=ACCESS_DENIED_ACTION,
            entity_type=entity_type,
            after=details,
            user_id=user_id,
        )
        await session.commit()
