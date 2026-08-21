"""Fixtures compartidas de tests/integration/e2e (issue #33).

Mismo patron de engine/session-factory fresco por test que
tests/integration/{payments,settlements,maintenance,charges}/conftest.py
(evita "Future attached to a different loop" entre tests async de
pytest-asyncio). El `Seeder` se duplica deliberadamente -- mismo
criterio que el repo ya aplica entre los demas modulos.

`seed_demo_organization` es el "seed de organizacion demo reutilizable"
pedido por el CA del issue #33: una organizacion activa con sus 3 roles
de sistema, un propietario con `commission_pct`, una propiedad ARS y
una USD (cada una con su contrato), y usuarios `owner`/`maintenance`
autenticados -- lista para que un test E2E ejerza el ciclo mensual
completo contra la API real. No pretende ser el seed "canonico" del
producto (spec_data_model.md §"Estrategia de Seed Data" no define una
organizacion demo especifica) -- es libre de diseno, documentado aca.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncGenerator
from datetime import date

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
    # documents_worker guarda los exports Excel/PDF como Adjuntos --
    # aisla el storage por test (nunca el volumen Docker real), mismo
    # criterio que tests/integration/{settlements,maintenance}/conftest.py.
    monkeypatch.setenv("ATTACHMENTS_DIR", str(tmp_path / "attachments"))
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
            permissions = permissions if permissions is not None else ["settlement:generate"]
            session_factory = get_session_factory()
            async with session_factory() as session, session.begin():
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
            self,
            *,
            status: str = "active",
            name: str | None = None,
            grace_day: int | None = None,
        ) -> dict:
            """Siembra una organizacion `active` con sus 3 roles de sistema
            reales (`ROLE_DEFINITIONS`) -- spec_data_model.md §"Estrategia
            de Seed Data": "toda organizacion nueva tiene exactamente 3
            roles de sistema y sus settings default"."""
            org_id = await self.create_organization(status=status, name=name)
            settings = dict(DEFAULT_ORGANIZATION_SETTINGS)
            if grace_day is not None:
                settings["grace_day"] = grace_day
            session_factory = get_session_factory()
            async with session_factory() as session, session.begin():
                await session.execute(
                    sa.text(
                        "UPDATE organizations SET settings = :settings WHERE id = :id"
                    ).bindparams(sa.bindparam("settings", type_=sa.JSON)),
                    {"id": str(org_id), "settings": json.dumps(settings)},
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

        # ─── helpers de dominio (issue #33) ────────────────────────────────

        async def create_landlord_row(
            self,
            *,
            organization_id: uuid.UUID,
            name: str = "Propietario de prueba",
            commission_pct: str = "10.00",
        ) -> uuid.UUID:
            landlord_id = uuid.uuid4()
            session_factory = get_session_factory()
            async with session_factory() as session, session.begin():
                await session.execute(
                    sa.text(
                        "INSERT INTO landlords (id, organization_id, name, commission_pct) "
                        "VALUES (:id, :org_id, :name, :commission_pct)"
                    ),
                    {
                        "id": str(landlord_id),
                        "org_id": str(organization_id),
                        "name": name,
                        "commission_pct": commission_pct,
                    },
                )
            return landlord_id

        async def create_renter_row(
            self, *, organization_id: uuid.UUID, name: str = "Inquilino de prueba"
        ) -> uuid.UUID:
            renter_id = uuid.uuid4()
            session_factory = get_session_factory()
            async with session_factory() as session, session.begin():
                await session.execute(
                    sa.text(
                        "INSERT INTO renters (id, organization_id, name) "
                        "VALUES (:id, :org_id, :name)"
                    ),
                    {"id": str(renter_id), "org_id": str(organization_id), "name": name},
                )
            return renter_id

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
            start_date: str = "2025-01-01",
            end_date: str = "2030-01-01",
            daily_late_fee_pct: str = "0.1",
            status: str = "active",
        ) -> uuid.UUID:
            contract_id = uuid.uuid4()
            current_amount = current_amount if current_amount is not None else initial_amount
            session_factory = get_session_factory()
            async with session_factory() as session, session.begin():
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

        async def get_rent_period_by_contract(
            self, *, organization_id: uuid.UUID, contract_id: uuid.UUID, period: str
        ) -> dict:
            session_factory = get_session_factory()
            async with session_factory() as session:
                result = await session.execute(
                    sa.text(
                        "SELECT id, status, amount_due, paid_total, currency FROM rent_periods "
                        "WHERE organization_id = :org_id AND contract_id = :contract_id "
                        "AND period = :period"
                    ),
                    {
                        "org_id": str(organization_id),
                        "contract_id": str(contract_id),
                        "period": date.fromisoformat(period),
                    },
                )
                return dict(result.mappings().one())

        async def get_settlement_row(self, settlement_id: uuid.UUID) -> dict:
            session_factory = get_session_factory()
            async with session_factory() as session:
                result = await session.execute(
                    sa.text("SELECT * FROM settlements WHERE id = :id"), {"id": str(settlement_id)}
                )
                return dict(result.mappings().one())

        async def get_line_items(self, settlement_id: uuid.UUID) -> list[dict]:
            session_factory = get_session_factory()
            async with session_factory() as session:
                result = await session.execute(
                    sa.text(
                        "SELECT * FROM settlement_line_items WHERE settlement_id = :id "
                        "ORDER BY created_at"
                    ),
                    {"id": str(settlement_id)},
                )
                return [dict(row._mapping) for row in result]

        async def audit_rows(self, organization_id: uuid.UUID, action: str) -> list[dict]:
            session_factory = get_session_factory()
            async with session_factory() as session:
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


async def seed_demo_organization(seed, *, contract_start: str = "2025-01-01") -> dict:
    """Seed de organizacion demo reutilizable (CA del issue #33: "existe
    un seed de organizacion demo reutilizable").

    Arma una organizacion `active` con sus 3 roles de sistema, un
    propietario con `commission_pct`, dos propiedades (una con contrato
    ARS con % de mora diaria, otra con contrato USD) y usuarios `owner`
    y `maintenance` autenticados -- listo para ejercer el ciclo mensual
    completo (generar periodos, cobrar, reparar, liquidar) contra la API
    real. Reutilizable por cualquier test E2E futuro que necesite el
    mismo punto de partida.
    """
    org = await seed.create_organization_with_system_roles(name="Demo E2E Inmobiliaria")
    org_id = org["organization_id"]
    roles = org["roles"]

    owner = await seed.add_member(
        organization_id=org_id, role_id=roles["owner"], role_name="owner"
    )
    maintenance_user = await seed.add_member(
        organization_id=org_id, role_id=roles["maintenance"], role_name="maintenance"
    )

    landlord_id = await seed.create_landlord_row(
        organization_id=org_id, name="Propietario Demo", commission_pct="10.00"
    )
    renter_ars = await seed.create_renter_row(organization_id=org_id, name="Inquilino ARS Demo")
    renter_usd = await seed.create_renter_row(organization_id=org_id, name="Inquilino USD Demo")

    property_ars = await seed.create_property_row(
        organization_id=org_id, landlord_id=landlord_id, address="Av. Colon 1234"
    )
    property_usd = await seed.create_property_row(
        organization_id=org_id, landlord_id=landlord_id, address="Bv. Chacabuco 5678"
    )

    contract_ars = await seed.create_contract_row(
        organization_id=org_id,
        property_id=property_ars,
        renter_id=renter_ars,
        currency="ARS",
        initial_amount="100000.00",
        daily_late_fee_pct="0.50",
        start_date=contract_start,
    )
    contract_usd = await seed.create_contract_row(
        organization_id=org_id,
        property_id=property_usd,
        renter_id=renter_usd,
        currency="USD",
        initial_amount="500.00",
        daily_late_fee_pct="0.30",
        start_date=contract_start,
    )

    return {
        "organization_id": org_id,
        "roles": roles,
        "owner": owner,
        "maintenance_user": maintenance_user,
        "landlord_id": landlord_id,
        "property_ars": property_ars,
        "property_usd": property_usd,
        "contract_ars": contract_ars,
        "contract_usd": contract_usd,
    }
