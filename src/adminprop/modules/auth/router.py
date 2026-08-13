"""Endpoints /v1/auth/login, /v1/auth/logout, /v1/auth/refresh (issue #6).

SDD: core/sdd_03_api_contracts.md parrafo 1 "Autenticacion".
Implements: CA del issue #6 (login/logout/refresh, cookies HttpOnly,
lockout, anti-enumeration, rate limit).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request, Response, status

from adminprop.config import Settings, get_settings
from adminprop.modules.auth.schemas import (
    LoginRequest,
    LoginResponse,
    LoginResponseData,
    OrganizationSummary,
    RefreshResponse,
    RefreshResponseData,
    UserSummary,
)
from adminprop.modules.auth.service import AuthService, LoginResult, get_auth_service
from adminprop.shared.auth.cookies import (
    REFRESH_TOKEN_COOKIE,
    clear_auth_cookies,
    set_auth_cookies,
)
from adminprop.shared.rate_limit.token_bucket import rate_limit_by_ip

router = APIRouter(prefix="/v1/auth", tags=["auth"])


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
