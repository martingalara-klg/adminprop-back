"""Fixtures compartidas de tests/integration/people (issue #13).

Mismo patron de engine/session-factory fresco por test que
tests/integration/administracion/conftest.py (evita "Future attached to a
different loop" entre tests async de pytest-asyncio). El `Seeder` se
duplica deliberadamente -- mismo criterio que el repo ya aplica entre
`auth`, `superadmin` y `administracion`.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncGenerator
from datetime import UTC, date, datetime

import pytest
import sqlalchemy as sa
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from httpx import ASGITransport, AsyncClient

from adminprop.config import get_settings
from adminprop.db.session import get_engine, get_session_factory
from adminprop.main import create_app
from adminprop.modules.superadmin.provisioning import (
    DEFAULT_ORGANIZATION_SETTINGS,
    ROLE_DEFINITIONS,
)
from adminprop.shared.auth import jwt as jwt_module
from adminprop.shared.auth.jwt import create_access_token
from adminprop.shared.auth.passwords import hash_password
from adminprop.shared.cache.redis import get_redis_client


@pytest.fixture(autouse=True)
async def _fresh_engine_per_test() -> AsyncGenerator[None]:
    get_session_factory.cache_clear()
    get_engine.cache_clear()
    get_redis_client.cache_clear()
    yield
    engine = get_engine()
    await engine.dispose()
    get_session_factory.cache_clear()
    get_engine.cache_clear()
    redis = get_redis_client()
    await redis.flushdb()
    await redis.aclose()
    get_redis_client.cache_clear()


@pytest.fixture()
def rsa_keypair(tmp_path, monkeypatch):
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


@pytest.fixture()
async def client(rsa_keypair) -> AsyncGenerator[AsyncClient]:
    transport = ASGITransport(app=create_app())
    async with AsyncClient(transport=transport, base_url="https://testserver") as async_client:
        yield async_client


def _unique_email() -> str:
    return f"user-{uuid.uuid4().hex[:12]}@example.com"


def _auth_headers(
    *, user_id: uuid.UUID, organization_id: uuid.UUID, role_name: str, permissions: list[str]
) -> dict[str, str]:
    token = create_access_token(
        user_id=user_id,
        organization_id=organization_id,
        role=role_name,
        permissions=permissions,
        is_super_admin=False,
        jti=str(uuid.uuid4()),
    )
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture()
def seed(rsa_keypair):
    class Seeder:
        def __init__(self) -> None:
            self.created_org_ids: list[uuid.UUID] = []
            self.created_user_ids: list[uuid.UUID] = []

        async def create_user(
            self,
            *,
            password: str = "Password1234",
            is_super_admin: bool = False,
            email: str | None = None,
        ) -> dict:
            user_id = uuid.uuid4()
            email = email or _unique_email()
            session_factory = get_session_factory()
            async with session_factory() as session, session.begin():
                await session.execute(sa.text("SET LOCAL ROLE adminprop_superadmin"))
                await session.execute(
                    sa.text(
                        "INSERT INTO users (id, email, password_hash, full_name, is_super_admin) "
                        "VALUES (:id, :email, :password_hash, :full_name, :is_super_admin)"
                    ),
                    {
                        "id": str(user_id),
                        "email": email,
                        "password_hash": hash_password(password),
                        "full_name": "Test User",
                        "is_super_admin": is_super_admin,
                    },
                )
            self.created_user_ids.append(user_id)
            return {"id": user_id, "email": email, "password": password}

        async def create_organization(
            self, *, status: str = "active", name: str | None = None
        ) -> uuid.UUID:
            org_id = uuid.uuid4()
            name = name or f"Org {org_id.hex[:8]}"
            session_factory = get_session_factory()
            async with session_factory() as session, session.begin():
                await session.execute(sa.text("SET LOCAL ROLE adminprop_superadmin"))
                await session.execute(
                    sa.text(
                        "INSERT INTO organizations (id, slug, name, status) "
                        "VALUES (:id, :slug, :name, :status)"
                    ),
                    {
                        "id": str(org_id),
                        "slug": f"org-{org_id.hex[:8]}",
                        "name": name,
                        "status": status,
                    },
                )
            self.created_org_ids.append(org_id)
            return org_id

        async def create_role(
            self,
            organization_id: uuid.UUID,
            *,
            name: str = "owner",
            permissions: list[str] | None = None,
        ) -> uuid.UUID:
            role_id = uuid.uuid4()
            permissions = permissions if permissions is not None else ["landlord:manage"]
            session_factory = get_session_factory()
            async with session_factory() as session, session.begin():
                await session.execute(sa.text("SET LOCAL ROLE adminprop_superadmin"))
                await session.execute(
                    sa.text(
                        "INSERT INTO roles (id, organization_id, name, permissions) "
                        "VALUES (:id, :org_id, :name, :permissions)"
                    ).bindparams(sa.bindparam("permissions", type_=sa.JSON)),
                    {
                        "id": str(role_id),
                        "org_id": str(organization_id),
                        "name": name,
                        "permissions": json.dumps(permissions),
                    },
                )
            return role_id

        async def create_membership(
            self,
            *,
            user_id: uuid.UUID,
            organization_id: uuid.UUID,
            role_id: uuid.UUID,
            status: str = "active",
        ) -> None:
            session_factory = get_session_factory()
            async with session_factory() as session, session.begin():
                await session.execute(sa.text("SET LOCAL ROLE adminprop_superadmin"))
                await session.execute(
                    sa.text(
                        "INSERT INTO organization_members "
                        "(organization_id, user_id, role_id, status) "
                        "VALUES (:org_id, :user_id, :role_id, :status)"
                    ),
                    {
                        "org_id": str(organization_id),
                        "user_id": str(user_id),
                        "role_id": str(role_id),
                        "status": status,
                    },
                )

        async def create_organization_with_system_roles(
            self, *, status: str = "active", name: str | None = None
        ) -> dict:
            """Siembra una organizacion `active` con sus 3 roles de sistema
            reales (`ROLE_DEFINITIONS`) -- mismo catalogo de permisos que
            la organizacion real (owner Y admin tienen `landlord:manage`/
            `renter:manage`; `maintenance` no tiene ninguno, CA-02-07)."""
            org_id = await self.create_organization(status=status, name=name)
            session_factory = get_session_factory()
            async with session_factory() as session, session.begin():
                await session.execute(sa.text("SET LOCAL ROLE adminprop_superadmin"))
                await session.execute(
                    sa.text(
                        "UPDATE organizations SET settings = :settings WHERE id = :id"
                    ).bindparams(sa.bindparam("settings", type_=sa.JSON)),
                    {"id": str(org_id), "settings": json.dumps(DEFAULT_ORGANIZATION_SETTINGS)},
                )
            role_ids: dict[str, uuid.UUID] = {}
            for role_name, permissions in ROLE_DEFINITIONS:
                role_ids[role_name] = await self.create_role(
                    org_id, name=role_name, permissions=list(permissions)
                )
            return {"organization_id": org_id, "roles": role_ids}

        async def add_member(
            self,
            *,
            organization_id: uuid.UUID,
            role_id: uuid.UUID,
            role_name: str,
            status: str = "active",
            password: str = "Password1234",
            email: str | None = None,
        ) -> dict:
            user = await self.create_user(password=password, email=email)
            await self.create_membership(
                user_id=user["id"],
                organization_id=organization_id,
                role_id=role_id,
                status=status,
            )
            permissions = next((list(p) for name, p in ROLE_DEFINITIONS if name == role_name), [])
            headers = _auth_headers(
                user_id=user["id"],
                organization_id=organization_id,
                role_name=role_name,
                permissions=permissions,
            )
            return {
                **user,
                "organization_id": organization_id,
                "role_name": role_name,
                "headers": headers,
            }

        # ─── helpers propios de personas (issue #13) ──────────────────────

        async def create_landlord_row(
            self,
            *,
            organization_id: uuid.UUID,
            name: str = "Propietario de prueba",
            commission_pct: str = "10.00",
            bank_info: str | None = "CBU 000000",
            deleted: bool = False,
        ) -> uuid.UUID:
            """Siembra un `landlord` directamente en DB (bypass del API) --
            usado por los tests de aislamiento cross-tenant, que necesitan
            un recurso en la organizacion B sin usar `client`. `bank_info`
            se cifra con el mismo `pgp_sym_encrypt` que el repository
            real (mismo `ENCRYPTION_KEY` de settings), para que el dato
            sembrado sea representativo."""
            landlord_id = uuid.uuid4()
            settings = get_settings()
            session_factory = get_session_factory()
            async with session_factory() as session, session.begin():
                await session.execute(sa.text("SET LOCAL ROLE adminprop_superadmin"))
                bank_info_expr = (
                    "pgp_sym_encrypt(:bank_info, :key, 'cipher-algo=aes256')"
                    if bank_info is not None
                    else "NULL"
                )
                deleted_at = datetime.now(UTC) if deleted else None
                await session.execute(
                    sa.text(
                        f"""
                        INSERT INTO landlords
                            (id, organization_id, name, commission_pct, bank_info, deleted_at)
                        VALUES
                            (:id, :org_id, :name, :commission_pct, {bank_info_expr}, :deleted_at)
                        """
                    ),
                    {
                        "id": str(landlord_id),
                        "org_id": str(organization_id),
                        "name": name,
                        "commission_pct": commission_pct,
                        "bank_info": bank_info,
                        "key": settings.encryption_key,
                        "deleted_at": deleted_at,
                    },
                )
            return landlord_id

        async def create_renter_row(
            self,
            *,
            organization_id: uuid.UUID,
            name: str = "Inquilino de prueba",
            deleted: bool = False,
        ) -> uuid.UUID:
            renter_id = uuid.uuid4()
            session_factory = get_session_factory()
            async with session_factory() as session, session.begin():
                await session.execute(sa.text("SET LOCAL ROLE adminprop_superadmin"))
                deleted_at = datetime.now(UTC) if deleted else None
                await session.execute(
                    sa.text(
                        "INSERT INTO renters (id, organization_id, name, deleted_at) "
                        "VALUES (:id, :org_id, :name, :deleted_at)"
                    ),
                    {
                        "id": str(renter_id),
                        "org_id": str(organization_id),
                        "name": name,
                        "deleted_at": deleted_at,
                    },
                )
            return renter_id

        # ─── helpers de cobranzas para CA-02-05 (issue #23) ────────────────
        # Duplicados de tests/integration/payments/conftest.py -- mismo
        # criterio documentado arriba: el Seeder se duplica entre modulos.

        async def create_property_row(
            self,
            *,
            organization_id: uuid.UUID,
            landlord_id: uuid.UUID,
            address: str = "Av. Test 123",
            status: str = "rented",
        ) -> uuid.UUID:
            property_id = uuid.uuid4()
            session_factory = get_session_factory()
            async with session_factory() as session, session.begin():
                await session.execute(sa.text("SET LOCAL ROLE adminprop_superadmin"))
                await session.execute(
                    sa.text(
                        "INSERT INTO properties "
                        "(id, organization_id, landlord_id, address, property_type, status) "
                        "VALUES (:id, :org_id, :landlord_id, :address, 'departamento', :status)"
                    ),
                    {
                        "id": str(property_id),
                        "org_id": str(organization_id),
                        "landlord_id": str(landlord_id),
                        "address": address,
                        "status": status,
                    },
                )
            return property_id

        async def create_contract_row(
            self,
            *,
            organization_id: uuid.UUID,
            property_id: uuid.UUID,
            renter_id: uuid.UUID,
            currency: str = "ARS",
            initial_amount: str = "100000.00",
            current_amount: str | None = None,
            start_date: str = "2026-01-01",
            end_date: str = "2027-01-01",
            daily_late_fee_pct: str = "0.1",
            status: str = "active",
        ) -> uuid.UUID:
            contract_id = uuid.uuid4()
            current_amount = current_amount if current_amount is not None else initial_amount
            session_factory = get_session_factory()
            async with session_factory() as session, session.begin():
                await session.execute(sa.text("SET LOCAL ROLE adminprop_superadmin"))
                await session.execute(
                    sa.text(
                        "INSERT INTO contracts "
                        "(id, organization_id, property_id, renter_id, currency, "
                        "initial_amount, current_amount, start_date, end_date, "
                        "daily_late_fee_pct, status) "
                        "VALUES (:id, :org_id, :property_id, :renter_id, :currency, "
                        ":initial_amount, :current_amount, :start_date, :end_date, "
                        ":daily_late_fee_pct, :status)"
                    ),
                    {
                        "id": str(contract_id),
                        "org_id": str(organization_id),
                        "property_id": str(property_id),
                        "renter_id": str(renter_id),
                        "currency": currency,
                        "initial_amount": initial_amount,
                        "current_amount": current_amount,
                        "start_date": date.fromisoformat(start_date),
                        "end_date": date.fromisoformat(end_date),
                        "daily_late_fee_pct": daily_late_fee_pct,
                        "status": status,
                    },
                )
            return contract_id

        async def create_rent_period_row(
            self,
            *,
            organization_id: uuid.UUID,
            contract_id: uuid.UUID,
            period: str = "2026-06-01",
            amount_due: str = "100000.00",
            currency: str = "ARS",
            status: str = "pending",
            paid_total: str = "0.00",
        ) -> uuid.UUID:
            rent_period_id = uuid.uuid4()
            session_factory = get_session_factory()
            async with session_factory() as session, session.begin():
                await session.execute(sa.text("SET LOCAL ROLE adminprop_superadmin"))
                await session.execute(
                    sa.text(
                        "INSERT INTO rent_periods "
                        "(id, organization_id, contract_id, period, amount_due, currency, "
                        "status, paid_total) "
                        "VALUES (:id, :org_id, :contract_id, :period, :amount_due, :currency, "
                        ":status, :paid_total)"
                    ),
                    {
                        "id": str(rent_period_id),
                        "org_id": str(organization_id),
                        "contract_id": str(contract_id),
                        "period": date.fromisoformat(period),
                        "amount_due": amount_due,
                        "currency": currency,
                        "status": status,
                        "paid_total": paid_total,
                    },
                )
            return rent_period_id

        async def raw_bank_info(self, landlord_id: uuid.UUID) -> bytes | None:
            """CA-02-04: lee el BYTEA crudo (sin descifrar) directamente
            de la columna -- usado para verificar que nunca es igual al
            texto plano original."""
            session_factory = get_session_factory()
            async with session_factory() as session:
                await session.execute(sa.text("SET LOCAL ROLE adminprop_superadmin"))
                result = await session.execute(
                    sa.text("SELECT bank_info FROM landlords WHERE id = :id"),
                    {"id": str(landlord_id)},
                )
                row = result.first()
                return row[0] if row is not None else None

        async def audit_rows(self, organization_id: uuid.UUID, action: str) -> list[dict]:
            session_factory = get_session_factory()
            async with session_factory() as session:
                await session.execute(sa.text("SET LOCAL ROLE adminprop_superadmin"))
                result = await session.execute(
                    sa.text(
                        "SELECT entity_id, user_id, before_state, after_state FROM audit_logs "
                        "WHERE organization_id = :org_id AND action = :action ORDER BY created_at"
                    ),
                    {"org_id": str(organization_id), "action": action},
                )
                return [dict(row._mapping) for row in result]

    return Seeder()


@pytest.fixture()
def auth_headers():
    return _auth_headers
