"""Issue #25 — Migracion Capa 5: work_orders + work_order_quotes +
attachments, schema/RLS/CHECK/UNIQUE/indices.

Requiere Postgres real con `alembic upgrade head` ya corrido -- mismo
patron que `tests/integration/db/test_capa4_cobranzas.py` (issue #20).

SDD: infrastructure/spec_data_model.md §Capa 5 — Mantenimiento
Implements: CA-25-01 (indice parcial UNIQUE que garantiza una sola
            cotizacion approved por pedido, probado), CA-25-02 (CHECK de
            entity_type en attachments completo -- incluye payment y
            renter, probado)
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncGenerator

import pytest
import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError

from adminprop.db.session import get_engine, get_session_factory

pytestmark = pytest.mark.asyncio

_WORK_ORDERS_COLUMNS = {
    "id",
    "organization_id",
    "property_id",
    "title",
    "description",
    "payer",
    "status",
    "approved_quote_id",
    "final_cost",
    "created_by",
    "closed_at",
    "created_at",
    "updated_at",
    "deleted_at",
}

_WORK_ORDER_QUOTES_COLUMNS = {
    "id",
    "organization_id",
    "work_order_id",
    "amount",
    "description",
    "status",
    "submitted_by",
    "created_at",
    "updated_at",
}

_ATTACHMENTS_COLUMNS = {
    "id",
    "organization_id",
    "entity_type",
    "entity_id",
    "file_path",
    "file_name",
    "mime_type",
    "size_bytes",
    "uploaded_by",
    "created_at",
    "deleted_at",
}

_ATTACHMENT_ENTITY_TYPES = {
    "work_order",
    "work_order_quote",
    "settlement",
    "payment",
    "renter",
}


class _Rows:
    def __init__(
        self,
        *,
        organization_id: uuid.UUID,
        property_id: uuid.UUID,
        user_id: uuid.UUID,
    ) -> None:
        self.organization_id = organization_id
        self.property_id = property_id
        self.user_id = user_id


@pytest.fixture
async def rows() -> AsyncGenerator[_Rows]:
    """Siembra una organizacion con landlord + property + un usuario --
    suficiente para ejercer los constraints de `work_orders` /
    `work_order_quotes` / `attachments` (mismo criterio que
    `tests/integration/db/test_capa4_cobranzas.py`)."""
    session_factory = get_session_factory()
    org_id = uuid.uuid4()
    landlord_id = uuid.uuid4()
    property_id = uuid.uuid4()
    user_id = uuid.uuid4()

    async with session_factory() as session, session.begin():
        await session.execute(
            sa.text(
                "INSERT INTO organizations (id, slug, name) VALUES (:id, :slug, 'Org Mantenimiento')"
            ),
            {"id": str(org_id), "slug": f"org-{org_id.hex[:8]}"},
        )
        await session.execute(
            sa.text(
                "INSERT INTO users (id, email, password_hash, full_name) "
                "VALUES (:id, :email, 'hash', 'Operador Mantenimiento')"
            ),
            {"id": str(user_id), "email": f"{user_id.hex[:8]}@adminprop.test"},
        )
        await session.execute(
            sa.text(
                "INSERT INTO landlords (id, organization_id, name, commission_pct) "
                "VALUES (:id, :org_id, 'Landlord', 10)"
            ),
            {"id": str(landlord_id), "org_id": str(org_id)},
        )
        await session.execute(
            sa.text(
                "INSERT INTO properties (id, organization_id, landlord_id, address) "
                "VALUES (:id, :org_id, :landlord_id, 'Propiedad Mantenimiento')"
            ),
            {"id": str(property_id), "org_id": str(org_id), "landlord_id": str(landlord_id)},
        )

    yield _Rows(organization_id=org_id, property_id=property_id, user_id=user_id)

    async with session_factory() as session, session.begin():
        await session.execute(
            sa.text("DELETE FROM attachments WHERE organization_id = :org_id"),
            {"org_id": str(org_id)},
        )
        await session.execute(
            sa.text(
                "UPDATE work_orders SET approved_quote_id = NULL WHERE organization_id = :org_id"
            ),
            {"org_id": str(org_id)},
        )
        await session.execute(
            sa.text(
                "DELETE FROM work_order_quotes WHERE work_order_id IN "
                "(SELECT id FROM work_orders WHERE organization_id = :org_id)"
            ),
            {"org_id": str(org_id)},
        )
        await session.execute(
            sa.text("DELETE FROM work_orders WHERE organization_id = :org_id"),
            {"org_id": str(org_id)},
        )
        await session.execute(
            sa.text("DELETE FROM properties WHERE organization_id = :org_id"),
            {"org_id": str(org_id)},
        )
        await session.execute(
            sa.text("DELETE FROM landlords WHERE organization_id = :org_id"),
            {"org_id": str(org_id)},
        )
        await session.execute(
            sa.text("DELETE FROM users WHERE id = :user_id"),
            {"user_id": str(user_id)},
        )
        await session.execute(
            sa.text("DELETE FROM organizations WHERE id = :org_id"),
            {"org_id": str(org_id)},
        )


async def _insert_work_order(
    rows: _Rows,
    *,
    title: str = "Perdida de agua",
    payer: str = "landlord",
    status: str = "open",
) -> uuid.UUID:
    work_order_id = uuid.uuid4()
    session_factory = get_session_factory()
    async with session_factory() as session, session.begin():
        await session.execute(
            sa.text(
                "INSERT INTO work_orders "
                "(id, organization_id, property_id, title, payer, status, created_by) "
                "VALUES (:id, :org_id, :property_id, :title, :payer, :status, :created_by)"
            ),
            {
                "id": str(work_order_id),
                "org_id": str(rows.organization_id),
                "property_id": str(rows.property_id),
                "title": title,
                "payer": payer,
                "status": status,
                "created_by": str(rows.user_id),
            },
        )
    return work_order_id


async def _insert_quote(
    rows: _Rows,
    *,
    work_order_id: uuid.UUID,
    amount: str = "50000.00",
    status: str = "submitted",
) -> uuid.UUID:
    quote_id = uuid.uuid4()
    session_factory = get_session_factory()
    async with session_factory() as session, session.begin():
        await session.execute(
            sa.text(
                "INSERT INTO work_order_quotes "
                "(id, organization_id, work_order_id, amount, status, submitted_by) "
                "VALUES (:id, :org_id, :work_order_id, :amount, :status, :submitted_by)"
            ),
            {
                "id": str(quote_id),
                "org_id": str(rows.organization_id),
                "work_order_id": str(work_order_id),
                "amount": amount,
                "status": status,
                "submitted_by": str(rows.user_id),
            },
        )
    return quote_id


async def _insert_attachment(
    rows: _Rows,
    *,
    entity_type: str,
    entity_id: uuid.UUID | None = None,
) -> uuid.UUID:
    attachment_id = uuid.uuid4()
    session_factory = get_session_factory()
    async with session_factory() as session, session.begin():
        await session.execute(
            sa.text(
                "INSERT INTO attachments "
                "(id, organization_id, entity_type, entity_id, file_path, file_name, "
                "mime_type, size_bytes, uploaded_by) "
                "VALUES (:id, :org_id, :entity_type, :entity_id, :file_path, :file_name, "
                ":mime_type, :size_bytes, :uploaded_by)"
            ),
            {
                "id": str(attachment_id),
                "org_id": str(rows.organization_id),
                "entity_type": entity_type,
                "entity_id": str(entity_id or uuid.uuid4()),
                "file_path": "/data/adminprop-storage/org/mantenimiento/foo.jpg",
                "file_name": "foo.jpg",
                "mime_type": "image/jpeg",
                "size_bytes": 1024,
                "uploaded_by": str(rows.user_id),
            },
        )
    return attachment_id


# ─── Schema identico al spec ────────────────────────────────────────────────


async def test_ca_25_01_work_orders_columnas_identicas_al_spec():
    engine = get_engine()
    async with engine.connect() as conn:
        result = await conn.execute(
            sa.text(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name = 'work_orders'"
            )
        )
        columns = {row[0] for row in result}
    assert columns == _WORK_ORDERS_COLUMNS


async def test_ca_25_01_work_order_quotes_columnas_identicas_al_spec():
    engine = get_engine()
    async with engine.connect() as conn:
        result = await conn.execute(
            sa.text(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name = 'work_order_quotes'"
            )
        )
        columns = {row[0] for row in result}
    assert columns == _WORK_ORDER_QUOTES_COLUMNS


async def test_ca_25_02_attachments_columnas_identicas_al_spec():
    engine = get_engine()
    async with engine.connect() as conn:
        result = await conn.execute(
            sa.text(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name = 'attachments'"
            )
        )
        columns = {row[0] for row in result}
    assert columns == _ATTACHMENTS_COLUMNS


async def test_ca_25_01_fk_work_orders_property_id_referencia_properties():
    engine = get_engine()
    async with engine.connect() as conn:
        result = await conn.execute(
            sa.text(
                "SELECT confrelid::regclass::text FROM pg_constraint "
                "WHERE conrelid = 'work_orders'::regclass AND contype = 'f' "
                "AND conname LIKE '%property_id%'"
            )
        )
        referenced_table = result.scalar_one()
    assert referenced_table == "properties"


async def test_ca_25_01_fk_work_orders_approved_quote_id_referencia_work_order_quotes():
    """`approved_quote_id` fue agregada por ALTER tras crear
    `work_order_quotes` (spec: "FK agregada por ALTER tras crear quotes")."""
    engine = get_engine()
    async with engine.connect() as conn:
        result = await conn.execute(
            sa.text(
                "SELECT confrelid::regclass::text FROM pg_constraint "
                "WHERE conrelid = 'work_orders'::regclass AND contype = 'f' "
                "AND conname LIKE '%approved_quote_id%'"
            )
        )
        referenced_table = result.scalar_one()
    assert referenced_table == "work_order_quotes"


async def test_ca_25_01_work_orders_settled_in_settlement_id_no_existe_todavia():
    """spec: la FK a `settlements` se agrega en la migracion de la Capa 6
    (issue #27) -- esta migracion no debe crear la columna."""
    engine = get_engine()
    async with engine.connect() as conn:
        result = await conn.execute(
            sa.text(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name = 'work_orders' AND column_name = 'settled_in_settlement_id'"
            )
        )
        columns = {row[0] for row in result}
    assert columns == set()


async def test_ca_25_01_fk_work_order_quotes_work_order_id_referencia_work_orders():
    engine = get_engine()
    async with engine.connect() as conn:
        result = await conn.execute(
            sa.text(
                "SELECT confrelid::regclass::text FROM pg_constraint "
                "WHERE conrelid = 'work_order_quotes'::regclass AND contype = 'f' "
                "AND conname LIKE '%work_order_id%'"
            )
        )
        referenced_table = result.scalar_one()
    assert referenced_table == "work_orders"


async def test_ca_25_01_fk_attachments_uploaded_by_referencia_users():
    engine = get_engine()
    async with engine.connect() as conn:
        result = await conn.execute(
            sa.text(
                "SELECT confrelid::regclass::text FROM pg_constraint "
                "WHERE conrelid = 'attachments'::regclass AND contype = 'f' "
                "AND conname LIKE '%uploaded_by%'"
            )
        )
        referenced_table = result.scalar_one()
    assert referenced_table == "users"


async def test_ca_25_01_attachments_entity_id_no_tiene_fk_fisica():
    """entity_id es polimorfica -- integridad app-level, sin FK."""
    engine = get_engine()
    async with engine.connect() as conn:
        result = await conn.execute(
            sa.text(
                "SELECT count(*) FROM pg_constraint "
                "WHERE conrelid = 'attachments'::regclass AND contype = 'f' "
                "AND conname LIKE '%entity_id%'"
            )
        )
        count = result.scalar_one()
    assert count == 0


@pytest.mark.parametrize("table", ["work_orders", "work_order_quotes", "attachments"])
async def test_ca_25_tabla_tiene_rls_habilitado_y_forzado(table: str):
    """RN-D01: RLS + FORCE en las tres tablas de la Capa 5."""
    engine = get_engine()
    async with engine.connect() as conn:
        result = await conn.execute(
            sa.text(
                "SELECT relrowsecurity, relforcerowsecurity FROM pg_class WHERE relname = :table"
            ),
            {"table": table},
        )
        row = result.one()
    assert row.relrowsecurity is True
    assert row.relforcerowsecurity is True


@pytest.mark.parametrize("table", ["work_orders", "work_order_quotes", "attachments"])
async def test_ca_25_politica_tenant_isolation_usa_nullif_en_el_cast(table: str):
    engine = get_engine()
    async with engine.connect() as conn:
        result = await conn.execute(
            sa.text(
                "SELECT pg_get_expr(polqual, polrelid) FROM pg_policy "
                "WHERE polrelid = to_regclass(:table)"
            ),
            {"table": table},
        )
        qual = result.scalar_one()
    assert "NULLIF" in qual
    assert "app.current_tenant_id" in qual


# ─── Indices del spec ───────────────────────────────────────────────────────


async def test_ca_25_indice_organization_status_existe_en_work_orders():
    engine = get_engine()
    async with engine.connect() as conn:
        result = await conn.execute(
            sa.text("SELECT indexdef FROM pg_indexes WHERE tablename = 'work_orders'")
        )
        defs = [row[0] for row in result]
    assert any("organization_id" in d and "status" in d for d in defs)


async def test_ca_25_indice_property_id_existe_en_work_orders():
    engine = get_engine()
    async with engine.connect() as conn:
        result = await conn.execute(
            sa.text("SELECT indexdef FROM pg_indexes WHERE tablename = 'work_orders'")
        )
        defs = [row[0] for row in result]
    assert any(
        "property_id" in d and "organization_id" not in d and "UNIQUE" not in d for d in defs
    )


async def test_ca_25_01_indice_parcial_unico_de_approved_por_pedido_existe():
    """CA-25-01: el indice parcial UNIQUE en (work_order_id) WHERE status =
    'approved' existe."""
    engine = get_engine()
    async with engine.connect() as conn:
        result = await conn.execute(
            sa.text("SELECT indexdef FROM pg_indexes WHERE tablename = 'work_order_quotes'")
        )
        defs = [row[0] for row in result]
    assert any("UNIQUE" in d and "work_order_id" in d and "status = 'approved'" in d for d in defs)


async def test_ca_25_indice_entity_type_entity_id_existe_en_attachments():
    engine = get_engine()
    async with engine.connect() as conn:
        result = await conn.execute(
            sa.text("SELECT indexdef FROM pg_indexes WHERE tablename = 'attachments'")
        )
        defs = [row[0] for row in result]
    assert any("entity_type" in d and "entity_id" in d for d in defs)


# ─── CA-25-01: un solo approved por pedido ──────────────────────────────────


class TestCA2501OneApprovedQuotePerWorkOrder:
    """CA-25-01: existe un indice parcial que garantiza una sola cotizacion
    `approved` por pedido, probado end-to-end contra Postgres real."""

    async def test_ca_25_01_allows_multiple_submitted_quotes_for_same_work_order(self, rows):
        work_order_id = await _insert_work_order(rows)
        first = await _insert_quote(rows, work_order_id=work_order_id, status="submitted")
        second = await _insert_quote(rows, work_order_id=work_order_id, status="submitted")
        assert first is not None
        assert second is not None

    async def test_ca_25_01_allows_first_approved_quote_for_work_order(self, rows):
        work_order_id = await _insert_work_order(rows)
        quote_id = await _insert_quote(rows, work_order_id=work_order_id, status="approved")
        assert quote_id is not None

    async def test_ca_25_01_rejects_second_approved_quote_for_same_work_order(self, rows):
        work_order_id = await _insert_work_order(rows)
        await _insert_quote(rows, work_order_id=work_order_id, status="approved")
        with pytest.raises(IntegrityError):
            await _insert_quote(rows, work_order_id=work_order_id, status="approved")

    async def test_ca_25_01_rejects_update_that_creates_second_approved_quote(self, rows):
        work_order_id = await _insert_work_order(rows)
        await _insert_quote(rows, work_order_id=work_order_id, status="approved")
        second_quote_id = await _insert_quote(rows, work_order_id=work_order_id, status="submitted")
        session_factory = get_session_factory()
        with pytest.raises(IntegrityError):
            async with session_factory() as session, session.begin():
                await session.execute(
                    sa.text("UPDATE work_order_quotes SET status = 'approved' WHERE id = :id"),
                    {"id": str(second_quote_id)},
                )

    async def test_ca_25_01_allows_approved_quote_for_different_work_orders(self, rows):
        work_order_a = await _insert_work_order(rows, title="Pedido A")
        work_order_b = await _insert_work_order(rows, title="Pedido B")
        quote_a = await _insert_quote(rows, work_order_id=work_order_a, status="approved")
        quote_b = await _insert_quote(rows, work_order_id=work_order_b, status="approved")
        assert quote_a is not None
        assert quote_b is not None

    async def test_ca_25_01_allows_approved_after_discarding_the_previous_one(self, rows):
        """Descartar la cotizacion approved libera el indice parcial para
        aprobar otra del mismo pedido."""
        work_order_id = await _insert_work_order(rows)
        first_quote_id = await _insert_quote(rows, work_order_id=work_order_id, status="approved")
        second_quote_id = await _insert_quote(rows, work_order_id=work_order_id, status="submitted")
        session_factory = get_session_factory()
        async with session_factory() as session, session.begin():
            await session.execute(
                sa.text("UPDATE work_order_quotes SET status = 'discarded' WHERE id = :id"),
                {"id": str(first_quote_id)},
            )
            await session.execute(
                sa.text("UPDATE work_order_quotes SET status = 'approved' WHERE id = :id"),
                {"id": str(second_quote_id)},
            )


# ─── CA-25-02: CHECK de entity_type completo (incluye payment y renter) ────


class TestCA2502AttachmentsEntityTypeCheck:
    """CA-25-02: el CHECK de `entity_type` en `attachments` esta completo --
    incluye `payment` y `renter`, ademas de los 3 valores propios de
    mantenimiento."""

    @pytest.mark.parametrize("entity_type", sorted(_ATTACHMENT_ENTITY_TYPES))
    async def test_ca_25_02_allows_each_documented_entity_type(self, rows, entity_type: str):
        attachment_id = await _insert_attachment(rows, entity_type=entity_type)
        assert attachment_id is not None

    async def test_ca_25_02_allows_payment_entity_type(self, rows):
        """El attachment_hook no-op de payments (issue #24) espera poder
        insertar entity_type='payment' cuando se cablee (issue #26+)."""
        attachment_id = await _insert_attachment(rows, entity_type="payment")
        assert attachment_id is not None

    async def test_ca_25_02_allows_renter_entity_type(self, rows):
        """El attachment_hook no-op de people (issue #24) espera poder
        insertar entity_type='renter' cuando se cablee (issue #26+)."""
        attachment_id = await _insert_attachment(rows, entity_type="renter")
        assert attachment_id is not None

    async def test_ca_25_02_rejects_entity_type_outside_the_documented_set(self, rows):
        with pytest.raises(IntegrityError):
            await _insert_attachment(rows, entity_type="contract")


# ─── Checks propios de work_orders / work_order_quotes ──────────────────────


class TestWorkOrdersChecks:
    async def test_rejects_invalid_payer(self, rows):
        with pytest.raises(IntegrityError):
            await _insert_work_order(rows, payer="tenant")

    async def test_rejects_invalid_status(self, rows):
        with pytest.raises(IntegrityError):
            await _insert_work_order(rows, status="pending")

    async def test_allows_each_documented_status(self, rows):
        for status in ("open", "in_progress", "closed", "cancelled"):
            work_order_id = await _insert_work_order(rows, status=status)
            assert work_order_id is not None

    async def test_status_default_is_open(self, rows):
        work_order_id = uuid.uuid4()
        session_factory = get_session_factory()
        async with session_factory() as session, session.begin():
            await session.execute(
                sa.text(
                    "INSERT INTO work_orders "
                    "(id, organization_id, property_id, title, payer, created_by) "
                    "VALUES (:id, :org_id, :property_id, 'Sin status explicito', 'agency', "
                    ":created_by)"
                ),
                {
                    "id": str(work_order_id),
                    "org_id": str(rows.organization_id),
                    "property_id": str(rows.property_id),
                    "created_by": str(rows.user_id),
                },
            )
        async with session_factory() as session:
            result = await session.execute(
                sa.text("SELECT status FROM work_orders WHERE id = :id"),
                {"id": str(work_order_id)},
            )
            status = result.scalar_one()
        assert status == "open"


class TestWorkOrderQuotesChecks:
    async def test_rejects_amount_not_greater_than_zero(self, rows):
        work_order_id = await _insert_work_order(rows)
        with pytest.raises(IntegrityError):
            await _insert_quote(rows, work_order_id=work_order_id, amount="0")

    async def test_rejects_negative_amount(self, rows):
        work_order_id = await _insert_work_order(rows)
        with pytest.raises(IntegrityError):
            await _insert_quote(rows, work_order_id=work_order_id, amount="-1")

    async def test_rejects_invalid_status(self, rows):
        work_order_id = await _insert_work_order(rows)
        with pytest.raises(IntegrityError):
            await _insert_quote(rows, work_order_id=work_order_id, status="pending")

    async def test_status_default_is_submitted(self, rows):
        work_order_id = await _insert_work_order(rows)
        quote_id = uuid.uuid4()
        session_factory = get_session_factory()
        async with session_factory() as session, session.begin():
            await session.execute(
                sa.text(
                    "INSERT INTO work_order_quotes "
                    "(id, organization_id, work_order_id, amount, submitted_by) "
                    "VALUES (:id, :org_id, :work_order_id, 10000, :submitted_by)"
                ),
                {
                    "id": str(quote_id),
                    "org_id": str(rows.organization_id),
                    "work_order_id": str(work_order_id),
                    "submitted_by": str(rows.user_id),
                },
            )
        async with session_factory() as session:
            result = await session.execute(
                sa.text("SELECT status FROM work_order_quotes WHERE id = :id"),
                {"id": str(quote_id)},
            )
            status = result.scalar_one()
        assert status == "submitted"


async def test_allows_setting_approved_quote_id_on_work_order(rows):
    """El flujo de aprobacion setea `work_orders.approved_quote_id` a la
    cotizacion aprobada (UC-14) -- ejercido aca solo a nivel de schema/FK,
    la logica de negocio es del issue #26."""
    work_order_id = await _insert_work_order(rows)
    quote_id = await _insert_quote(rows, work_order_id=work_order_id, status="approved")
    session_factory = get_session_factory()
    async with session_factory() as session, session.begin():
        await session.execute(
            sa.text("UPDATE work_orders SET approved_quote_id = :quote_id WHERE id = :id"),
            {"quote_id": str(quote_id), "id": str(work_order_id)},
        )
    async with session_factory() as session:
        result = await session.execute(
            sa.text("SELECT approved_quote_id FROM work_orders WHERE id = :id"),
            {"id": str(work_order_id)},
        )
        approved_quote_id = result.scalar_one()
    assert str(approved_quote_id) == str(quote_id)


async def test_allows_soft_deleting_a_work_order_via_deleted_at(rows):
    """Apendice B: work_orders usa deleted_at (RN-D02)."""
    work_order_id = await _insert_work_order(rows)
    session_factory = get_session_factory()
    async with session_factory() as session, session.begin():
        await session.execute(
            sa.text("UPDATE work_orders SET deleted_at = now() WHERE id = :id"),
            {"id": str(work_order_id)},
        )
    async with session_factory() as session:
        result = await session.execute(
            sa.text("SELECT deleted_at FROM work_orders WHERE id = :id"),
            {"id": str(work_order_id)},
        )
        deleted_at = result.scalar_one()
    assert deleted_at is not None


async def test_allows_soft_deleting_an_attachment_via_deleted_at(rows):
    """Apendice B: attachments usa deleted_at (RN-D02)."""
    attachment_id = await _insert_attachment(rows, entity_type="work_order")
    session_factory = get_session_factory()
    async with session_factory() as session, session.begin():
        await session.execute(
            sa.text("UPDATE attachments SET deleted_at = now() WHERE id = :id"),
            {"id": str(attachment_id)},
        )
    async with session_factory() as session:
        result = await session.execute(
            sa.text("SELECT deleted_at FROM attachments WHERE id = :id"),
            {"id": str(attachment_id)},
        )
        deleted_at = result.scalar_one()
    assert deleted_at is not None
