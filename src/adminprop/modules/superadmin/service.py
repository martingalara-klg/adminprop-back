"""Logica de negocio del modulo superadmin (issue #7).

SDD: core/spec_module_00_superadmin.md RF-01..RF-05.
"""

from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID

from fastapi import Depends

from adminprop.config import Settings, get_settings
from adminprop.modules.superadmin.provisioning import (
    DEFAULT_ORGANIZATION_SETTINGS,
    ROLE_DEFINITIONS,
    slugify,
)
from adminprop.modules.superadmin.repository import (
    InvitationRow,
    OrganizationRow,
    SuperAdminRepository,
    get_superadmin_repository,
)
from adminprop.shared.audit.service import audit
from adminprop.shared.errors.codes import (
    InvitationPendingExistsException,
    NotFoundException,
    ValidationError,
)
from adminprop.workers.notification_worker import send_transactional_email


@dataclass(frozen=True)
class IssuedInvitation:
    row: InvitationRow
    raw_token: str


def _hash_token(raw_token: str) -> str:
    """Mismo patron que `shared/auth/refresh_store.py`: nunca persistir el
    token en claro (spec_module_00_superadmin.md §Validaciones)."""
    return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()


class OrganizationService:
    def __init__(self, repo: SuperAdminRepository, settings: Settings) -> None:
        self._repo = repo
        self._settings = settings

    async def create(self, name: str, timezone_name: str, actor_user_id: UUID) -> OrganizationRow:
        """RF-02 + CA-00-01: slug autogenerado unico + 3 roles + settings
        default, todo en la misma transaccion (repository.create_organization_with_roles)."""
        slug = await self._generate_unique_slug(name)
        org = await self._repo.create_organization_with_roles(
            name=name,
            slug=slug,
            timezone=timezone_name,
            settings=dict(DEFAULT_ORGANIZATION_SETTINGS),
            role_definitions=ROLE_DEFINITIONS,
        )

        # sdd_02 §2.17 / RN-05: creacion de organizacion auditada, en la
        # MISMA transaccion que el INSERT de arriba (repository ya no
        # comitea internamente, ver issue #10).
        await audit(
            self._repo.session,
            organization_id=org.id,
            action="org.created",
            entity_type="organization",
            entity_id=org.id,
            after={"name": org.name, "slug": org.slug, "status": org.status},
            user_id=actor_user_id,
        )
        await self._repo.commit()
        return org

    async def _generate_unique_slug(self, name: str) -> str:
        """RF-02: kebab-case, unico global; colisiones se sufijan -2, -3, ..."""
        base = slugify(name)
        candidate = base
        suffix = 2
        while await self._repo.slug_exists(candidate):
            candidate = f"{base}-{suffix}"
            suffix += 1
        return candidate

    async def list(
        self, *, status: str | None, search: str | None, cursor: str | None, limit: int
    ) -> tuple[list[OrganizationRow], str | None]:
        """RF-01: dashboard de organizaciones."""
        return await self._repo.list_organizations(
            status=status, search=search, cursor=cursor, limit=limit
        )

    async def get(self, organization_id: UUID) -> OrganizationRow | None:
        return await self._repo.get_organization_by_id(organization_id)

    async def invite_owner(
        self, organization_id: UUID, email: str, request_id: str, actor_user_id: UUID
    ) -> InvitationRow:
        """RF-03: primera invitacion de owner de la organizacion.

        `INVITATION_PENDING_EXISTS` si ya hay una pending -- el caller debe
        usar `resend_invitation` (RF-04), que revoca la anterior
        automaticamente.
        """
        org = await self._repo.get_organization_by_id(organization_id)
        if org is None:
            raise NotFoundException()
        if org.status != "pending_owner":
            raise ValidationError(
                field="organization_id",
                message="Solo se puede invitar un owner a una organizacion pending_owner.",
                details={"status": org.status},
            )

        existing = await self._repo.get_pending_owner_invitation_for_update(organization_id)
        if existing is not None:
            raise InvitationPendingExistsException()

        role_id = await self._repo.get_role_id_by_name(organization_id, "owner")
        if role_id is None:  # pragma: no cover -- defensivo, siempre sembrado en create()
            raise NotFoundException(message="Rol owner no encontrado para la organizacion.")

        issued = await self._issue_invitation(
            organization_id=organization_id, email=email, role_id=role_id
        )
        await self._audit_invitation_sent(
            organization_id=organization_id, issued=issued, actor_user_id=actor_user_id
        )
        self._send_invitation_email(
            to=email,
            organization_name=org.name,
            raw_token=issued.raw_token,
            request_id=request_id,
        )
        return issued.row

    async def resend_invitation(
        self, organization_id: UUID, request_id: str, actor_user_id: UUID
    ) -> InvitationRow:
        """RF-04: reenvio -- regenera token/expiracion; la anterior queda `revoked`."""
        org = await self._repo.get_organization_by_id(organization_id)
        if org is None:
            raise NotFoundException()

        existing = await self._repo.get_pending_owner_invitation_for_update(organization_id)
        if existing is None:
            raise NotFoundException(
                message="No hay una invitacion de owner pendiente para reenviar.",
                field="organization_id",
            )

        await self._repo.revoke_invitation(existing.id)
        issued = await self._issue_invitation(
            organization_id=organization_id, email=existing.email, role_id=existing.role_id
        )
        await self._audit_invitation_sent(
            organization_id=organization_id, issued=issued, actor_user_id=actor_user_id
        )
        self._send_invitation_email(
            to=issued.row.email,
            organization_name=org.name,
            raw_token=issued.raw_token,
            request_id=request_id,
        )
        return issued.row

    async def _audit_invitation_sent(
        self, *, organization_id: UUID, issued: IssuedInvitation, actor_user_id: UUID
    ) -> None:
        """sdd_02 §2.17 (issue #10): `invitation.sent` en la MISMA
        transaccion que `_issue_invitation` (repository ya no comitea
        internamente) -- confirmada por `commit()` antes de que
        `_send_invitation_email` encole el mail (nunca mandar el email de
        una invitacion cuya escritura todavia no confirmo)."""
        await audit(
            self._repo.session,
            organization_id=organization_id,
            action="invitation.sent",
            entity_type="organization_invitation",
            entity_id=issued.row.id,
            after={"email": issued.row.email, "role": "owner"},
            user_id=actor_user_id,
        )
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

    def _send_invitation_email(
        self, *, to: str, organization_name: str, raw_token: str, request_id: str
    ) -> None:
        """RF-03: el email sale via `notification_worker` (Resend), encolado
        -- nunca bloquea la respuesta HTTP de este endpoint (async-worker.md)."""
        link = f"{self._settings.frontend_base_url}/accept-invitation?token={raw_token}"
        ttl_hours = self._settings.invitation_ttl_hours
        send_transactional_email.delay(
            to=[to],
            subject=f"Invitacion a AdminProp -- {organization_name}",
            html=(
                f"<p>Fuiste invitado a administrar <strong>{organization_name}</strong> "
                f'en AdminProp.</p><p><a href="{link}">Activar mi cuenta</a></p>'
                f"<p>Este link expira en {ttl_hours} horas.</p>"
            ),
            text=(
                f"Fuiste invitado a administrar {organization_name} en AdminProp. "
                f"Activa tu cuenta: {link} (expira en {ttl_hours}h)."
            ),
            organization_name=organization_name,
            request_id=request_id,
        )

    async def disable(
        self, organization_id: UUID, reason: str, actor_user_id: UUID
    ) -> OrganizationRow:
        """RF-05 + RN-03: una organizacion `disabled` rechaza login/refresh
        de sus miembros -- ya enforzado por `modules/auth/repository.py`
        (`get_active_memberships` filtra `o.status = 'active'`)."""
        org = await self._repo.get_organization_by_id(organization_id)
        if org is None:
            raise NotFoundException()
        if org.status == "disabled":
            raise ValidationError(field="status", message="La organizacion ya esta deshabilitada.")

        updated = await self._repo.update_organization_status(organization_id, "disabled")
        if not updated:  # pragma: no cover -- defensivo, race entre el check y el UPDATE
            raise NotFoundException()

        # RN-05 (issue #10): toda operacion de Super Admin se audita
        # (creacion, invitacion, disable/enable) con actor y motivo, en la
        # MISMA transaccion que el UPDATE de arriba.
        await audit(
            self._repo.session,
            organization_id=organization_id,
            action="org.disabled",
            entity_type="organization",
            entity_id=organization_id,
            before={"status": org.status},
            after={"status": "disabled", "reason": reason},
            user_id=actor_user_id,
        )
        await self._repo.commit()
        result = await self._repo.get_organization_by_id(organization_id)
        if result is None:  # pragma: no cover -- defensivo, se acaba de actualizar
            raise NotFoundException()
        return result

    async def enable(
        self, organization_id: UUID, reason: str, actor_user_id: UUID
    ) -> OrganizationRow:
        """RF-05."""
        org = await self._repo.get_organization_by_id(organization_id)
        if org is None:
            raise NotFoundException()
        if org.status != "disabled":
            raise ValidationError(field="status", message="La organizacion no esta deshabilitada.")

        updated = await self._repo.update_organization_status(organization_id, "active")
        if not updated:  # pragma: no cover -- defensivo, race entre el check y el UPDATE
            raise NotFoundException()

        # RN-05 (issue #10): mismo criterio que `disable()`.
        await audit(
            self._repo.session,
            organization_id=organization_id,
            action="org.enabled",
            entity_type="organization",
            entity_id=organization_id,
            before={"status": org.status},
            after={"status": "active", "reason": reason},
            user_id=actor_user_id,
        )
        await self._repo.commit()
        result = await self._repo.get_organization_by_id(organization_id)
        if result is None:  # pragma: no cover -- defensivo, se acaba de actualizar
            raise NotFoundException()
        return result


def get_organization_service(
    repo: SuperAdminRepository = Depends(get_superadmin_repository),
    settings: Settings = Depends(get_settings),
) -> OrganizationService:
    return OrganizationService(repo, settings)
