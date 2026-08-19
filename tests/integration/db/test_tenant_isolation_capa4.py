"""Issue #20 — Aislamiento cross-tenant (RN-D01) sobre
`rent_periods`/`payments`.

Mismo patron que `tests/integration/db/test_tenant_isolation_capa3.py`
(issue #16, tablas `contracts`/`contract_adjustments`): ejerce el
aislamiento end-to-end con `set_tenant_context`, no solo inspecciona el
catalogo (eso lo cubre `test_capa4_cobranzas.py`).

SDD: core/sdd_02_domain_model.md §3 RN-D01
     + docs/skills/tenant-isolation.md
Implements: CA-20 (RLS aisla cross-tenant en ambas tablas de la Capa 4)
"""

import uuid
from collections.abc import AsyncGenerator
from datetime import date

import pytest
import sqlalchemy as sa

from adminprop.db.session import get_session_factory, set_tenant_context

pytestmark = pytest.mark.asyncio

ORG_A = uuid.uuid4()
ORG_B = uuid.uuid4()


@pytest.fixture
async def two_orgs_with_one_rent_period_and_payment_each() -> AsyncGenerator[None]:
    """Crea dos organizaciones, cada una con landlord + property + renter +
    contract + un rent_period + un payment (conexion por default:
    `adminprop`, superuser con BYPASSRLS -- set up ajeno al aislamiento que
    se quiere probar). Limpieza al final."""
    session_factory = get_session_factory()
    landlord_a, landlord_b = uuid.uuid4(), uuid.uuid4()
    property_a, property_b = uuid.uuid4(), uuid.uuid4()
    renter_a, renter_b = uuid.uuid4(), uuid.uuid4()
    contract_a, contract_b = uuid.uuid4(), uuid.uuid4()
    rent_period_a, rent_period_b = uuid.uuid4(), uuid.uuid4()
    user_a, user_b = uuid.uuid4(), uuid.uuid4()
    payment_a, payment_b = uuid.uuid4(), uuid.uuid4()

    async with session_factory() as session, session.begin():
        await session.execute(
            sa.text(
                "INSERT INTO organizations (id, slug, name) "
                "VALUES (:id_a, :slug_a, 'Org A'), (:id_b, :slug_b, 'Org B')"
            ),
            {
                "id_a": str(ORG_A),
                "slug_a": f"org-a-{ORG_A.hex[:8]}",
                "id_b": str(ORG_B),
                "slug_b": f"org-b-{ORG_B.hex[:8]}",
            },
        )
        await session.execute(
            sa.text(
                "INSERT INTO users (id, email, password_hash, full_name) "
                "VALUES (:id_a, :email_a, 'hash', 'Operador A'), "
                "(:id_b, :email_b, 'hash', 'Operador B')"
            ),
            {
                "id_a": str(user_a),
                "email_a": f"{user_a.hex[:8]}@adminprop.test",
                "id_b": str(user_b),
                "email_b": f"{user_b.hex[:8]}@adminprop.test",
            },
        )
        await session.execute(
            sa.text(
                "INSERT INTO landlords (id, organization_id, name, commission_pct) "
                "VALUES (:id_a, :org_a, 'Landlord A', 10), (:id_b, :org_b, 'Landlord B', 10)"
            ),
            {
                "id_a": str(landlord_a),
                "org_a": str(ORG_A),
                "id_b": str(landlord_b),
                "org_b": str(ORG_B),
            },
        )
        await session.execute(
            sa.text(
                "INSERT INTO properties (id, organization_id, landlord_id, address) "
                "VALUES (:id_a, :org_a, :landlord_a, 'Calle A 123'), "
                "(:id_b, :org_b, :landlord_b, 'Calle B 456')"
            ),
            {
                "id_a": str(property_a),
                "org_a": str(ORG_A),
                "landlord_a": str(landlord_a),
                "id_b": str(property_b),
                "org_b": str(ORG_B),
                "landlord_b": str(landlord_b),
            },
        )
        await session.execute(
            sa.text(
                "INSERT INTO renters (id, organization_id, name) "
                "VALUES (:id_a, :org_a, 'Renter A'), (:id_b, :org_b, 'Renter B')"
            ),
            {
                "id_a": str(renter_a),
                "org_a": str(ORG_A),
                "id_b": str(renter_b),
                "org_b": str(ORG_B),
            },
        )
        await session.execute(
            sa.text(
                "INSERT INTO contracts (id, organization_id, property_id, renter_id, currency, "
                "initial_amount, current_amount, start_date, end_date, daily_late_fee_pct, "
                "status) "
                "VALUES "
                "(:id_a, :org_a, :property_a, :renter_a, 'ARS', 100000, 100000, "
                ":start_date, :end_date, 0.5, 'active'), "
                "(:id_b, :org_b, :property_b, :renter_b, 'ARS', 100000, 100000, "
                ":start_date, :end_date, 0.5, 'active')"
            ),
            {
                "id_a": str(contract_a),
                "org_a": str(ORG_A),
                "property_a": str(property_a),
                "renter_a": str(renter_a),
                "id_b": str(contract_b),
                "org_b": str(ORG_B),
                "property_b": str(property_b),
                "renter_b": str(renter_b),
                "start_date": date(2026, 1, 1),
                "end_date": date(2026, 12, 31),
            },
        )
        await session.execute(
            sa.text(
                "INSERT INTO rent_periods "
                "(id, organization_id, contract_id, period, amount_due, currency, status) "
                "VALUES "
                "(:id_a, :org_a, :contract_a, :period, 100000, 'ARS', 'pending'), "
                "(:id_b, :org_b, :contract_b, :period, 100000, 'ARS', 'pending')"
            ),
            {
                "id_a": str(rent_period_a),
                "org_a": str(ORG_A),
                "contract_a": str(contract_a),
                "id_b": str(rent_period_b),
                "org_b": str(ORG_B),
                "contract_b": str(contract_b),
                "period": date(2026, 8, 1),
            },
        )
        await session.execute(
            sa.text(
                "INSERT INTO payments "
                "(id, organization_id, rent_period_id, payment_date, method, "
                "payment_currency, amount, destination, created_by) "
                "VALUES "
                "(:id_a, :org_a, :rent_period_a, :payment_date, 'cash', 'ARS', 50000, "
                "'agency_account', :user_a), "
                "(:id_b, :org_b, :rent_period_b, :payment_date, 'cash', 'ARS', 50000, "
                "'agency_account', :user_b)"
            ),
            {
                "id_a": str(payment_a),
                "org_a": str(ORG_A),
                "rent_period_a": str(rent_period_a),
                "user_a": str(user_a),
                "id_b": str(payment_b),
                "org_b": str(ORG_B),
                "rent_period_b": str(rent_period_b),
                "user_b": str(user_b),
                "payment_date": date(2026, 8, 10),
            },
        )
    yield
    async with session_factory() as session, session.begin():
        await session.execute(
            sa.text("DELETE FROM payments WHERE organization_id IN (:org_a, :org_b)"),
            {"org_a": str(ORG_A), "org_b": str(ORG_B)},
        )
        await session.execute(
            sa.text("DELETE FROM rent_periods WHERE organization_id IN (:org_a, :org_b)"),
            {"org_a": str(ORG_A), "org_b": str(ORG_B)},
        )
        await session.execute(
            sa.text("DELETE FROM contracts WHERE organization_id IN (:org_a, :org_b)"),
            {"org_a": str(ORG_A), "org_b": str(ORG_B)},
        )
        await session.execute(
            sa.text("DELETE FROM properties WHERE organization_id IN (:org_a, :org_b)"),
            {"org_a": str(ORG_A), "org_b": str(ORG_B)},
        )
        await session.execute(
            sa.text("DELETE FROM renters WHERE organization_id IN (:org_a, :org_b)"),
            {"org_a": str(ORG_A), "org_b": str(ORG_B)},
        )
        await session.execute(
            sa.text("DELETE FROM landlords WHERE organization_id IN (:org_a, :org_b)"),
            {"org_a": str(ORG_A), "org_b": str(ORG_B)},
        )
        await session.execute(
            sa.text("DELETE FROM users WHERE id IN (:user_a, :user_b)"),
            {"user_a": str(user_a), "user_b": str(user_b)},
        )
        await session.execute(
            sa.text("DELETE FROM organizations WHERE id IN (:org_a, :org_b)"),
            {"org_a": str(ORG_A), "org_b": str(ORG_B)},
        )


@pytest.mark.parametrize("table", ["rent_periods", "payments"])
async def test_ca_20_tenant_a_solo_ve_sus_propias_filas(
    table: str, two_orgs_with_one_rent_period_and_payment_each
):
    """RN-D01: con `app.current_tenant_id = ORG_A`, `adminprop_app` solo ve
    la fila de la organizacion A, tanto en `rent_periods` como en
    `payments`."""
    session_factory = get_session_factory()
    async with session_factory() as session, session.begin():
        await session.execute(sa.text("SET LOCAL ROLE adminprop_app"))
        await set_tenant_context(session, ORG_A)
        result = await session.execute(sa.text(f"SELECT organization_id FROM {table}"))
        seen = {row[0] for row in result}

    assert seen == {ORG_A}


@pytest.mark.parametrize("table", ["rent_periods", "payments"])
async def test_ca_20_tenant_b_solo_ve_sus_propias_filas(
    table: str, two_orgs_with_one_rent_period_and_payment_each
):
    """Simetrico al test de A: `app.current_tenant_id = ORG_B`."""
    session_factory = get_session_factory()
    async with session_factory() as session, session.begin():
        await session.execute(sa.text("SET LOCAL ROLE adminprop_app"))
        await set_tenant_context(session, ORG_B)
        result = await session.execute(sa.text(f"SELECT organization_id FROM {table}"))
        seen = {row[0] for row in result}

    assert seen == {ORG_B}


@pytest.mark.parametrize("table", ["rent_periods", "payments"])
async def test_ca_20_sin_contexto_seteado_no_ve_ninguna_fila(
    table: str, two_orgs_with_one_rent_period_and_payment_each
):
    """Fail-closed -- missing_ok=true + NULLIF cierran el acceso en vez de
    tirar un error 500 cuando nadie seteo el contexto de tenant."""
    session_factory = get_session_factory()
    async with session_factory() as session, session.begin():
        await session.execute(sa.text("SET LOCAL ROLE adminprop_app"))
        result = await session.execute(sa.text(f"SELECT organization_id FROM {table}"))
        rows = list(result)

    assert rows == []


@pytest.mark.parametrize("table", ["rent_periods", "payments"])
async def test_ca_20_contexto_limpiado_a_none_no_ve_ninguna_fila_ni_revienta(
    table: str, two_orgs_with_one_rent_period_and_payment_each
):
    """`set_tenant_context(session, None)` (patron de rutas /superadmin/*)
    no revienta el cast a uuid -- devuelve 0 filas (fix NULLIF, issue #3)."""
    session_factory = get_session_factory()
    async with session_factory() as session, session.begin():
        await session.execute(sa.text("SET LOCAL ROLE adminprop_app"))
        await set_tenant_context(session, None)
        result = await session.execute(sa.text(f"SELECT organization_id FROM {table}"))
        rows = list(result)

    assert rows == []


@pytest.mark.parametrize("table", ["rent_periods", "payments"])
async def test_ca_20_adminprop_superadmin_bypassa_rls_y_ve_ambos_tenants(
    table: str, two_orgs_with_one_rent_period_and_payment_each
):
    """Decision #42: `adminprop_superadmin` (BYPASSRLS) ve las filas de
    ambas organizaciones, sin necesidad de `set_tenant_context`."""
    session_factory = get_session_factory()
    async with session_factory() as session, session.begin():
        await session.execute(sa.text("SET LOCAL ROLE adminprop_superadmin"))
        result = await session.execute(
            sa.text(f"SELECT organization_id FROM {table} WHERE organization_id IN (:a, :b)"),
            {"a": str(ORG_A), "b": str(ORG_B)},
        )
        seen = {row[0] for row in result}

    assert seen == {ORG_A, ORG_B}
