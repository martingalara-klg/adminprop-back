"""Cookies HttpOnly+Secure+SameSite=Lax para el access/refresh token (issue #6).

SDD: core/sdd_03_api_contracts.md parrafo "Convenciones Generales"
("JWT RS256 en HttpOnly Secure cookies (server-set en login),
SameSite=Lax."). core/sdd_04_nonfunctional.md §2.2/§2.4.
"""

from __future__ import annotations

from fastapi import Response

from adminprop.config import Settings

ACCESS_TOKEN_COOKIE = "access_token"
REFRESH_TOKEN_COOKIE = "refresh_token"


def set_auth_cookies(
    response: Response,
    *,
    access_token: str,
    refresh_token: str,
    settings: Settings,
) -> None:
    """Setea las dos cookies de sesion. Server-set, nunca legibles por JS (HttpOnly)."""
    response.set_cookie(
        key=ACCESS_TOKEN_COOKIE,
        value=access_token,
        max_age=settings.jwt_access_token_ttl_seconds,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="lax",
        domain=settings.cookie_domain,
        path="/",
    )
    response.set_cookie(
        key=REFRESH_TOKEN_COOKIE,
        value=refresh_token,
        max_age=settings.jwt_refresh_token_ttl_days * 24 * 60 * 60,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="lax",
        domain=settings.cookie_domain,
        # El refresh token solo se necesita en /v1/auth/refresh y
        # /v1/auth/logout -- restringir el path reduce la superficie de
        # exposicion (no viaja en cada request como el access token).
        path="/v1/auth",
    )


def clear_auth_cookies(response: Response, *, settings: Settings) -> None:
    """Limpia ambas cookies (logout). Los `path` deben matchear los de `set_auth_cookies`."""
    response.delete_cookie(
        key=ACCESS_TOKEN_COOKIE,
        domain=settings.cookie_domain,
        path="/",
        httponly=True,
        secure=settings.cookie_secure,
        samesite="lax",
    )
    response.delete_cookie(
        key=REFRESH_TOKEN_COOKIE,
        domain=settings.cookie_domain,
        path="/v1/auth",
        httponly=True,
        secure=settings.cookie_secure,
        samesite="lax",
    )
