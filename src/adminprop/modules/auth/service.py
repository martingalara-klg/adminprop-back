"""Logica de negocio de auth: login, logout, refresh (issue #6).

SDD: core/sdd_03_api_contracts.md parrafo 1. core/sdd_04_nonfunctional.md
parrafo 2.1/2.2/2.2a/2.3.
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from uuid import UUID, uuid4

from fastapi import Depends
from redis.asyncio import Redis

from adminprop.config import Settings, get_settings
from adminprop.modules.auth.repository import (
    AuthRepository,
    InvitationDetailRecord,
    MembershipRecord,
    UserRecord,
    get_auth_activation_repository,
    get_auth_repository,
)
from adminprop.shared.auth.jwt import create_access_token
from adminprop.shared.auth.lockout import LoginLockout
from adminprop.shared.auth.password_reset_store import (
    PasswordResetTokenStore,
    get_password_reset_token_store,
)
from adminprop.shared.auth.passwords import hash_password, verify_password
from adminprop.shared.auth.refresh_store import RefreshTokenStore
from adminprop.shared.cache.redis import get_redis_client
from adminprop.shared.errors.codes import (
    AccountLockedException,
    InvitationAlreadyAcceptedException,
    InvitationExpiredException,
    InvitationNotFoundException,
    MembershipInactiveException,
    NotFoundException,
    ResetTokenExpiredException,
    UnauthorizedException,
    UserAlreadyMemberException,
)
from adminprop.workers.notification_worker import send_transactional_email

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class AuthenticatedTokens:
    access_token: str
    refresh_token: str


@dataclass(frozen=True)
class LoginResult:
    status: str
    user: UserRecord | None
    organizations: list[MembershipRecord] = field(default_factory=list)
    tokens: AuthenticatedTokens | None = None


class AuthService:
    def __init__(self, repo: AuthRepository, redis: Redis, settings: Settings) -> None:
        self._repo = repo
        self._settings = settings
        self._lockout = LoginLockout(redis, settings)
        self._refresh_store = RefreshTokenStore(redis, settings)

    async def login(
        self, *, email: str, password: str, organization_id: UUID | None
    ) -> LoginResult:
        lock_status = await self._lockout.check(email)
        if lock_status.locked:
            raise AccountLockedException(
                details={"retry_after_seconds": lock_status.retry_after_seconds}
            )

        user = await self._repo.get_user_by_email(email)
        password_ok = verify_password(password, user.password_hash if user else None)

        if user is None or not password_ok:
            await self._lockout.register_failure(email)
            raise UnauthorizedException(message="Credenciales incorrectas.")

        await self._lockout.reset(email)

        if user.is_super_admin:
            return await self._issue_super_admin_session(user)

        return await self._issue_org_session(user, organization_id)

    async def _issue_super_admin_session(self, user: UserRecord) -> LoginResult:
        access_token = create_access_token(
            user_id=user.id,
            organization_id=None,
            role=None,
            permissions=[],
            is_super_admin=True,
            jti=str(uuid4()),
        )
        issued = await self._refresh_store.issue_family(user_id=user.id, organization_id=None)
        return LoginResult(
            status="authenticated",
            user=user,
            organizations=[],
            tokens=AuthenticatedTokens(access_token=access_token, refresh_token=issued.raw_token),
        )

    async def _issue_org_session(
        self, user: UserRecord, organization_id: UUID | None
    ) -> LoginResult:
        memberships = await self._repo.get_active_memberships(user.id)
        if not memberships:
            raise MembershipInactiveException()

        selected = self._select_membership(memberships, organization_id)
        if selected is None and organization_id is not None:
            raise MembershipInactiveException()

        if selected is None:
            return LoginResult(
                status="organization_selection_required", user=user, organizations=memberships
            )

        access_token = create_access_token(
            user_id=user.id,
            organization_id=selected.organization_id,
            role=selected.role_name,
            permissions=selected.permissions,
            is_super_admin=False,
            jti=str(uuid4()),
        )
        issued = await self._refresh_store.issue_family(
            user_id=user.id, organization_id=selected.organization_id
        )
        return LoginResult(
            status="authenticated",
            user=user,
            organizations=memberships,
            tokens=AuthenticatedTokens(access_token=access_token, refresh_token=issued.raw_token),
        )

    @staticmethod
    def _select_membership(
        memberships: list[MembershipRecord], organization_id: UUID | None
    ) -> MembershipRecord | None:
        if organization_id is not None:
            return next((m for m in memberships if m.organization_id == organization_id), None)
        if len(memberships) == 1:
            return memberships[0]
        return None

    async def logout(self, raw_refresh_token: str | None) -> None:
        if raw_refresh_token:
            await self._refresh_store.revoke_by_raw_token(raw_refresh_token)

    async def refresh(self, raw_refresh_token: str | None) -> AuthenticatedTokens:
        if not raw_refresh_token:
            raise UnauthorizedException()

        record, issued = await self._refresh_store.rotate(raw_refresh_token)

        if record.organization_id is None:
            user = await self._repo.get_user_by_id(record.user_id)
            if user is None or not user.is_super_admin:
                await self._refresh_store.revoke_family(record.family_id)
                raise UnauthorizedException()
            access_token = create_access_token(
                user_id=user.id,
                organization_id=None,
                role=None,
                permissions=[],
                is_super_admin=True,
                jti=str(uuid4()),
            )
            return AuthenticatedTokens(access_token=access_token, refresh_token=issued.raw_token)

        memberships = await self._repo.get_active_memberships(record.user_id)
        membership = next(
            (m for m in memberships if m.organization_id == record.organization_id), None
        )
        if membership is None:
            await self._refresh_store.revoke_family(record.family_id)
            raise MembershipInactiveException()

        access_token = create_access_token(
            user_id=record.user_id,
            organization_id=membership.organization_id,
            role=membership.role_name,
            permissions=membership.permissions,
            is_super_admin=False,
            jti=str(uuid4()),
        )
        return AuthenticatedTokens(access_token=access_token, refresh_token=issued.raw_token)


def get_auth_service(
    repo: AuthRepository = Depends(get_auth_repository),
    redis: Redis = Depends(get_redis_client),
    settings: Settings = Depends(get_settings),
) -> AuthService:
    return AuthService(repo, redis, settings)


# ─── issue #8 — Activacion de cuenta (accept-invitation) ───────────────────


def _hash_invitation_token(raw_token: str) -> str:
    """Mismo algoritmo que `modules/superadmin/service.py._hash_token` --
    debe producir exactamente el mismo hash que el usado al emitir la
    invitacion (sha256 hex), para que la busqueda por `token` coincida."""
    return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class ActivatedOrganization:
    id: UUID
    name: str
    role: str


@dataclass(frozen=True)
class AcceptInvitationResult:
    user: UserRecord
    organization: ActivatedOrganization
    tokens: AuthenticatedTokens


class AccountActivationService:
    """spec_module_00_superadmin.md "Flujo de Activacion de Cuenta" pasos
    2-5 (issue #8): valida el token de invitacion, y en la aceptacion crea
    el user + membresia + activa la organizacion en una unica transaccion
    (CA-00-03), emitiendo la sesion igual que `AuthService.login`."""

    def __init__(self, repo: AuthRepository, redis: Redis, settings: Settings) -> None:
        self._repo = repo
        self._settings = settings
        self._refresh_store = RefreshTokenStore(redis, settings)

    async def get_invitation(self, raw_token: str) -> InvitationDetailRecord:
        """GET /auth/invitation/:token (paso 2)."""
        invitation = await self._repo.get_invitation_by_token_hash(
            _hash_invitation_token(raw_token)
        )
        self._ensure_invitation_usable(invitation)
        assert invitation is not None
        return invitation

    async def accept_invitation(
        self, *, raw_token: str, full_name: str, password: str
    ) -> AcceptInvitationResult:
        """POST /auth/accept-invitation (pasos 3-5)."""
        invitation = await self._repo.get_invitation_by_token_hash(
            _hash_invitation_token(raw_token)
        )
        self._ensure_invitation_usable(invitation)
        assert invitation is not None

        existing_user = await self._repo.get_user_by_email(invitation.email)
        if existing_user is not None:
            # RN de implementacion (issue #8): reutilizar el user global si
            # el email ya existe, mientras no tenga membresia (activa o
            # inactiva) en ESTA organizacion -- en ese caso es conflicto.
            membership_status = await self._repo.get_membership_status(
                invitation.organization_id, existing_user.id
            )
            if membership_status is not None:
                raise UserAlreadyMemberException()
            user = existing_user
        else:
            user = await self._repo.create_user(
                email=invitation.email,
                password_hash=hash_password(password),
                full_name=full_name,
            )

        # Paso 4: "En una transaccion: se crea el user, la membresia con
        # rol owner, la invitacion pasa a accepted y la organizacion a
        # active." -- un unico commit al final confirma las cuatro
        # escrituras juntas.
        await self._repo.create_membership(
            organization_id=invitation.organization_id,
            user_id=user.id,
            role_id=invitation.role_id,
        )
        await self._repo.mark_invitation_accepted(invitation.id)
        await self._repo.activate_organization(invitation.organization_id)  # CA-00-03
        await self._repo.commit()

        # TODO(#10): persistir en `audit_logs` -- la tabla todavia no
        # existe (mismo criterio que modules/superadmin/service.py
        # disable/enable). Se deja constancia estructurada en el logger.
        logger.info(
            "account activated via invitation",
            extra={
                "organization_id": str(invitation.organization_id),
                "user_id": str(user.id),
                "service": "auth",
            },
        )

        # Paso 5: "El backend setea las cookies de sesion; el owner entra
        # directo a la app." -- mismo mecanismo que AuthService.login.
        access_token = create_access_token(
            user_id=user.id,
            organization_id=invitation.organization_id,
            role=invitation.role_name,
            permissions=invitation.role_permissions,
            is_super_admin=False,
            jti=str(uuid4()),
        )
        issued = await self._refresh_store.issue_family(
            user_id=user.id, organization_id=invitation.organization_id
        )
        return AcceptInvitationResult(
            user=user,
            organization=ActivatedOrganization(
                id=invitation.organization_id,
                name=invitation.organization_name,
                role=invitation.role_name,
            ),
            tokens=AuthenticatedTokens(access_token=access_token, refresh_token=issued.raw_token),
        )

    @staticmethod
    def _ensure_invitation_usable(invitation: InvitationDetailRecord | None) -> None:
        if invitation is None:
            raise InvitationNotFoundException()
        if invitation.status == "accepted":
            raise InvitationAlreadyAcceptedException()
        if invitation.status != "pending":
            # `revoked` (o el valor defensivo `expired`, que ningun flujo
            # actual asigna): no se distingue de "no existe" para no
            # revelar el ciclo de vida de la invitacion (mismo criterio que
            # INVITATION_NOT_FOUND para tokens desconocidos).
            raise InvitationNotFoundException()
        if invitation.expires_at < datetime.now(UTC):
            raise InvitationExpiredException()


def get_account_activation_service(
    repo: AuthRepository = Depends(get_auth_activation_repository),
    redis: Redis = Depends(get_redis_client),
    settings: Settings = Depends(get_settings),
) -> AccountActivationService:
    return AccountActivationService(repo, redis, settings)


# ─── issue #8 — Forgot / reset password ────────────────────────────────────


class PasswordResetService:
    """sdd_03 §1 forgot-password/reset-password. sdd_04 §2.2a
    anti-enumeration: forgot-password siempre "tiene exito" de cara al
    cliente, exista o no el email -- el envio real (y la creacion del
    token) solo ocurre si existe."""

    def __init__(
        self,
        repo: AuthRepository,
        token_store: PasswordResetTokenStore,
        refresh_store: RefreshTokenStore,
        settings: Settings,
    ) -> None:
        self._repo = repo
        self._token_store = token_store
        self._refresh_store = refresh_store
        self._settings = settings

    async def forgot_password(self, email: str, request_id: str) -> None:
        user = await self._repo.get_user_by_email(email)
        if user is None:
            # Anti-enumeration (sdd_04 §2.2a): el router siempre responde
            # 200 con el mismo texto, exista o no el email -- acá sólo se
            # evita crear el token/enviar el mail.
            return
        raw_token = await self._token_store.issue(user_id=user.id, email=user.email)
        self._send_reset_email(user=user, raw_token=raw_token, request_id=request_id)

    async def get_reset_token_email(self, raw_token: str) -> str:
        """GET /auth/reset-password/:token -- 200 | 404 | 410."""
        status = await self._token_store.peek(raw_token)
        if not status.exists:
            raise NotFoundException()
        if status.expired:
            raise ResetTokenExpiredException()
        assert status.email is not None
        return status.email

    async def reset_password(self, raw_token: str, password: str) -> None:
        """POST /auth/reset-password -- consume el token (un solo uso),
        actualiza el password y cierra todas las sesiones existentes del
        usuario (revocacion de refresh tokens, sdd_04 §2.2)."""
        status = await self._token_store.consume(raw_token)
        if not status.exists:
            raise NotFoundException()
        if status.expired:
            raise ResetTokenExpiredException()
        assert status.user_id is not None

        await self._repo.update_password_hash(status.user_id, hash_password(password))
        await self._repo.commit()
        await self._refresh_store.revoke_all_families_for_user(status.user_id)

    def _send_reset_email(self, *, user: UserRecord, raw_token: str, request_id: str) -> None:
        link = f"{self._settings.frontend_base_url}/reset-password?token={raw_token}"
        ttl_minutes = self._settings.password_reset_token_ttl_seconds // 60
        # `organization_name` es requerido por `send_transactional_email`
        # (encabezado "From" dinamico, spec_notificaciones.md §Email) pero
        # el reset de password es una accion a nivel de usuario, no de
        # organizacion -- se usa el nombre del producto como fallback
        # (decision documentada en el PR del issue #8).
        send_transactional_email.delay(
            to=[user.email],
            subject="Restablecer tu contrasena -- AdminProp",
            html=(
                "<p>Recibimos un pedido para restablecer tu contrasena de AdminProp.</p>"
                f'<p><a href="{link}">Restablecer mi contrasena</a></p>'
                f"<p>Este link expira en {ttl_minutes} minutos. "
                "Si no fuiste vos, ignora este email.</p>"
            ),
            text=(
                "Recibimos un pedido para restablecer tu contrasena de AdminProp. "
                f"Restablecela aca: {link} (expira en {ttl_minutes} minutos). "
                "Si no fuiste vos, ignora este email."
            ),
            organization_name="AdminProp",
            request_id=request_id,
        )


def get_password_reset_service(
    repo: AuthRepository = Depends(get_auth_repository),
    token_store: PasswordResetTokenStore = Depends(get_password_reset_token_store),
    redis: Redis = Depends(get_redis_client),
    settings: Settings = Depends(get_settings),
) -> PasswordResetService:
    return PasswordResetService(repo, token_store, RefreshTokenStore(redis, settings), settings)
