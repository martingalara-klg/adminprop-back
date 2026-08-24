"""Acceso a datos del modulo superadmin: organizations, roles,
organization_invitations (issue #7).

SDD: infrastructure/spec_data_model.md §Capa 0 + §"Estrategia de Seed Data".
docs/skills/tenant-isolation.md "Super Admin: rol DB privilegiado".

Todas las queries de este repository asumen una sesion bajo el rol
`adminprop_superadmin` (BYPASSRLS) -- inyectada via
`db.session.get_superadmin_db_session`. Aun asi, cada query sobre
`roles`/`organization_invitations` (tenant-scoped) filtra
`organization_id` explicitamente (defense in depth,
docs/skills/tenant-isolation.md invariante #4) aunque el bypass ya
ignore la politica RLS -- mismo criterio que `modules/auth/repository.py`.
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

from adminprop.db.session import get_superadmin_db_session


@dataclass(frozen=True)
class OrganizationRow:
    id: UUID
    slug: str
    name: str
    status: str
    timezone: str
    settings: dict
    created_at: datetime
    updated_at: datetime
    owner_email: str | None = None


@dataclass(frozen=True)
class InvitationRow:
    id: UUID
    organization_id: UUID
    email: str
    role_id: UUID
    status: str
    expires_at: datetime


def _parse_settings(raw: object) -> dict:
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        return json.loads(raw)
    return {}


def _encode_cursor(created_at: datetime, organization_id: UUID) -> str:
    raw = f"{created_at.isoformat()}|{organization_id}"
    return base64.urlsafe_b64encode(raw.encode("utf-8")).decode("ascii")


def _decode_cursor(cursor: str) -> tuple[datetime, UUID]:
    raw = base64.urlsafe_b64decode(cursor.encode("ascii")).decode("utf-8")
    created_at_raw, org_id_raw = raw.split("|", 1)
    return datetime.fromisoformat(created_at_raw), UUID(org_id_raw)


# RF-01: owner "actual" mostrado en el dashboard -- primer usuario con
# membresia activa de rol `owner` (RN-A03 exige >= 1, puede haber mas de
# uno; se muestra el mas antiguo).
_OWNER_EMAIL_SUBQUERY = """(
        SELECT u.email
        FROM organization_members m
        JOIN roles r ON r.id = m.role_id AND r.organization_id = o.id
        JOIN users u ON u.id = m.user_id
        WHERE m.organization_id = o.id AND r.name = 'owner' AND m.status = 'active'
        ORDER BY m.created_at
        LIMIT 1
    ) AS owner_email"""

_ORGANIZATION_COLUMNS = (
    "o.id, o.slug, o.name, o.status, o.timezone, o.settings, o.created_at, o.updated_at"
)


def _row_to_organization(row: sa.RowMapping) -> OrganizationRow:
    return OrganizationRow(
        id=row["id"],
        slug=row["slug"],
        name=row["name"],
        status=row["status"],
        timezone=row["timezone"],
        settings=_parse_settings(row["settings"]),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        owner_email=row.get("owner_email", None),
    )


class SuperAdminRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    @property
    def session(self) -> AsyncSession:
        """Expuesto para que `service.py` pueda pasar la MISMA sesion a
        `AuditService.audit()` (issue #10), confirmada junto con la
        escritura de negocio por `commit()`."""
        return self._session

    async def commit(self) -> None:
        """Confirma la transaccion actual.

        Issue #10: se centraliza aca (mismo patron que
        `modules/auth/repository.py.commit` y
        `modules/administracion/repository.py.commit`) en vez de comitear
        dentro de cada metodo de escritura (patron anterior de este
        archivo) -- el `service.py` necesita insertar el evento de
        `audit_logs` ANTES del commit para que quede en la misma
        transaccion que la operacion de negocio.
        """
        await self._session.commit()

    # ─── organizations ──────────────────────────────────────────────

    async def slug_exists(self, slug: str) -> bool:
        """RF-02: slug unico global (incluye organizaciones deshabilitadas)."""
        stmt = text("SELECT 1 FROM organizations WHERE slug = :slug")
        result = await self._session.execute(stmt, {"slug": slug})
        return result.first() is not None

    async def create_organization_with_roles(
        self,
        *,
        name: str,
        slug: str,
        timezone: str,
        settings: dict,
        role_definitions: tuple[tuple[str, tuple[str, ...]], ...],
    ) -> OrganizationRow:
        """CA-00-01: crea la organizacion y siembra sus 3 roles de sistema
        en una unica transaccion (autobegin de SQLAlchemy + un solo commit
        al final -- ver `docs/sdd/infrastructure/spec_data_model.md`
        §"Estrategia de Seed Data")."""
        org_stmt = text(
            """
            INSERT INTO organizations (slug, name, timezone, settings)
            VALUES (:slug, :name, :timezone, :settings)
            RETURNING id, slug, name, status, timezone, settings, created_at, updated_at
            """
        ).bindparams(sa.bindparam("settings", type_=sa.JSON))
        result = await self._session.execute(
            org_stmt,
            {
                "slug": slug,
                "name": name,
                "timezone": timezone,
                "settings": json.dumps(settings),
            },
        )
        row = result.mappings().one()
        organization_id = row["id"]

        role_stmt = text(
            """
            INSERT INTO roles (organization_id, name, permissions, is_system_role)
            VALUES (:organization_id, :name, :permissions, TRUE)
            """
        ).bindparams(sa.bindparam("permissions", type_=sa.JSON))
        for role_name, permissions in role_definitions:
            await self._session.execute(
                role_stmt,
                {
                    "organization_id": str(organization_id),
                    "name": role_name,
                    "permissions": json.dumps(list(permissions)),
                },
            )

        # Issue #10: el commit lo hace `service.py.create()` DESPUES de
        # auditar `org.created` en esta misma transaccion (ver `commit()`
        # de esta clase).
        return OrganizationRow(
            id=row["id"],
            slug=row["slug"],
            name=row["name"],
            status=row["status"],
            timezone=row["timezone"],
            settings=_parse_settings(row["settings"]),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            owner_email=None,
        )

    async def list_organizations(
        self,
        *,
        status: str | None,
        search: str | None,
        cursor: str | None,
        limit: int,
    ) -> tuple[list[OrganizationRow], str | None]:
        """RF-01: dashboard -- filtros por status y busqueda por nombre/slug."""
        conditions = ["o.deleted_at IS NULL"]
        params: dict[str, object] = {"limit": limit + 1}
        if status is not None:
            conditions.append("o.status = :status")
            params["status"] = status
        if search:
            conditions.append("(o.name ILIKE :search OR o.slug ILIKE :search)")
            params["search"] = f"%{search}%"
        if cursor:
            cursor_created_at, cursor_id = _decode_cursor(cursor)
            conditions.append("(o.created_at, o.id) < (:cursor_created_at, :cursor_id)")
            params["cursor_created_at"] = cursor_created_at
            params["cursor_id"] = str(cursor_id)

        where_clause = " AND ".join(conditions)
        stmt = text(
            f"""
            SELECT {_ORGANIZATION_COLUMNS}, {_OWNER_EMAIL_SUBQUERY}
            FROM organizations o
            WHERE {where_clause}
            ORDER BY o.created_at DESC, o.id DESC
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
        return [_row_to_organization(row) for row in page], next_cursor

    async def get_organization_by_id(self, organization_id: UUID) -> OrganizationRow | None:
        stmt = text(
            f"""
            SELECT {_ORGANIZATION_COLUMNS}, {_OWNER_EMAIL_SUBQUERY}
            FROM organizations o
            WHERE o.id = :organization_id AND o.deleted_at IS NULL
            """
        )
        result = await self._session.execute(stmt, {"organization_id": str(organization_id)})
        row = result.mappings().first()
        return _row_to_organization(row) if row is not None else None

    async def update_organization_fields(
        self, organization_id: UUID, fields: dict[str, str]
    ) -> bool:
        """PATCH /superadmin/organizations/:id (issue #44).

        `fields` es un dict con las columnas efectivamente enviadas por el
        cliente (`name` y/o `timezone` -- construido en
        `service.py::update()` a partir de `OrganizationUpdate`
        excluyendo `unset`). Retorna False si la organizacion no existe
        (o esta soft-deleted), igual criterio que
        `update_organization_status`.
        """
        set_clause = ", ".join(f"{column} = :{column}" for column in fields)
        stmt = text(
            f"""
            UPDATE organizations
            SET {set_clause}, updated_at = now()
            WHERE id = :organization_id AND deleted_at IS NULL
            RETURNING id
            """
        )
        params: dict[str, object] = {"organization_id": str(organization_id), **fields}
        result = await self._session.execute(stmt, params)
        # Issue #10: el commit lo hace `service.py.update()` DESPUES de
        # auditar `org.updated` en esta misma transaccion.
        return result.first() is not None

    async def update_organization_status(self, organization_id: UUID, status: str) -> bool:
        """RF-05: disable/enable. Retorna False si la organizacion no existe."""
        stmt = text(
            """
            UPDATE organizations
            SET status = :status, updated_at = now()
            WHERE id = :organization_id AND deleted_at IS NULL
            RETURNING id
            """
        )
        result = await self._session.execute(
            stmt, {"organization_id": str(organization_id), "status": status}
        )
        updated = result.first() is not None
        # Issue #10: el commit lo hace `service.py.disable()/enable()`
        # DESPUES de auditar `org.disabled`/`org.enabled` en esta misma
        # transaccion.
        return updated

    # ─── roles ──────────────────────────────────────────────────────

    async def get_role_id_by_name(self, organization_id: UUID, role_name: str) -> UUID | None:
        stmt = text(
            "SELECT id FROM roles WHERE organization_id = :organization_id AND name = :name"
        )
        result = await self._session.execute(
            stmt, {"organization_id": str(organization_id), "name": role_name}
        )
        row = result.first()
        return row[0] if row is not None else None

    # ─── organization_invitations ──────────────────────────────────

    async def get_pending_owner_invitation_for_update(
        self, organization_id: UUID
    ) -> InvitationRow | None:
        """RF-03: `SELECT ... FOR UPDATE` -- serializa a nivel app la
        invariante "una sola invitacion de owner pending por organizacion"
        contra invocaciones concurrentes de invite-owner/resend-invitation."""
        stmt = text(
            """
            SELECT oi.id, oi.organization_id, oi.email, oi.role_id, oi.status, oi.expires_at
            FROM organization_invitations oi
            JOIN roles r ON r.id = oi.role_id AND r.organization_id = oi.organization_id
            WHERE oi.organization_id = :organization_id
              AND r.name = 'owner'
              AND oi.status = 'pending'
            FOR UPDATE
            """
        )
        result = await self._session.execute(stmt, {"organization_id": str(organization_id)})
        row = result.mappings().first()
        if row is None:
            return None
        return InvitationRow(**row)

    async def revoke_invitation(self, invitation_id: UUID) -> None:
        """RF-04: la invitacion reemplazada queda `revoked` (no se borra)."""
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
        """`token` guarda el HASH (nunca el token en claro -- mismo patron
        que `shared/auth/refresh_store.py`)."""
        stmt = text(
            """
            INSERT INTO organization_invitations
                (organization_id, email, role_id, token, status, expires_at)
            VALUES (:organization_id, :email, :role_id, :token_hash, 'pending', :expires_at)
            RETURNING id, organization_id, email, role_id, status, expires_at
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
        # Issue #10: el commit lo hace `service.py` DESPUES de auditar
        # `invitation.sent` en esta misma transaccion.
        return InvitationRow(**row)


def get_superadmin_repository(
    session: AsyncSession = Depends(get_superadmin_db_session),
) -> SuperAdminRepository:
    return SuperAdminRepository(session)
