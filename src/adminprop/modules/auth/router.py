"""Endpoints /v1/auth/login, /v1/auth/logout, /v1/auth/refresh (issue #6).

SDD: core/sdd_03_api_contracts.md parrafo 1 "Autenticacion".
Implements: CA del issue #6 (login/logout/refresh, cookies HttpOnly,
lockout, anti-enumeration, rate limit).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request, Response, status

from adminprop.config import Settings, get_settings
from adminprop.modules.auth.schemas import (
    AcceptInvitationOrganization,
    AcceptInvitationRequest,
    AcceptInvitationResponse,
    AcceptInvitationResponseData,
    ForgotPasswordRequest,
    ForgotPasswordResponse,
    ForgotPasswordResponseData,
    InvitationDetailResponse,
    InvitationDetailResponseData,
    LoginRequest,
    LoginResponse,
    LoginResponseData,
    OrganizationSummary,
    RefreshResponse,
    RefreshResponseData,
    ResetPasswordRequest,
    ResetPasswordResponse,
    ResetPasswordResponseData,
    ResetPasswordTokenResponse,
    ResetPasswordTokenResponseData,
    UserSummary,
)
from adminprop.modules.auth.service import (
    AccountActivationService,
    AuthService,
    LoginResult,
    PasswordResetService,
    get_account_activation_service,
    get_auth_service,
    get_password_reset_service,
)
from adminprop.shared.auth.cookies import (
    REFRESH_TOKEN_COOKIE,
    clear_auth_cookies,
    set_auth_cookies,
)
from adminprop.shared.logging.json_logger import request_id_var
from adminprop.shared.rate_limit.token_bucket import rate_limit_by_ip

router = APIRouter(prefix="/v1/auth", tags=["auth"])

# sdd_04 §2.2a (texto literal -- no traducir ni "mejorar"): anti-enumeration
# de forgot-password, idéntico exista o no el email.
_FORGOT_PASSWORD_MESSAGE = (
    "Si el email está registrado, recibirás instrucciones para restablecer "
    "tu contraseña en los próximos minutos."
)


def _request_id() -> str:
    return request_id_var.get() or ""


def _to_login_response(result: LoginResult) -> LoginResponse:
    return LoginResponse(
        data=LoginResponseData(
            status=result.status,
            user=UserSummary.model_validate(result.user) if result.user else None,
            organizations=[
                OrganizationSummary(
                    id=m.organization_id, name=m.organization_name, role=m.role_name
                )
                for m in result.organizations
            ],
        )
    )


@router.post(
    "/login",
    status_code=status.HTTP_200_OK,
    response_model=LoginResponse,
    dependencies=[
        # sdd_04 §2.5: POST /auth/login -- 10 req / IP / 10 min.
        Depends(rate_limit_by_ip("auth_login", 10, 10 * 60)),
    ],
)
async def login(
    dto: LoginRequest,
    response: Response,
    settings: Settings = Depends(get_settings),
    service: AuthService = Depends(get_auth_service),
) -> LoginResponse:
    """SDD: sdd_03 parrafo 1. Implements login con lockout + anti-enumeration."""
    result = await service.login(
        email=dto.email, password=dto.password, organization_id=dto.organization_id
    )
    if result.tokens is not None:
        set_auth_cookies(
            response,
            access_token=result.tokens.access_token,
            refresh_token=result.tokens.refresh_token,
            settings=settings,
        )
    return _to_login_response(result)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(
    request: Request,
    response: Response,
    settings: Settings = Depends(get_settings),
    service: AuthService = Depends(get_auth_service),
) -> Response:
    """SDD: sdd_03 parrafo 1 -- "204 (invalida refresh server-side, limpia cookies)"."""
    raw_refresh_token = request.cookies.get(REFRESH_TOKEN_COOKIE)
    await service.logout(raw_refresh_token)
    clear_auth_cookies(response, settings=settings)
    response.status_code = status.HTTP_204_NO_CONTENT
    return response


@router.post(
    "/refresh",
    status_code=status.HTTP_200_OK,
    response_model=RefreshResponse,
    dependencies=[
        # sdd_04 §2.5 declara el limite "por usuario"; se aplica por IP
        # como sustituto pragmatico (decision de implementacion, ver PR):
        # el token de refresh todavia no fue validado en este punto, y
        # `rotate()` es single-use -- consultarlo solo para computar la
        # clave del rate limit rompe esa invariante o duplica el consumo.
        Depends(rate_limit_by_ip("auth_refresh", 60, 60 * 60)),
    ],
)
async def refresh(
    request: Request,
    response: Response,
    settings: Settings = Depends(get_settings),
    service: AuthService = Depends(get_auth_service),
) -> RefreshResponse:
    """SDD: sdd_03 parrafo 1 -- "200 (rota refresh token; cookie nueva)"."""
    raw_refresh_token = request.cookies.get(REFRESH_TOKEN_COOKIE)
    tokens = await service.refresh(raw_refresh_token)
    set_auth_cookies(
        response,
        access_token=tokens.access_token,
        refresh_token=tokens.refresh_token,
        settings=settings,
    )
    return RefreshResponse(data=RefreshResponseData())


# ─── issue #8 — Activacion de cuenta ────────────────────────────────────────


@router.get(
    "/invitation/{token}",
    status_code=status.HTTP_200_OK,
    response_model=InvitationDetailResponse,
)
async def get_invitation(
    token: str,
    service: AccountActivationService = Depends(get_account_activation_service),
) -> InvitationDetailResponse:
    """SDD: sdd_03 §1 "GET /auth/invitation/:token".

    spec_module_00_superadmin.md "Flujo de Activacion de Cuenta" paso 2:
    el frontend valida el token antes de mostrar el formulario de
    activacion. INVITATION_NOT_FOUND (404) / INVITATION_EXPIRED (410) si
    no es usable.
    """
    invitation = await service.get_invitation(token)
    return InvitationDetailResponse(
        data=InvitationDetailResponseData(
            email=invitation.email,
            organization_name=invitation.organization_name,
            role_name=invitation.role_name,
        )
    )


@router.post(
    "/accept-invitation",
    status_code=status.HTTP_201_CREATED,
    response_model=AcceptInvitationResponse,
)
async def accept_invitation(
    dto: AcceptInvitationRequest,
    response: Response,
    settings: Settings = Depends(get_settings),
    service: AccountActivationService = Depends(get_account_activation_service),
) -> AcceptInvitationResponse:
    """SDD: sdd_03 §1 "POST /auth/accept-invitation -> 201 (nombre +
    password; setea cookies)".

    Implements: CA-00-03 ("la organizacion pasa a active y el owner queda
    logueado con rol owner"). Errores: INVITATION_NOT_FOUND (404),
    INVITATION_EXPIRED (410), INVITATION_ALREADY_ACCEPTED (409),
    USER_ALREADY_MEMBER (409), VALIDATION_ERROR (400, password/nombre).
    """
    result = await service.accept_invitation(
        raw_token=dto.token, full_name=dto.full_name, password=dto.password
    )
    set_auth_cookies(
        response,
        access_token=result.tokens.access_token,
        refresh_token=result.tokens.refresh_token,
        settings=settings,
    )
    return AcceptInvitationResponse(
        data=AcceptInvitationResponseData(
            user=UserSummary.model_validate(result.user),
            organization=AcceptInvitationOrganization(
                id=result.organization.id,
                name=result.organization.name,
                role=result.organization.role,
            ),
        )
    )


# ─── issue #8 — Forgot / reset password ─────────────────────────────────────


@router.post(
    "/forgot-password",
    status_code=status.HTTP_200_OK,
    response_model=ForgotPasswordResponse,
    dependencies=[
        # sdd_04 §2.5: POST /auth/forgot-password -- 5 req / IP / hora.
        Depends(rate_limit_by_ip("auth_forgot_password", 5, 60 * 60)),
    ],
)
async def forgot_password(
    dto: ForgotPasswordRequest,
    service: PasswordResetService = Depends(get_password_reset_service),
) -> ForgotPasswordResponse:
    """SDD: sdd_03 §1 "POST /auth/forgot-password -> 200 SIEMPRE
    (anti-enumeration)". sdd_04 §2.2a: texto literal, exista o no el email.
    """
    await service.forgot_password(dto.email, _request_id())
    return ForgotPasswordResponse(data=ForgotPasswordResponseData(message=_FORGOT_PASSWORD_MESSAGE))


@router.get(
    "/reset-password/{token}",
    status_code=status.HTTP_200_OK,
    response_model=ResetPasswordTokenResponse,
)
async def get_reset_password_token(
    token: str,
    service: PasswordResetService = Depends(get_password_reset_service),
) -> ResetPasswordTokenResponse:
    """SDD: sdd_03 §1 "GET /auth/reset-password/:token -> 200 | 404 | 410".

    404 NOT_FOUND: token desconocido o ya usado. 410 RESET_TOKEN_EXPIRED
    (agregado a sdd_03 en este PR, ver `shared/errors/codes.py`): el token
    existio pero vencio su ventana de 1h.
    """
    email = await service.get_reset_token_email(token)
    return ResetPasswordTokenResponse(data=ResetPasswordTokenResponseData(email=email))


@router.post(
    "/reset-password",
    status_code=status.HTTP_200_OK,
    response_model=ResetPasswordResponse,
)
async def reset_password(
    dto: ResetPasswordRequest,
    service: PasswordResetService = Depends(get_password_reset_service),
) -> ResetPasswordResponse:
    """SDD: sdd_03 §1 "POST /auth/reset-password -> 200".

    Consume el token (un solo uso) y revoca todas las sesiones existentes
    del usuario (sdd_04 §2.2, refresh tokens revocables server-side).
    """
    await service.reset_password(dto.token, dto.password)
    return ResetPasswordResponse(data=ResetPasswordResponseData())
