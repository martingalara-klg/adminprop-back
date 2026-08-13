"""Unit tests: cookies HttpOnly+Secure+SameSite=Lax (issue #6).

SDD: core/sdd_03_api_contracts.md parrafo "Convenciones Generales".
"""

from fastapi import Response

from adminprop.config import Settings
from adminprop.shared.auth.cookies import (
    ACCESS_TOKEN_COOKIE,
    REFRESH_TOKEN_COOKIE,
    clear_auth_cookies,
    set_auth_cookies,
)


def _settings() -> Settings:
    return Settings(cookie_secure=True, cookie_domain=None)


class TestSetAuthCookies:
    def test_sets_both_cookies_httponly_secure_samesite_lax(self):
        response = Response()
        set_auth_cookies(
            response, access_token="access-tok", refresh_token="refresh-tok", settings=_settings()
        )
        set_cookie_headers = response.headers.getlist("set-cookie")
        assert len(set_cookie_headers) == 2

        access_header = next(h for h in set_cookie_headers if h.startswith(ACCESS_TOKEN_COOKIE))
        refresh_header = next(h for h in set_cookie_headers if h.startswith(REFRESH_TOKEN_COOKIE))

        for header in (access_header, refresh_header):
            assert "HttpOnly" in header
            assert "Secure" in header
            assert "samesite=lax" in header.lower()

        assert "access-tok" in access_header
        assert "refresh-tok" in refresh_header

    def test_refresh_cookie_is_scoped_to_auth_path(self):
        """El refresh token solo viaja a /v1/auth/* -- reduce exposicion."""
        response = Response()
        set_auth_cookies(
            response, access_token="access-tok", refresh_token="refresh-tok", settings=_settings()
        )
        refresh_header = next(
            h for h in response.headers.getlist("set-cookie") if h.startswith(REFRESH_TOKEN_COOKIE)
        )
        assert "Path=/v1/auth" in refresh_header

    def test_access_cookie_is_scoped_to_root_path(self):
        response = Response()
        set_auth_cookies(
            response, access_token="access-tok", refresh_token="refresh-tok", settings=_settings()
        )
        access_header = next(
            h for h in response.headers.getlist("set-cookie") if h.startswith(ACCESS_TOKEN_COOKIE)
        )
        assert "Path=/" in access_header


class TestClearAuthCookies:
    def test_clears_both_cookies_with_expired_max_age(self):
        response = Response()
        clear_auth_cookies(response, settings=_settings())
        set_cookie_headers = response.headers.getlist("set-cookie")
        assert len(set_cookie_headers) == 2
        for header in set_cookie_headers:
            assert "max-age=0" in header.lower() or "expires=" in header.lower()
