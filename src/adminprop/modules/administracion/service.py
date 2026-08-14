"""Logica de negocio del modulo administracion (issue #9).

SDD: docs/sdd/features/spec_module_07_administracion.md RF-01..RF-04.
Implements: CA-07-01..CA-07-05, RN-01..RN-05 (= RN-A02/A03/D01).
"""

from __future__ import annotations

import hashlib
import logging
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID

from fastapi import Depends
from redis.asyncio import Redis

from adminprop.config import Settings, get_settings
from adminprop.modules.administracion.repository import (
    AdministracionRepository,
    InvitationRow,
    MemberRow,
    RoleRow,
    get_administracion_repository,
)
from adminprop.shared.audit.service import audit
from adminprop.shared.auth.refresh_store import RefreshTokenStore
from adminprop.shared.cache.redis import get_redis_client
from adminprop.shared.errors.codes import (
    InvitationPendingExistsException,
    LastOwnerRequiredException,
    NotFoundException,
    RoleNotFoundException,
    SystemRoleImmutableException,
    UserAlreadyMemberException,
)
from adminprop.workers.notification_worker import send_transactional_email

logger = logging.getLogger(__name__)

_OWNER_ROLE_NAME = "owner"


def _hash_token(raw_token: str) -> str:
    """Mismo algoritmo que `modules/superadmin/service.py._hash_token` --
    debe producir el mismo hash que `modules/auth` para que la
    aceptacion de la invitacion (que reusa el flujo generico de
    `AccountActivationService`) encuentre el token."""
    return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class IssuedInvitation:
    row: InvitationRow
    raw_token: str


class UserService:
    """RF-01 (invitaciones) + RF-02 (gestion de usuarios)."""

    def __init__(
        self, repo: AdministracionRepository, refresh_store: RefreshTokenStore, settings: Settings
    ) -> None:
        self._repo = repo
        self._refresh_store = refresh_store
        self._settings = settings

    # ─── RF-01: invitaciones ────────────────────────────────────────────

    async def invite(
        self, *, organization_id: UUID, email: str, role_name: str, request_id: str
    ) -> InvitationRow:
        """RF-01: invita un usuario del equipo con rol `admin` o
        `maintenance` (el rol `owner` nunca llega aca -- rechazado antes
        por el `Literal` del schema)."""
        existing_user_id = await self._repo.get_user_id_by_email(email)
        if existing_user_id is not None:
            membership_status = await self._repo.get_membership_status(
                organization_id, existing_user_id
            )
            if membership_status is not None:
                raise UserAlreadyMemberException()

        existing_invitation = await self._repo.get_pending_invitation_by_email(
            organization_id, email
        )
        if existing_invitation is not None:
            raise InvitationPendingExistsException()

        role_id = await self._repo.get_role_id_by_name(organization_id, role_name)
        if role_id is None:  # pragma: no cover -- defensivo, siempre sembrado en create()
            raise RoleNotFoundException()

        issued = await self._issue_invitation(
            organization_id=organization_id, email=email, role_id=role_id
        )
        await self._repo.commit()
        self._send_invitation_email(to=email, raw_token=issued.raw_token, request_id=request_id)

        # Nota (issue #10): "invitar un usuario" no esta en la lista minima
        # de eventos auditados de sdd_02 §2.17 ("cambios de rol/usuario,
        # intentos de acceso no autorizado, ...") -- se deja como logging
        # estructurado (no `audit_logs`) hasta que un CA/RN real lo pida.
        logger.info(
            "user invited",
            extra={
                "organization_id": str(organization_id),
                "email": email,
                "role": role_name,
                "service": "administracion",
            },
        )
        return issued.row

    async def list_invitations(
        self, *, organization_id: UUID, cursor: str | None, limit: int
    ) -> tuple[list[InvitationRow], str | None]:
        return await self._repo.list_pending_invitations(
            organization_id=organization_id, cursor=cursor, limit=limit
        )

    async def resend_invitation(
        self, *, organization_id: UUID, invitation_id: UUID, request_id: str
    ) -> InvitationRow:
        """RF-01: revoca la anterior y emite una nueva con el mismo
        email/rol. 404 si no existe/no es de esta organizacion/no esta
        `pending` (RN-D01, no se distingue el motivo)."""
        existing = await self._repo.get_pending_invitation_by_id(organization_id, invitation_id)
        if existing is None:
            raise NotFoundException()

        await self._repo.revoke_invitation(existing.id)
        issued = await self._issue_invitation(
            organization_id=organization_id, email=existing.email, role_id=existing.role_id
        )
        await self._repo.commit()
        self._send_invitation_email(
            to=issued.row.email, raw_token=issued.raw_token, request_id=request_id
        )
        return issued.row

    async def revoke_invitation(self, *, organization_id: UUID, invitation_id: UUID) -> None:
        existing = await self._repo.get_pending_invitation_by_id(organization_id, invitation_id)
        if existing is None:
            raise NotFoundException()
        await self._repo.revoke_invitation(existing.id)
        await self._repo.commit()

    async def _issue_invitation(
        self, *, organization_id: UUID, email: str, role_id: UUID
    ) -> IssuedInvitation:
        raw_token = secrets.token_urlsafe(32)
        expires_at = datetime.now(UTC) + timedelta(hours=self._settings.invitation_ttl_hours)
        row = await self._repo.create_invitation(
            organization_id=organization_id,
            email=email,
            role_id=role_id,
            token_hash=_hash_token(raw_token),
            expires_at=expires_at,
        )
        return IssuedInvitation(row=row, raw_token=raw_token)

    def _send_invitation_email(self, *, to: str, raw_token: str, request_id: str) -> None:
        """RF-01: mismo flujo de activacion de cuenta que el Modulo 0 --
        el email sale via `notification_worker` (Resend), encolado, nunca
        bloquea la respuesta HTTP (docs/skills/async-worker.md)."""
        link = f"{self._settings.frontend_base_url}/accept-invitation?token={raw_token}"
        ttl_hours = self._settings.invitation_ttl_hours
        send_transactional_email.delay(
            to=[to],
            subject="Invitacion a AdminProp",
            html=(
                "<p>Fuiste invitado a sumarte a un equipo en AdminProp.</p>"
                f'<p><a href="{link}">Activar mi cuenta</a></p>'
                f"<p>Este link expira en {ttl_hours} horas.</p>"
            ),
            text=(
                "Fuiste invitado a sumarte a un equipo en AdminProp. "
                f"Activa tu cuenta: {link} (expira en {ttl_hours}h)."
            ),
            organization_name="AdminProp",
            request_id=request_id,
        )

    # ─── RF-02: gestion de usuarios ─────────────────────────────────────

    async def list_members(
        self, *, organization_id: UUID, cursor: str | None, limit: int
    ) -> tuple[list[MemberRow], str | None]:
        return await self._repo.list_members(
            organization_id=organization_id, cursor=cursor, limit=limit
        )

    async def change_role(
        self,
        *,
        organization_id: UUID,
        user_id: UUID,
        new_role_name: str,
        actor_user_id: UUID,
    ) -> MemberRow:
        """RF-02: `PATCH /users/:id`.

        RN-02/RN-A03: si el rol ACTUAL es `owner` y se lo cambia a otro
        rol, valida `LAST_OWNER_REQUIRED` con el lock de
        `count_active_owners_locked` -- serializa contra un DELETE/PATCH
        concurrente del otro owner (ver `test_last_owner_required.py`
        para el escenario de "concurrencia secuencial").
        """
        member = await self._repo.get_member(organization_id, user_id)
        if member is None:
            raise NotFoundException()

        if member.role_name == _OWNER_ROLE_NAME and new_role_name != _OWNER_ROLE_NAME:
            active_owners = await self._repo.count_active_owners_locked(organization_id)
            if active_owners <= 1:
                raise LastOwnerRequiredException()

        role_id = await self._repo.get_role_id_by_name(organization_id, new_role_name)
        if role_id is None:  # pragma: no cover -- defensivo, roles de sistema siempre existen
            raise RoleNotFoundException()

        await self._repo.update_member_role(
            organization_id=organization_id, user_id=user_id, role_id=role_id
        )

        # RN-D04 / sdd_02 §2.17 ("cambios de rol/usuario" -- evento minimo
        # auditado): registrado en la MISMA transaccion que el UPDATE de
        # arriba -- si algo rollbackea despues, el audit tambien.
        await audit(
            self._repo.session,
            organization_id=organization_id,
            action="user.role_changed",
            entity_type="organization_member",
            entity_id=user_id,
            before={"role": member.role_name},
            after={"role": new_role_name},
            user_id=actor_user_id,
        )
        await self._repo.commit()

        updated = await self._repo.get_member(organization_id, user_id)
        assert updated is not None  # pragma: no cover -- se acaba de actualizar
        return updated

    async def deactivate(
        self, *, organization_id: UUID, user_id: UUID, actor_user_id: UUID
    ) -> None:
        """RF-02: `DELETE /users/:id` -- soft (`status='inactive'`).

        RN-02/RN-A03: mismo lock que `change_role` si el miembro es
        owner. Ademas revoca todas las sesiones existentes del usuario
        (CLAUDE.md §4 / RN-A: "un usuario desactivado no puede
        loguearse" -- el refresh token ya emitido seguiria vivo en Redis
        sin esto, aunque `get_active_memberships` ya bloquee un login/
        refresh nuevo filtrando `status='active'`).
        """
        member = await self._repo.get_member(organization_id, user_id)
        if member is None:
            raise NotFoundException()

        if member.role_name == _OWNER_ROLE_NAME:
            active_owners = await self._repo.count_active_owners_locked(organization_id)
            if active_owners <= 1:
                raise LastOwnerRequiredException()

        await self._repo.deactivate_member(organization_id=organization_id, user_id=user_id)

        # RN-D04 / sdd_02 §2.17 ("cambios de rol/usuario"): audit en la
        # MISMA transaccion que el soft-delete de arriba, confirmada por
        # el mismo `commit()` -- se mueve el commit despues del audit
        # (antes commiteaba primero, ver PR #10).
        await audit(
            self._repo.session,
            organization_id=organization_id,
            action="user.deactivated",
            entity_type="organization_member",
            entity_id=user_id,
            before={"status": member.status},
            after={"status": "inactive"},
            user_id=actor_user_id,
        )
        await self._repo.commit()
        await self._refresh_store.revoke_all_families_for_user(user_id)


def get_user_service(
    repo: AdministracionRepository = Depends(get_administracion_repository),
    redis: Redis = Depends(get_redis_client),
    settings: Settings = Depends(get_settings),
) -> UserService:
    return UserService(repo, RefreshTokenStore(redis, settings), settings)


class RoleService:
    """RF-03: roles y permisos."""

    def __init__(self, repo: AdministracionRepository) -> None:
        self._repo = repo

    async def list_roles(self, organization_id: UUID) -> list[RoleRow]:
        return await self._repo.list_roles(organization_id)

    @staticmethod
    def ensure_role_editable(role: RoleRow) -> None:
        """RN-03 (CA-07-03): los roles de sistema son inmutables.

        `sdd_03` §3 no define un endpoint de escritura de roles en MVP
        (`GET /roles` es solo lectura) -- este metodo defensivo documenta
        la invariante para que cualquier endpoint futuro de escritura de
        roles (post-MVP) lo invoque antes de aplicar el cambio.
        """
        if role.is_system_role:
            raise SystemRoleImmutableException()


def get_role_service(
    repo: AdministracionRepository = Depends(get_administracion_repository),
) -> RoleService:
    return RoleService(repo)


class OrganizationSettingsService:
    """RF-04: configuracion de la organizacion."""

    def __init__(self, repo: AdministracionRepository) -> None:
        self._repo = repo

    async def get_settings(self, organization_id: UUID) -> dict:
        settings = await self._repo.get_organization_settings(organization_id)
        if settings is None:  # pragma: no cover -- defensivo, la org del JWT siempre existe
            raise NotFoundException()
        return settings

    async def update_settings(
        self,
        organization_id: UUID,
        *,
        grace_day: int,
        contract_expiry_notice_days: int,
        billing_name: str | None,
        billing_cuit: str | None,
        billing_contact: str | None,
        actor_user_id: UUID,
    ) -> dict:
        """RF-04: mergea los campos nuevos dentro del JSON `settings`
        existente (no pisa otras claves que puedan existir a futuro).

        RN-05/CA-07-05: `grace_day` rige desde el momento del cambio, sin
        recalcular intereses ya imputados -- este servicio solo persiste
        el nuevo valor (el modulo de cobranzas, issue #22, es el que
        consume `grace_day` al calcular mora futura). El cambio (si lo
        hay) queda auditado en `audit_logs` (issue #10, action
        `settings.changed`).
        """
        current = await self._repo.get_organization_settings(organization_id)
        if current is None:  # pragma: no cover -- defensivo, la org del JWT siempre existe
            raise NotFoundException()

        merged = {
            **current,
            "grace_day": grace_day,
            "contract_expiry_notice_days": contract_expiry_notice_days,
            "billing_header": {
                "name": billing_name,
                "cuit": billing_cuit,
                "contact": billing_contact,
            },
        }
        updated = await self._repo.update_organization_settings(organization_id, merged)
        if updated is None:  # pragma: no cover -- defensivo, la org del JWT siempre existe
            raise NotFoundException()

        if current != merged:
            # RN-D04 / sdd_02 §2.17 ("cambios de rol/usuario" y, por
            # extension, de configuracion sensible como `grace_day`):
            # audit en la MISMA transaccion que el UPDATE de arriba, sin
            # ruido si el PUT no cambio nada.
            await audit(
                self._repo.session,
                organization_id=organization_id,
                action="settings.changed",
                entity_type="organization",
                entity_id=organization_id,
                before=current,
                after=merged,
                user_id=actor_user_id,
            )
        await self._repo.commit()
        return updated


def get_organization_settings_service(
    repo: AdministracionRepository = Depends(get_administracion_repository),
) -> OrganizationSettingsService:
    return OrganizationSettingsService(repo)
