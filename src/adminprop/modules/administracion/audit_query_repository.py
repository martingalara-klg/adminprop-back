"""Lectura de `audit_logs` para el visor de auditoria (issue #32).

SDD: docs/sdd/features/spec_module_07_administracion.md §RF-05 +
core/sdd_03_api_contracts.md §16 "Audit Logs" (paginacion EXCEPCIONAL
`page`/`page_size` -- la unica excepcion de `sdd_03` §Paginacion al resto
de la API, que es cursor-based) + core/sdd_02_domain_model.md §2.17
"Log de Auditoria (AuditLog)".

Repository de SOLO LECTURA: el unico camino de escritura de `audit_logs`
es `shared/audit/service.py.audit()` (RN-D03, append-only -- el rol
`adminprop_app` ni siquiera tiene UPDATE/DELETE sobre la tabla, ver la
migracion `20260814_190741_create_audit_logs.py`). Filtra
`organization_id` explicitamente (RN-D01, defense in depth sobre RLS) en
cada query, mismo criterio que `AdministracionRepository`.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

import sqlalchemy as sa
from fastapi import Depends
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from adminprop.db.session import get_tenant_db_session


@dataclass(frozen=True)
class AuditLogRow:
    id: UUID
    organization_id: UUID
    user_id: UUID | None
    user_email: str | None
    action: str
    entity_type: str
    entity_id: UUID | None
    before_state: dict | list | None
    after_state: dict | list | None
    request_id: str | None
    created_at: datetime


def _row_to_audit_log(row: sa.RowMapping) -> AuditLogRow:
    return AuditLogRow(**dict(row))


class AuditLogQueryRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_entries(
        self,
        *,
        organization_id: UUID,
        page: int,
        page_size: int,
        entity_type: str | None = None,
        entity_id: UUID | None = None,
        user_id: UUID | None = None,
        action: str | None = None,
        date_from: datetime | None = None,
        date_to: datetime | None = None,
    ) -> tuple[list[AuditLogRow], int]:
        """RF-05 / CA-07-06: filtros por entidad, usuario, accion y rango
        de fechas; paginacion `page`/`page_size` (excepcion de `sdd_03`
        §16). Devuelve la pagina pedida + el total de filas que matchean
        los filtros (para `meta.total` de la respuesta)."""
        conditions = ["a.organization_id = :organization_id"]
        params: dict[str, object] = {"organization_id": str(organization_id)}

        if entity_type is not None:
            conditions.append("a.entity_type = :entity_type")
            params["entity_type"] = entity_type
        if entity_id is not None:
            conditions.append("a.entity_id = :entity_id")
            params["entity_id"] = str(entity_id)
        if user_id is not None:
            conditions.append("a.user_id = :user_id")
            params["user_id"] = str(user_id)
        if action is not None:
            conditions.append("a.action = :action")
            params["action"] = action
        if date_from is not None:
            conditions.append("a.created_at >= :date_from")
            params["date_from"] = date_from
        if date_to is not None:
            conditions.append("a.created_at <= :date_to")
            params["date_to"] = date_to

        where_clause = " AND ".join(conditions)

        count_stmt = text(f"SELECT COUNT(*) FROM audit_logs a WHERE {where_clause}")
        total = (await self._session.execute(count_stmt, params)).scalar_one()

        page_params: dict[str, object] = {
            **params,
            "limit": page_size,
            "offset": (page - 1) * page_size,
        }
        stmt = text(
            f"""
            SELECT a.id, a.organization_id, a.user_id, u.email AS user_email,
                   a.action, a.entity_type, a.entity_id, a.before_state,
                   a.after_state, a.request_id, a.created_at
            FROM audit_logs a
            LEFT JOIN users u ON u.id = a.user_id
            WHERE {where_clause}
            ORDER BY a.created_at DESC, a.id DESC
            LIMIT :limit OFFSET :offset
            """
        )
        result = await self._session.execute(stmt, page_params)
        rows = [_row_to_audit_log(row) for row in result.mappings().all()]
        return rows, int(total)

    async def get_by_id(self, organization_id: UUID, audit_log_id: UUID) -> AuditLogRow | None:
        """RN-D01: filtro explicito de `organization_id` -- cross-tenant y
        "no existe" son indistinguibles (404 via `NotFoundException` en
        el service, nunca 403)."""
        stmt = text(
            """
            SELECT a.id, a.organization_id, a.user_id, u.email AS user_email,
                   a.action, a.entity_type, a.entity_id, a.before_state,
                   a.after_state, a.request_id, a.created_at
            FROM audit_logs a
            LEFT JOIN users u ON u.id = a.user_id
            WHERE a.organization_id = :organization_id AND a.id = :id
            """
        )
        result = await self._session.execute(
            stmt, {"organization_id": str(organization_id), "id": str(audit_log_id)}
        )
        row = result.mappings().first()
        return _row_to_audit_log(row) if row is not None else None


def get_audit_log_query_repository(
    session: AsyncSession = Depends(get_tenant_db_session),
) -> AuditLogQueryRepository:
    return AuditLogQueryRepository(session)
