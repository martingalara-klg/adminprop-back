"""Acceso a datos de auth: users, organization_members, roles, organizations.

SDD: infrastructure/spec_data_model.md §Capa 0. sdd_04 §2.3 (RLS + rol
adminprop_superadmin BYPASSRLS).

Decision de implementacion (issue #6): estas 4 tablas fueron creadas por
el issue #5 (migracion pura, sin modelos ORM) y son compartidas por varios
modulos futuros (superadmin #7, administracion #9) que todavia no
definieron su propio dueno de esas tablas -- para no imponer un modelo
SQLAlchemy prematuro que un modulo futuro tendria que redefinir o
reusar de forma acoplada, este repositorio usa SQL crudo via `text()`
(mismo patron que `docs/skills/tenant-isolation.md` "Queries con
join/agregacion"), no ORM. Si un modulo posterior centraliza estos
modelos, este repositorio se puede migrar sin cambiar su interfaz publica.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from uuid import UUID

from fastapi import Depends
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from adminprop.db.session import get_db_session


@dataclass(frozen=True)
class UserRecord:
    id: UUID
    email: str
    password_hash: str
    full_name: str
    is_super_admin: bool


@dataclass(frozen=True)
class MembershipRecord:
    organization_id: UUID
    organization_name: str
    role_name: str
    permissions: list[str]


def _parse_permissions(raw: object) -> list[str]:
    if isinstance(raw, list):
        return [str(item) for item in raw]
    if isinstance(raw, str):
        return [str(item) for item in json.loads(raw)]
    return []


class AuthRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_user_by_email(self, email: str) -> UserRecord | None:
        """`users` no tiene RLS (identidad global, issue #5) -- lectura directa."""
        stmt = text(
            """
            SELECT id, email, password_hash, full_name, is_super_admin
            FROM users
            WHERE LOWER(email) = LOWER(:email) AND deleted_at IS NULL
            """
        )
        result = await self._session.execute(stmt, {"email": email})
        row = result.mappings().first()
        if row is None:
            return None
        return UserRecord(
            id=row["id"],
            email=row["email"],
            password_hash=row["password_hash"],
            full_name=row["full_name"],
            is_super_admin=bool(row["is_super_admin"]),
        )

    async def get_user_by_id(self, user_id: UUID) -> UserRecord | None:
        stmt = text(
            """
            SELECT id, email, password_hash, full_name, is_super_admin
            FROM users
            WHERE id = :user_id AND deleted_at IS NULL
            """
        )
        result = await self._session.execute(stmt, {"user_id": str(user_id)})
        row = result.mappings().first()
        if row is None:
            return None
        return UserRecord(
            id=row["id"],
            email=row["email"],
            password_hash=row["password_hash"],
            full_name=row["full_name"],
            is_super_admin=bool(row["is_super_admin"]),
        )

    async def get_active_memberships(self, user_id: UUID) -> list[MembershipRecord]:
        """Resuelve las organizaciones activas del usuario, con rol + permisos.

        `organization_members`/`roles` tienen RLS FORCE (issue #5) y el
        tenant todavia no existe en este punto del flujo (login/refresh
        deben *descubrir* la organizacion antes de poder setear
        `app.current_tenant_id`). Se conmuta a `adminprop_superadmin`
        (BYPASSRLS) solo para esta lectura puntual -- mismo mecanismo
        transaction-scoped via PgBouncer que `/superadmin/*`
        (docs/skills/tenant-isolation.md) -- y se revierte con `RESET ROLE`
        antes de continuar, sin escribir nada bajo el rol elevado.
        """
        await self._session.execute(text("SET ROLE adminprop_superadmin"))
        try:
            stmt = text(
                """
                SELECT o.id AS organization_id, o.name AS organization_name,
                       r.name AS role_name, r.permissions AS permissions
                FROM organization_members m
                JOIN organizations o ON o.id = m.organization_id
                JOIN roles r ON r.id = m.role_id
                WHERE m.user_id = :user_id
                  AND m.status = 'active'
                  AND o.status = 'active'
                  AND o.deleted_at IS NULL
                ORDER BY o.name
                """
            )
            result = await self._session.execute(stmt, {"user_id": str(user_id)})
            rows = result.mappings().all()
        finally:
            await self._session.execute(text("RESET ROLE"))

        return [
            MembershipRecord(
                organization_id=row["organization_id"],
                organization_name=row["organization_name"],
                role_name=row["role_name"],
                permissions=_parse_permissions(row["permissions"]),
            )
            for row in rows
        ]


def get_auth_repository(
    session: AsyncSession = Depends(get_db_session),
) -> AuthRepository:
    return AuthRepository(session)
