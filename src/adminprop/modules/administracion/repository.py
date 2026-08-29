"""Acceso a datos del modulo administracion: users, organization_members,
roles, organization_invitations, organizations (issue #9).

SDD: infrastructure/spec_data_model.md §Capa 0. core/sdd_03_api_contracts.md
§3/§4.

Mismo criterio que `modules/auth/repository.py` y
`modules/superadmin/repository.py`: SQL crudo via `text()` (estas tablas
compartidas todavia no tienen un dueno ORM comun). Toda query filtra
`organization_id` explicitamente (defense in depth sobre RLS, RN-D01),
salvo `users` (identidad global, sin RLS, issue #5) y `organizations`
(raiz del tenant, sin RLS -- igual se filtra `id` explicitamente).

Este repository opera bajo una sesion `adminprop_app` normal (via
`db.session.get_tenant_db_session`), a diferencia de
`modules/superadmin/repository.py` (BYPASSRLS): las tablas tenant-scoped
(`roles`, `organization_members`, `organization_invitations`) SI aplican
su politica RLS aca, que es exactamente el punto de este modulo (primer
consumidor con un tenant real resuelto desde el JWT, ver
`shared/tenant.py`).
"""

from __future__ import annotations

import base64
import json
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

import sqlalchemy as sa
from fastapi import Depends
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from adminprop.db.session import get_tenant_db_session


@dataclass(frozen=True)
class InvitationRow:
    id: UUID
    organization_id: UUID
    email: str
    role_id: UUID
    role_name: str
    status: str
    expires_at: datetime
    created_at: datetime


@dataclass(frozen=True)
class MemberRow:
    user_id: UUID
    email: str
    full_name: str
    role_id: UUID
    role_name: str
    status: str
    created_at: datetime


@dataclass(frozen=True)
class RoleRow:
    id: UUID
    name: str
    permissions: list[str]
    is_system_role: bool


def _parse_json_list(raw: object) -> list[str]:
    if isinstance(raw, list):
        return [str(item) for item in raw]
    if isinstance(raw, str):
        return [str(item) for item in json.loads(raw)]
    return []


def _parse_settings(raw: object) -> dict:
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        return json.loads(raw)
    return {}


def _encode_cursor(created_at: datetime, row_id: UUID) -> str:
    raw = f"{created_at.isoformat()}|{row_id}"
    return base64.urlsafe_b64encode(raw.encode("utf-8")).decode("ascii")


def _decode_cursor(cursor: str) -> tuple[datetime, UUID]:
    raw = base64.urlsafe_b64decode(cursor.encode("ascii")).decode("utf-8")
    created_at_raw, row_id_raw = raw.split("|", 1)
    return datetime.fromisoformat(created_at_raw), UUID(row_id_raw)


def _row_to_invitation(row: sa.RowMapping) -> InvitationRow:
    return InvitationRow(
        id=row["id"],
        organization_id=row["organization_id"],
        email=row["email"],
        role_id=row["role_id"],
        role_name=row["role_name"],
        status=row["status"],
        expires_at=row["expires_at"],
        created_at=row["created_at"],
    )


class AdministracionRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    @property
    def session(self) -> AsyncSession:
        """Expuesto para que `service.py` pueda pasar la MISMA sesion a
        `AuditService.audit()` (issue #10) -- el evento de auditoria debe
        persistirse en la misma transaccion que la operacion de negocio,
        confirmada junto con ella por `commit()`."""
        return self._session

    # ─── users (identidad global, sin RLS) ─────────────────────────────

    async def get_user_id_by_email(self, email: str) -> UUID | None:
        stmt = text(
            "SELECT id FROM users WHERE LOWER(email) = LOWER(:email) AND deleted_at IS NULL"
        )
        result = await self._session.execute(stmt, {"email": email})
        row = result.first()
        return row[0] if row is not None else None

    # ─── organization_members ───────────────────────────────────────────

    async def get_membership_status(self, organization_id: UUID, user_id: UUID) -> str | None:
        """`None` si el user global no tiene ninguna fila de membresia
        (activa o inactiva) en `organization_id` -- usado para decidir
        `USER_ALREADY_MEMBER` (RF-01), mismo criterio que
        `modules/auth/repository.py.get_membership_status`."""
        stmt = text(
            "SELECT status FROM organization_members "
            "WHERE organization_id = :organization_id AND user_id = :user_id"
        )
        result = await self._session.execute(
            stmt, {"organization_id": str(organization_id), "user_id": str(user_id)}
        )
        row = result.first()
        return row[0] if row is not None else None

    async def get_member(self, organization_id: UUID, user_id: UUID) -> MemberRow | None:
        stmt = text(
            """
            SELECT u.id AS user_id, u.email, u.full_name, r.id AS role_id,
                   r.name AS role_name, m.status, m.created_at
            FROM organization_members m
            JOIN users u ON u.id = m.user_id
            JOIN roles r ON r.id = m.role_id AND r.organization_id = m.organization_id
            WHERE m.organization_id = :organization_id AND m.user_id = :user_id
            """
        )
        result = await self._session.execute(
            stmt, {"organization_id": str(organization_id), "user_id": str(user_id)}
        )
        row = result.mappings().first()
        if row is None:
            return None
        return MemberRow(**row)

    async def list_members(
        self, *, organization_id: UUID, cursor: str | None, limit: int
    ) -> tuple[list[MemberRow], str | None]:
        """RF-02: `GET /users` -- miembros de la organizacion, cursor-based."""
        conditions = ["m.organization_id = :organization_id"]
        params: dict[str, object] = {"organization_id": str(organization_id), "limit": limit + 1}
        if cursor:
            cursor_created_at, cursor_id = _decode_cursor(cursor)
            conditions.append("(m.created_at, m.user_id) < (:cursor_created_at, :cursor_id)")
            params["cursor_created_at"] = cursor_created_at
            params["cursor_id"] = str(cursor_id)

        where_clause = " AND ".join(conditions)
        stmt = text(
            f"""
            SELECT u.id AS user_id, u.email, u.full_name, r.id AS role_id,
                   r.name AS role_name, m.status, m.created_at
            FROM organization_members m
            JOIN users u ON u.id = m.user_id
            JOIN roles r ON r.id = m.role_id AND r.organization_id = m.organization_id
            WHERE {where_clause}
            ORDER BY m.created_at DESC, m.user_id DESC
            LIMIT :limit
            """
        )
        result = await self._session.execute(stmt, params)
        rows = result.mappings().all()

        has_more = len(rows) > limit
        page = rows[:limit]
        next_cursor = (
            _encode_cursor(page[-1]["created_at"], page[-1]["user_id"])
            if has_more and page
            else None
        )
        return [MemberRow(**row) for row in page], next_cursor

    async def count_active_owners_locked(self, organization_id: UUID) -> int:
        """RN-A03/RN-02 (`LAST_OWNER_REQUIRED`): `SELECT ... FOR UPDATE` --
        lockea las filas de `organization_members` con rol `owner` activo
        de la organizacion, serializando a nivel app contra un
        DELETE/PATCH concurrente del otro owner (mismo patron que
        `modules/superadmin/repository.get_pending_owner_invitation_for_update`)."""
        stmt = text(
            """
            SELECT m.id
            FROM organization_members m
            JOIN roles r ON r.id = m.role_id AND r.organization_id = m.organization_id
            WHERE m.organization_id = :organization_id
              AND m.status = 'active'
              AND r.name = 'owner'
            FOR UPDATE
            """
        )
        result = await self._session.execute(stmt, {"organization_id": str(organization_id)})
        return len(result.all())

    async def update_member_role(
        self, *, organization_id: UUID, user_id: UUID, role_id: UUID
    ) -> None:
        stmt = text(
            "UPDATE organization_members SET role_id = :role_id, updated_at = now() "
            "WHERE organization_id = :organization_id AND user_id = :user_id"
        )
        await self._session.execute(
            stmt,
            {
                "role_id": str(role_id),
                "organization_id": str(organization_id),
                "user_id": str(user_id),
            },
        )

    async def deactivate_member(self, *, organization_id: UUID, user_id: UUID) -> None:
        """RF-02: soft -- `status = 'inactive'` (nunca se borra la fila)."""
        stmt = text(
            "UPDATE organization_members SET status = 'inactive', updated_at = now() "
            "WHERE organization_id = :organization_id AND user_id = :user_id"
        )
        await self._session.execute(
            stmt, {"organization_id": str(organization_id), "user_id": str(user_id)}
        )

    # ─── roles ──────────────────────────────────────────────────────────

    async def get_role_id_by_name(self, organization_id: UUID, role_name: str) -> UUID | None:
        stmt = text(
            "SELECT id FROM roles WHERE organization_id = :organization_id AND name = :name"
        )
        result = await self._session.execute(
            stmt, {"organization_id": str(organization_id), "name": role_name}
        )
        row = result.first()
        return row[0] if row is not None else None

    async def list_roles(self, organization_id: UUID) -> list[RoleRow]:
        """RF-03: `GET /roles` -- los 3 roles de sistema de la organizacion."""
        stmt = text(
            "SELECT id, name, permissions, is_system_role FROM roles "
            "WHERE organization_id = :organization_id ORDER BY name"
        )
        result = await self._session.execute(stmt, {"organization_id": str(organization_id)})
        rows = result.mappings().all()
        return [
            RoleRow(
                id=row["id"],
                name=row["name"],
                permissions=_parse_json_list(row["permissions"]),
                is_system_role=bool(row["is_system_role"]),
            )
            for row in rows
        ]

    # ─── organization_invitations ───────────────────────────────────────

    async def get_pending_invitation_by_email(
        self, organization_id: UUID, email: str
    ) -> InvitationRow | None:
        """RF-01: invariante "una invitacion `pending` por (organization_id,
        email)" -- a diferencia de `modules/superadmin` (una sola pending
        de owner por organizacion), aca conviven varias invitaciones
        pending a distintos emails."""
        stmt = text(
            """
            SELECT oi.id, oi.organization_id, oi.email, oi.role_id,
                   r.name AS role_name, oi.status, oi.expires_at, oi.created_at
            FROM organization_invitations oi
            JOIN roles r ON r.id = oi.role_id AND r.organization_id = oi.organization_id
            WHERE oi.organization_id = :organization_id
              AND LOWER(oi.email) = LOWER(:email)
              AND oi.status = 'pending'
            """
        )
        result = await self._session.execute(
            stmt, {"organization_id": str(organization_id), "email": email}
        )
        row = result.mappings().first()
        return _row_to_invitation(row) if row is not None else None

    async def get_pending_invitation_by_id(
        self, organization_id: UUID, invitation_id: UUID
    ) -> InvitationRow | None:
        """RN-D01: filtra `organization_id` explicitamente -- una
        invitacion de otra organizacion, o que ya no este `pending`, no
        se distingue de "no existe" (404), mismo criterio que
        `InvitationNotFoundException` en accept-invitation."""
        stmt = text(
            """
            SELECT oi.id, oi.organization_id, oi.email, oi.role_id,
                   r.name AS role_name, oi.status, oi.expires_at, oi.created_at
            FROM organization_invitations oi
            JOIN roles r ON r.id = oi.role_id AND r.organization_id = oi.organization_id
            WHERE oi.organization_id = :organization_id
              AND oi.id = :invitation_id
              AND oi.status = 'pending'
            """
        )
        result = await self._session.execute(
            stmt,
            {"organization_id": str(organization_id), "invitation_id": str(invitation_id)},
        )
        row = result.mappings().first()
        return _row_to_invitation(row) if row is not None else None

    async def list_pending_invitations(
        self, *, organization_id: UUID, cursor: str | None, limit: int
    ) -> tuple[list[InvitationRow], str | None]:
        """RF-01: "listado de invitaciones pendientes" (texto literal del
        SDD) -- filtra `status = 'pending'`."""
        conditions = ["oi.organization_id = :organization_id", "oi.status = 'pending'"]
        params: dict[str, object] = {"organization_id": str(organization_id), "limit": limit + 1}
        if cursor:
            cursor_created_at, cursor_id = _decode_cursor(cursor)
            conditions.append("(oi.created_at, oi.id) < (:cursor_created_at, :cursor_id)")
            params["cursor_created_at"] = cursor_created_at
            params["cursor_id"] = str(cursor_id)

        where_clause = " AND ".join(conditions)
        stmt = text(
            f"""
            SELECT oi.id, oi.organization_id, oi.email, oi.role_id,
                   r.name AS role_name, oi.status, oi.expires_at, oi.created_at
            FROM organization_invitations oi
            JOIN roles r ON r.id = oi.role_id AND r.organization_id = oi.organization_id
            WHERE {where_clause}
            ORDER BY oi.created_at DESC, oi.id DESC
            LIMIT :limit
            """
        )
        result = await self._session.execute(stmt, params)
        rows = result.mappings().all()

        has_more = len(rows) > limit
        page = rows[:limit]
        next_cursor = (
            _encode_cursor(page[-1]["created_at"], page[-1]["id"]) if has_more and page else None
        )
        return [_row_to_invitation(row) for row in page], next_cursor

    async def revoke_invitation(self, invitation_id: UUID) -> None:
        stmt = text(
            "UPDATE organization_invitations SET status = 'revoked', updated_at = now() "
            "WHERE id = :id"
        )
        await self._session.execute(stmt, {"id": str(invitation_id)})

    async def create_invitation(
        self,
        *,
        organization_id: UUID,
        email: str,
        role_id: UUID,
        token_hash: str,
        expires_at: datetime,
    ) -> InvitationRow:
        stmt = text(
            """
            WITH inserted AS (
                INSERT INTO organization_invitations
                    (organization_id, email, role_id, token, status, expires_at)
                VALUES (:organization_id, :email, :role_id, :token_hash, 'pending', :expires_at)
                RETURNING id, organization_id, email, role_id, status, expires_at, created_at
            )
            SELECT inserted.id, inserted.organization_id, inserted.email, inserted.role_id,
                   r.name AS role_name, inserted.status, inserted.expires_at, inserted.created_at
            FROM inserted
            JOIN roles r ON r.id = inserted.role_id
            """
        )
        result = await self._session.execute(
            stmt,
            {
                "organization_id": str(organization_id),
                "email": email,
                "role_id": str(role_id),
                "token_hash": token_hash,
                "expires_at": expires_at,
            },
        )
        row = result.mappings().one()
        return _row_to_invitation(row)

    # ─── organizations (settings, RF-04) ────────────────────────────────

    async def get_organization_settings(self, organization_id: UUID) -> dict | None:
        stmt = text(
            "SELECT settings FROM organizations WHERE id = :organization_id AND deleted_at IS NULL"
        )
        result = await self._session.execute(stmt, {"organization_id": str(organization_id)})
        row = result.first()
        if row is None:  # pragma: no cover -- defensivo, la org del JWT siempre existe
            return None
        return _parse_settings(row[0])

    async def update_organization_settings(
        self, organization_id: UUID, settings: dict
    ) -> dict | None:
        """RF-04: `UPDATE ... WHERE id = :org_id` -- filtro explicito por
        organizacion aunque `organizations` no tenga RLS (defense in
        depth, mismo criterio que el resto del modulo)."""
        stmt = text(
            """
            UPDATE organizations
            SET settings = :settings, updated_at = now()
            WHERE id = :organization_id AND deleted_at IS NULL
            RETURNING settings
            """
        ).bindparams(sa.bindparam("settings", type_=sa.JSON))
        result = await self._session.execute(
            stmt,
            {
                "organization_id": str(organization_id),
                # Issue #116: `type_=sa.JSON` ya serializa el valor Python a
                # JSON -- pasarle `json.dumps(settings)` (un dict ya
                # convertido a str) lo serializaba UNA SEGUNDA VEZ, dejando
                # la columna JSONB con un escalar string en vez de un
                # objeto (mismo bug que `superadmin/repository.py`). Pasar
                # el dict crudo.
                "settings": settings,
            },
        )
        row = result.first()
        if row is None:  # pragma: no cover -- defensivo, la org del JWT siempre existe
            return None
        return _parse_settings(row[0])

    async def commit(self) -> None:
        await self._session.commit()


def get_administracion_repository(
    session: AsyncSession = Depends(get_tenant_db_session),
) -> AdministracionRepository:
    return AdministracionRepository(session)
