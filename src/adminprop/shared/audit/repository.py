"""Acceso a datos de `audit_logs` (issue #10).

SDD: infrastructure/spec_data_model.md §Capa 7 "audit_logs".

Tabla append-only (RN-D03): este repository solo expone un metodo de
escritura (`insert`) -- no hay `update`/`delete` a proposito, ademas de
que el rol `adminprop_app` no tiene esos permisos a nivel de PostgreSQL
(ver la migracion `20260814_190741_create_audit_logs.py`, REVOKE
UPDATE/DELETE).
"""

from __future__ import annotations

from uuid import UUID

import sqlalchemy as sa
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

# `none_as_null=True`: sin esto, el bind processor de `sa.JSON` serializa
# un valor Python `None` como el literal JSON `'null'` (texto), no como
# SQL NULL -- verificado contra SQLAlchemy 2.0.52 real. `before_state`/
# `after_state` deben quedar NULL de verdad cuando no aplican (ej:
# `access.denied` no tiene `before`).
_NULLABLE_JSON = sa.JSON(none_as_null=True)


class AuditLogRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def insert(
        self,
        *,
        organization_id: UUID,
        user_id: UUID | None,
        action: str,
        entity_type: str,
        entity_id: UUID | None,
        before_state: dict | None,
        after_state: dict | None,
        request_id: str | None,
    ) -> None:
        """RN-D01: filtro explicito de `organization_id` (la politica RLS
        `WITH CHECK` tambien lo exige) -- INSERT unicamente, nunca
        UPDATE/DELETE (RN-D03)."""
        stmt = text(
            """
            INSERT INTO audit_logs
                (organization_id, user_id, action, entity_type, entity_id,
                 before_state, after_state, request_id)
            VALUES
                (:organization_id, :user_id, :action, :entity_type, :entity_id,
                 :before_state, :after_state, :request_id)
            """
        ).bindparams(
            sa.bindparam("before_state", type_=_NULLABLE_JSON),
            sa.bindparam("after_state", type_=_NULLABLE_JSON),
        )
        await self._session.execute(
            stmt,
            {
                "organization_id": str(organization_id),
                "user_id": str(user_id) if user_id is not None else None,
                "action": action,
                "entity_type": entity_type,
                "entity_id": str(entity_id) if entity_id is not None else None,
                # Dict/None crudo: el bind processor de `_NULLABLE_JSON` ya
                # serializa a JSON -- pre-serializar aca con json.dumps()
                # produciria doble-encoding (string JSON dentro de JSONB).
                "before_state": before_state,
                "after_state": after_state,
                "request_id": request_id,
            },
        )
