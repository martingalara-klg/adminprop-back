"""Unit tests: JWT RS256 -- emision y decodificacion (issue #6).

SDD: core/sdd_03_api_contracts.md parrafo "Convenciones Generales"
(shape del JWT). core/sdd_04_nonfunctional.md parrafo 2.2 (RS256, access 8h).
"""

import time
from uuid import uuid4

import jwt as pyjwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from adminprop.config import get_settings
from adminprop.shared.auth import jwt as jwt_module
from adminprop.shared.errors.codes import UnauthorizedException


@pytest.fixture()
def rsa_keypair(tmp_path, monkeypatch):
    """Genera un par RS256 efimero solo para tests -- nunca se commitea
    (issue #6: "generar un par SOLO para tests/dev... nunca claves de
    produccion").
    """
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.TraditionalOpenSSL,
        encryption_algorithm=serialization.NoEncryption(),
    )
    public_pem = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    private_path = tmp_path / "private.pem"
    public_path = tmp_path / "public.pem"
    private_path.write_bytes(private_pem)
    public_path.write_bytes(public_pem)

    monkeypatch.setenv("JWT_PRIVATE_KEY_PATH", str(private_path))
    monkeypatch.setenv("JWT_PUBLIC_KEY_PATH", str(public_path))
    get_settings.cache_clear()
    jwt_module.clear_key_cache()
    yield
    get_settings.cache_clear()
    jwt_module.clear_key_cache()


class TestCreateAndDecodeAccessToken:
    def test_decode_roundtrip_preserves_org_role_permissions(self, rsa_keypair):
        user_id = uuid4()
        org_id = uuid4()
        token = jwt_module.create_access_token(
            user_id=user_id,
            organization_id=org_id,
            role="owner",
            permissions=["contract:read", "contract:manage"],
            is_super_admin=False,
            jti="jti-1",
        )
        payload = jwt_module.decode_access_token(token)

        assert payload.sub == user_id
        assert payload.org_id == org_id
        assert payload.role == "owner"
        assert payload.permissions == ["contract:read", "contract:manage"]
        assert payload.is_super_admin is False
        assert payload.jti == "jti-1"

    def test_super_admin_token_has_no_org_or_role(self, rsa_keypair):
        """sdd_03 "Convenciones Generales": "En /superadmin/* el JWT no
        lleva org ni role.".
        """
        user_id = uuid4()
        token = jwt_module.create_access_token(
            user_id=user_id,
            organization_id=None,
            role=None,
            permissions=[],
            is_super_admin=True,
            jti="jti-2",
        )
        payload = jwt_module.decode_access_token(token)

        assert payload.org_id is None
        assert payload.role is None
        assert payload.is_super_admin is True

    def test_decode_uses_rs256_algorithm(self, rsa_keypair):
        token = jwt_module.create_access_token(
            user_id=uuid4(),
            organization_id=None,
            role=None,
            permissions=[],
            is_super_admin=True,
            jti="jti-3",
        )
        header = pyjwt.get_unverified_header(token)
        assert header["alg"] == "RS256"

    def test_decode_expired_token_raises_unauthorized(self, rsa_keypair):
        settings = get_settings()
        now = int(time.time())
        expired_payload = {
            "sub": str(uuid4()),
            "org": None,
            "role": None,
            "permissions": [],
            "is_super_admin": False,
            "iat": now - 100,
            "exp": now - 50,
            "jti": "expired",
        }
        token = pyjwt.encode(
            expired_payload, jwt_module._load_private_key(), algorithm=settings.jwt_algorithm
        )
        with pytest.raises(UnauthorizedException):
            jwt_module.decode_access_token(token)

    def test_decode_tampered_token_raises_unauthorized(self, rsa_keypair):
        token = jwt_module.create_access_token(
            user_id=uuid4(),
            organization_id=None,
            role=None,
            permissions=[],
            is_super_admin=True,
            jti="jti-4",
        )
        header_part, payload_part, signature_part = token.split(".")
        tampered_payload = payload_part[:-1] + ("a" if payload_part[-1] != "a" else "b")
        tampered = f"{header_part}.{tampered_payload}.{signature_part}"
        with pytest.raises(UnauthorizedException):
            jwt_module.decode_access_token(tampered)

    def test_decode_garbage_token_raises_unauthorized(self, rsa_keypair):
        with pytest.raises(UnauthorizedException):
            jwt_module.decode_access_token("not-a-jwt")
