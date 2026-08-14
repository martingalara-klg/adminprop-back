"""Logica de negocio de auth: login, logout, refresh (issue #6).

SDD: core/sdd_03_api_contracts.md parrafo 1. core/sdd_04_nonfunctional.md
parrafo 2.1/2.2/2.2a/2.3.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from uuid import UUID, uuid4

from fastapi import Depends
from redis.asyncio import Redis

from adminprop.config import Settings, get_settings
from adminprop.modules.auth.repository import (
    AuthRepository,
    MembershipRecord,
    UserRecord,
    get_auth_repository,
)
from adminprop.shared.auth.jwt import create_access_token
from adminprop.shared.auth.lockout import LoginLockout
from adminprop.shared.auth.passwords import verify_password
from adminprop.shared.auth.refresh_store import RefreshTokenStore
from adminprop.shared.cache.redis import get_redis_client
from adminprop.shared.errors.codes import (
    AccountLockedException,
    MembershipInactiveException,
    UnauthorizedException,
)


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
