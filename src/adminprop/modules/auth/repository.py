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
import logging
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from fastapi import Depends
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from adminprop.db.session import get_db_session, get_superadmin_db_session
from adminprop.shared.errors.codes import InternalError

logger = logging.getLogger(__name__)


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


@dataclass(frozen=True)
class InvitationDetailRecord:
    """Issue #8: shape completo de una invitacion + su org/rol para
    `GET /auth/invitation/:token` y `POST /auth/accept-invitation`
    (spec_module_00_superadmin.md "Flujo de Activacion de Cuenta")."""

    id: UUID
    organization_id: UUID
    organization_name: str
    organization_status: str
    email: str
    role_id: UUID
    role_name: str
    role_permissions: list[str]
    status: str
    expires_at: datetime


def _parse_permission_json_array(value: object) -> list[object] | None:
    """Si `value` es un string que a su vez es JSON de un array, lo parsea
    y devuelve esa lista; `None` si no aplica (no es string, o no es JSON
    de un array)."""
    if not isinstance(value, str):
        return None
    try:
        nested = json.loads(value)
    except (TypeError, ValueError):
        return None
    return nested if isinstance(nested, list) else None


def _parse_permissions(raw: object) -> list[str]:
    """Normaliza `roles.permissions` (o `role_permissions` de invitaciones)
    a una lista plana de strings.

    Issue #116: un bug historico de doble-codificacion en la escritura
    (`INSERT`/`UPDATE` con `bindparam(type_=sa.JSON)` recibiendo un valor
    ya serializado con `json.dumps`) podia dejar la columna en alguna de
    estas formas:
    - escalar string con el array serializado adentro (asyncpg ya
      decodifica un nivel de JSON -- la rama `isinstance(raw, str)` de
      abajo hace el segundo `json.loads` necesario).
    - array con UN elemento string que a su vez es JSON de un array +
      el resto de elementos ya planos (la forma que producia la migracion
      `permissions || '[...]'::jsonb` del issue #105 al concatenar sobre
      un valor ya doble-codificado).
    - (forma correcta, post-fix del bug de escritura) array de strings
      simples.

    Esta funcion aplana cualquier elemento string que a su vez sea JSON
    de un array (unico nivel de anidamiento observado en produccion,
    ver evidencia del issue #116), dedupea preservando el orden, y valida
    que el resultado final sea `list[str]`. Ante una forma que no puede
    normalizarse de forma segura, loguea el error y levanta
    `InternalError` en vez de devolver una lista corrupta silenciosamente
    (CLAUDE.md §8 "ante algo no especificado: detenerse y reportar" /
    issue #116 "fallar ruidosamente").
    """
    value: object = raw
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (TypeError, ValueError):
            logger.error(
                "roles.permissions con forma invalida: string no es JSON valido",
                extra={"raw_permissions": raw},
            )
            raise InternalError(message="El formato de permisos del rol es invalido.") from None

    if not isinstance(value, list):
        logger.error(
            "roles.permissions con forma invalida: se esperaba una lista",
            extra={"raw_permissions": raw},
        )
        raise InternalError(message="El formato de permisos del rol es invalido.")

    flat: list[str] = []
    seen: set[str] = set()
    for item in value:
        nested = _parse_permission_json_array(item)
        candidates: list[object] = nested if nested is not None else [item]
        for candidate in candidates:
            if not isinstance(candidate, str):
                logger.error(
                    "roles.permissions con elemento no-string tras aplanar",
                    extra={"raw_permissions": raw, "offending_item": candidate},
                )
                raise InternalError(message="El formato de permisos del rol es invalido.")
            if candidate not in seen:
                seen.add(candidate)
                flat.append(candidate)
    return flat


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

    # ─── issue #8 — activacion de cuenta + reset password ───────────────
    # Los metodos de esta seccion se invocan siempre a traves de
    # `get_auth_activation_repository` (sesion BYPASSRLS, ver mas abajo):
    # el organization_id de una invitacion es desconocido hasta resolver
    # el token (cross-org por naturaleza), igual criterio que
    # `get_active_memberships` arriba y que
    # `modules/superadmin/repository.py` (docs/skills/tenant-isolation.md
    # "Super Admin: rol DB privilegiado"). Cada query igual filtra
    # `organization_id`/`id` explicitamente (defense in depth).

    async def get_invitation_by_token_hash(self, token_hash: str) -> InvitationDetailRecord | None:
        """spec_module_00_superadmin.md "Flujo de Activacion de Cuenta" paso 2/3."""
        stmt = text(
            """
            SELECT oi.id, oi.organization_id, o.name AS organization_name,
                   o.status AS organization_status, oi.email, oi.role_id,
                   r.name AS role_name, r.permissions AS role_permissions,
                   oi.status, oi.expires_at
            FROM organization_invitations oi
            JOIN organizations o ON o.id = oi.organization_id
            JOIN roles r ON r.id = oi.role_id
            WHERE oi.token = :token_hash
            """
        )
        result = await self._session.execute(stmt, {"token_hash": token_hash})
        row = result.mappings().first()
        if row is None:
            return None
        return InvitationDetailRecord(
            id=row["id"],
            organization_id=row["organization_id"],
            organization_name=row["organization_name"],
            organization_status=row["organization_status"],
            email=row["email"],
            role_id=row["role_id"],
            role_name=row["role_name"],
            role_permissions=_parse_permissions(row["role_permissions"]),
            status=row["status"],
            expires_at=row["expires_at"],
        )

    async def get_membership_status(self, organization_id: UUID, user_id: UUID) -> str | None:
        """`None` si el user global no tiene ninguna fila de membresia en
        `organization_id` (activa o inactiva) -- usado para decidir
        `USER_ALREADY_MEMBER` en accept-invitation."""
        stmt = text(
            "SELECT status FROM organization_members "
            "WHERE organization_id = :organization_id AND user_id = :user_id"
        )
        result = await self._session.execute(
            stmt, {"organization_id": str(organization_id), "user_id": str(user_id)}
        )
        row = result.first()
        return row[0] if row is not None else None

    async def create_user(self, *, email: str, password_hash: str, full_name: str) -> UserRecord:
        stmt = text(
            """
            INSERT INTO users (email, password_hash, full_name)
            VALUES (:email, :password_hash, :full_name)
            RETURNING id, email, password_hash, full_name, is_super_admin
            """
        )
        result = await self._session.execute(
            stmt, {"email": email, "password_hash": password_hash, "full_name": full_name}
        )
        row = result.mappings().one()
        return UserRecord(
            id=row["id"],
            email=row["email"],
            password_hash=row["password_hash"],
            full_name=row["full_name"],
            is_super_admin=bool(row["is_super_admin"]),
        )

    async def create_membership(
        self, *, organization_id: UUID, user_id: UUID, role_id: UUID
    ) -> None:
        stmt = text(
            """
            INSERT INTO organization_members (organization_id, user_id, role_id, status)
            VALUES (:organization_id, :user_id, :role_id, 'active')
            """
        )
        await self._session.execute(
            stmt,
            {
                "organization_id": str(organization_id),
                "user_id": str(user_id),
                "role_id": str(role_id),
            },
        )

    async def mark_invitation_accepted(self, invitation_id: UUID) -> None:
        stmt = text(
            "UPDATE organization_invitations SET status = 'accepted', updated_at = now() "
            "WHERE id = :id"
        )
        await self._session.execute(stmt, {"id": str(invitation_id)})

    async def activate_organization(self, organization_id: UUID) -> None:
        """CA-00-03: "la organizacion pasa a active" al completar la
        activacion -- se aplica siempre (idempotente si ya estaba active),
        defense in depth con filtro explicito por id aunque la sesion ya
        sea BYPASSRLS."""
        stmt = text(
            "UPDATE organizations SET status = 'active', updated_at = now() "
            "WHERE id = :organization_id"
        )
        await self._session.execute(stmt, {"organization_id": str(organization_id)})

    async def update_password_hash(self, user_id: UUID, password_hash: str) -> None:
        """reset-password (issue #8): `users` no tiene RLS (issue #5), no
        requiere sesion BYPASSRLS -- se invoca via `get_auth_repository`
        (sesion `adminprop_app` normal)."""
        stmt = text(
            "UPDATE users SET password_hash = :password_hash, updated_at = now() "
            "WHERE id = :user_id"
        )
        await self._session.execute(stmt, {"user_id": str(user_id), "password_hash": password_hash})

    async def commit(self) -> None:
        """Confirma la transaccion actual. Expuesto explicitamente (en vez
        de comitear dentro de cada metodo de escritura, patron de
        `modules/superadmin/repository.py`) porque accept-invitation
        combina varias escrituras (create_user + create_membership +
        mark_invitation_accepted + activate_organization) que deben
        confirmarse **todas juntas** en una unica transaccion (spec_module_00_superadmin.md
        "Flujo de Activacion de Cuenta" paso 4)."""
        await self._session.commit()


def get_auth_repository(
    session: AsyncSession = Depends(get_db_session),
) -> AuthRepository:
    return AuthRepository(session)


def get_auth_activation_repository(
    session: AsyncSession = Depends(get_superadmin_db_session),
) -> AuthRepository:
    """Issue #8: variante BYPASSRLS para accept-invitation/GET invitation --
    el organization_id de la invitacion todavia no se conoce al momento de
    resolver el token (docs/skills/tenant-isolation.md "Super Admin: rol DB
    privilegiado", mismo mecanismo que `get_active_memberships`)."""
    return AuthRepository(session)
