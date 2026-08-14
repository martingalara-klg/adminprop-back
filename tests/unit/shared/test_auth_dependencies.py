"""Unit tests: dependency FastAPI que extrae/decodifica el access token (issue #6)."""

from uuid import uuid4

import pytest
from starlette.requests import Request

from adminprop.shared.auth import jwt as jwt_module
from adminprop.shared.auth.cookies import ACCESS_TOKEN_COOKIE
from adminprop.shared.auth.dependencies import (
    get_current_access_token_payload,
    requires_super_admin,
)
from adminprop.shared.errors.codes import SuperAdminRequiredException, UnauthorizedException


def _make_request(*, cookie_header: str | None = None, auth_header: str | None = None) -> Request:
    raw_headers = []
    if cookie_header:
        raw_headers.append((b"cookie", cookie_header.encode()))
    if auth_header:
        raw_headers.append((b"authorization", auth_header.encode()))
    scope = {
        "type": "http",
        "headers": raw_headers,
        "method": "GET",
        "path": "/",
        "query_string": b"",
        "server": ("test", 80),
        "client": ("test", 123),
        "scheme": "http",
    }
    return Request(scope)


class TestGetCurrentAccessTokenPayload:
    async def test_raises_unauthorized_without_cookie_or_header(self):
        request = _make_request()
        with pytest.raises(UnauthorizedException):
            await get_current_access_token_payload(request)

    async def test_decodes_token_from_cookie(self, monkeypatch):
        expected_payload = jwt_module.JWTPayload(
            sub=uuid4(), org_id=None, role=None, permissions=[], is_super_admin=True
        )
        monkeypatch.setattr(jwt_module, "decode_access_token", lambda token: expected_payload)
        request = _make_request(cookie_header=f"{ACCESS_TOKEN_COOKIE}=some-token")

        from adminprop.shared.auth import dependencies as dependencies_module

        monkeypatch.setattr(
            dependencies_module, "decode_access_token", lambda token: expected_payload
        )

        payload = await get_current_access_token_payload(request)
        assert payload is expected_payload

    async def test_decodes_token_from_bearer_header_when_no_cookie(self, monkeypatch):
        expected_payload = jwt_module.JWTPayload(
            sub=uuid4(), org_id=None, role=None, permissions=[], is_super_admin=True
        )
        from adminprop.shared.auth import dependencies as dependencies_module

        monkeypatch.setattr(
            dependencies_module, "decode_access_token", lambda token: expected_payload
        )

        request = _make_request(auth_header="Bearer some-token")
        payload = await get_current_access_token_payload(request)
        assert payload is expected_payload

    async def test_raises_unauthorized_when_authorization_header_is_not_bearer(self):
        request = _make_request(auth_header="Basic some-token")
        with pytest.raises(UnauthorizedException):
            await get_current_access_token_payload(request)


class TestRequiresSuperAdmin:
    """issue #7 -- CA-00-05: SUPERADMIN_REQUIRED para JWTs sin is_super_admin."""

    async def test_returns_payload_when_is_super_admin_true(self):
        request = _make_request(auth_header="Bearer irrelevant")
        payload = jwt_module.JWTPayload(
            sub=uuid4(), org_id=None, role=None, permissions=[], is_super_admin=True
        )

        result = await requires_super_admin(request, payload)

        assert result is payload

    async def test_raises_superadmin_required_for_non_super_admin_payload(self, monkeypatch):
        """Issue #10: `requires_super_admin` ahora audita `access.denied`
        (RN-A04) -- se mockea `record_access_denied` para mantener este
        test unitario puro (org_id fabricado, no sembrado en Postgres;
        la cobertura real del INSERT vive en
        tests/integration/shared/test_access_denied_audit.py)."""
        from adminprop.shared.auth import dependencies as dependencies_module

        async def _noop_record_access_denied(**kwargs: object) -> None:
            return None

        monkeypatch.setattr(dependencies_module, "record_access_denied", _noop_record_access_denied)

        request = _make_request(auth_header="Bearer irrelevant")
        payload = jwt_module.JWTPayload(
            sub=uuid4(),
            org_id=uuid4(),
            role="owner",
            permissions=["contract:manage", "user:manage"],
            is_super_admin=False,
        )

        with pytest.raises(SuperAdminRequiredException):
            await requires_super_admin(request, payload)

    async def test_denied_attempt_is_logged_with_user_id_and_path(self, caplog, monkeypatch):
        import logging

        from adminprop.shared.auth import dependencies as dependencies_module

        async def _noop_record_access_denied(**kwargs: object) -> None:
            return None

        monkeypatch.setattr(dependencies_module, "record_access_denied", _noop_record_access_denied)

        request = _make_request(auth_header="Bearer irrelevant")
        payload = jwt_module.JWTPayload(
            sub=uuid4(), org_id=uuid4(), role="admin", permissions=[], is_super_admin=False
        )

        with (
            caplog.at_level(logging.WARNING, logger="adminprop.shared.auth.dependencies"),
            pytest.raises(SuperAdminRequiredException),
        ):
            await requires_super_admin(request, payload)

        assert any("superadmin access denied" in record.message for record in caplog.records)
