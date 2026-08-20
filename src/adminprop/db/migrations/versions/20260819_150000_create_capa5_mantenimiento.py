"""create_capa5_mantenimiento — work_orders + work_order_quotes + attachments

SDD: infrastructure/spec_data_model.md §Capa 5 — Mantenimiento
     + §"Indices PostgreSQL Recomendados" + §"Orden de Migracion"
     + §Apendice B "Politica de soft delete por entidad"
     + core/sdd_02_domain_model.md (WorkOrder / WorkOrderQuote / Attachment)
Implements: CA-25-01 (indice parcial que garantiza una sola cotizacion
            `approved` por pedido), CA-25-02 (CHECK de `entity_type` en
            `attachments` completo, incluye `payment` y `renter`),
            RN-D01 (aislamiento multi-tenant), RN-D02 (soft delete en
            work_orders/attachments)

Issue #25: sexta capa del modelo de datos (depende de la Capa 2, issue
#14 -- `properties`). `work_orders` es el pedido de reparacion con su
ciclo completo (UC-13..UC-16); `work_order_quotes` son las cotizaciones
del encargado (UC-14); `attachments` son los archivos (fotos, exports)
asociados genericamente a entidades -- vive en esta capa por ser el
mantenimiento su primer consumidor (fotos de reparaciones), aunque
tambien la usan cobranzas (recibos/libre deuda, issue #24) y liquidaciones
(issue #27).

`work_orders.approved_quote_id` referencia a `work_order_quotes`, que
todavia no existe cuando se crea `work_orders` -- se agrega la columna +
FK con un `ALTER TABLE` posterior a la creacion de `work_order_quotes`,
tal como documenta el spec ("FK agregada por ALTER tras crear quotes").

`work_orders.settled_in_settlement_id` (FK a `settlements`) NO se agrega
en esta migracion: el spec lo declara explicitamente como una referencia
diferida que se agrega con `ALTER TABLE` en la migracion de la Capa 6
(issue #27, `settlements` todavia no existe).

`attachments.entity_id` es polimorfica (sin FK fisica); la integridad es
app-level. El CHECK de `entity_type` incluye los 5 valores del spec
(`work_order`, `work_order_quote`, `settlement`, `payment`, `renter`) --
`payment` (recibo de cobro) y `renter` (libre deuda) son los que
`modules/payments/attachment_hook.py` y `modules/people/attachment_hook.py`
(issue #24) esperan para dejar de ser no-op; cablear esos hooks es alcance
del issue #26 en adelante, no de esta migracion.

`work_order_quotes` no tiene `deleted_at` (Apendice B no la lista): su
mecanismo de baja logica es el propio `status = 'discarded'`, no soft
delete generico.

Alcance estrictamente de migracion (mismo criterio que los issues
#14/#16/#20): no se agregan modelos ORM (`modules/maintenance/models.py`)
ni router/service/repository, ni se cablean los attachment_hooks del
issue #24 -- eso es el issue #26 (Modulo mantenimiento) en adelante.
"""

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "20260819_150000"
down_revision: str | None = "20260819_140000"
branch_labels: tuple[str, ...] | None = None
depends_on: tuple[str, ...] | None = None

_TENANT_SCOPED_TABLES = ("work_orders", "work_order_quotes", "attachments")


def _enable_rls_with_force(table: str) -> None:
    """Aplica el patron RLS canonico (ENABLE + politica + FORCE) a `table`.

    Mismo patron que `20260819_140000_create_capa4_cobranzas.py`
    (docs/skills/database-migration.md): missing_ok=true + NULLIF
    normalizan tanto "nunca seteado" (NULL) como "limpiado a ''" (rutas
    /superadmin/*) a NULL antes del cast a uuid, evitando un 500 y
    cerrando el acceso (0 filas) en ambos casos.
    """
    op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
    op.execute(
        f"""
        CREATE POLICY {table}_tenant_isolation ON {table}
        USING (
            organization_id = NULLIF(current_setting('app.current_tenant_id', true), '')::uuid
        )
        WITH CHECK (
            organization_id = NULLIF(current_setting('app.current_tenant_id', true), '')::uuid
        )
        """
    )
    op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")


def upgrade() -> None:
    # ─── work_orders ────────────────────────────────────────────────────
    # spec_data_model.md §Capa 5 "work_orders": el pedido de reparacion.
    # `approved_quote_id` se agrega mas abajo con ALTER (work_order_quotes
    # todavia no existe). `settled_in_settlement_id` NO se agrega aca --
    # es una referencia diferida a la Capa 6 (issue #27).
    op.execute("""
        CREATE TABLE work_orders (
            id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            organization_id UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
            property_id     UUID NOT NULL REFERENCES properties(id),
            title           TEXT NOT NULL,
            description     TEXT,
            payer           TEXT NOT NULL
                CHECK (payer IN ('landlord', 'agency')),
            status          TEXT NOT NULL DEFAULT 'open'
                CHECK (status IN ('open', 'in_progress', 'closed', 'cancelled')),
            final_cost      NUMERIC(14,2),
            created_by      UUID NOT NULL REFERENCES users(id),
            closed_at       TIMESTAMPTZ,
            created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
            deleted_at      TIMESTAMPTZ
        )
    """)

    # ─── work_order_quotes ─────────────────────────────────────────────
    # spec_data_model.md §Capa 5 "work_order_quotes": las cotizaciones del
    # encargado. Sin `deleted_at` (Apendice B no la lista -- la baja
    # logica es el propio status 'discarded').
    op.execute("""
        CREATE TABLE work_order_quotes (
            id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            organization_id UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
            work_order_id   UUID NOT NULL REFERENCES work_orders(id),
            amount          NUMERIC(14,2) NOT NULL
                CHECK (amount > 0),
            description     TEXT,
            status          TEXT NOT NULL DEFAULT 'submitted'
                CHECK (status IN ('submitted', 'approved', 'discarded')),
            submitted_by    UUID NOT NULL REFERENCES users(id),
            created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)

    # `approved_quote_id` referencia a `work_order_quotes`, que recien se
    # acaba de crear -- se agrega la columna + FK con ALTER, tal como
    # documenta el spec ("FK agregada por ALTER tras crear quotes").
    op.execute(
        "ALTER TABLE work_orders ADD COLUMN approved_quote_id UUID "
        "REFERENCES work_order_quotes(id)"
    )

    # ─── attachments ────────────────────────────────────────────────────
    # spec_data_model.md §Capa 5 "attachments": archivos asociados
    # genericamente a entidades (polimorfica, sin FK fisica en entity_id
    # -- integridad app-level). CHECK de entity_type completo con los 5
    # valores del spec (CA-25-02): `payment` = recibo de cobro, `renter` =
    # libre deuda (issue #24, hooks todavia no-op -- cablearlos es del
    # issue #26 en adelante).
    op.execute("""
        CREATE TABLE attachments (
            id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            organization_id UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
            entity_type     TEXT NOT NULL
                CHECK (entity_type IN (
                    'work_order', 'work_order_quote', 'settlement', 'payment', 'renter'
                )),
            entity_id       UUID NOT NULL,
            file_path       TEXT NOT NULL,
            file_name       TEXT NOT NULL,
            mime_type       TEXT NOT NULL,
            size_bytes      BIGINT NOT NULL,
            uploaded_by     UUID NOT NULL REFERENCES users(id),
            created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
            deleted_at      TIMESTAMPTZ
        )
    """)

    # ─── Indices (spec_data_model.md §"Indices PostgreSQL Recomendados") ──
    op.execute(
        "CREATE INDEX idx_work_orders_organization_status ON work_orders "
        "(organization_id, status)"
    )
    op.execute("CREATE INDEX idx_work_orders_property_id ON work_orders (property_id)")

    # CA-25-01: indice parcial que garantiza una sola cotizacion `approved`
    # por pedido (RN aplicable al ciclo de cotizaciones, UC-14).
    op.execute(
        "CREATE UNIQUE INDEX idx_work_order_quotes_one_approved_per_order "
        "ON work_order_quotes (work_order_id) WHERE status = 'approved'"
    )

    op.execute(
        "CREATE INDEX idx_attachments_entity_type_entity_id ON attachments "
        "(entity_type, entity_id)"
    )

    # ─── Row Level Security (RN-D01) ──────────────────────────────────────
    for table in _TENANT_SCOPED_TABLES:
        _enable_rls_with_force(table)

    # ─── Grants ─────────────────────────────────────────────────────────
    # Piso general (ALTER DEFAULT PRIVILEGES, issue #3) ya otorgo
    # SELECT/INSERT/UPDATE/DELETE a ambos roles -- se deja explicito aca
    # (mismo criterio que las capas anteriores) para que la migracion sea
    # auto-contenida y legible sin cruzar con la migracion del issue #3.
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON work_orders, work_order_quotes, attachments "
        "TO adminprop_app, adminprop_superadmin"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS attachments CASCADE")
    op.execute("ALTER TABLE work_orders DROP COLUMN IF EXISTS approved_quote_id")
    op.execute("DROP TABLE IF EXISTS work_order_quotes CASCADE")
    op.execute("DROP TABLE IF EXISTS work_orders CASCADE")
