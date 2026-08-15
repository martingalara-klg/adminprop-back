"""create_capa3_contratos — contracts + contract_adjustments (EXCLUDE btree_gist)

SDD: infrastructure/spec_data_model.md §Capa 3 — Contratos
     + §"Indices PostgreSQL Recomendados" + §"Orden de Migracion"
Implements: CA-16-01 (constraint de exclusion EXCLUDE + btree_gist probado),
            CA-16-02 (CHECK que impide ajuste en contratos USD),
            CA-16-03 (indice parcial unico: un solo ajuste `pending` por
            contrato), RN-D01 (aislamiento multi-tenant), RN-D02 (soft
            delete en `contracts`; `contract_adjustments` no tiene
            `deleted_at` -- el spec no lo declara, las correcciones se
            modelan como un ajuste nuevo con nota, nunca borrado)

Issue #16: cuarta capa del modelo de datos (depende de la Capa 2, issue
#14 -- `properties`). `contracts` es el contrato de locacion (RN-C01: sin
solapamiento de vigencias sobre la misma propiedad, enforzado con un
EXCLUDE constraint via btree_gist -- la extension ya la instalo el issue
#3 (`20260812_114322_setup_extensions_and_roles.py`), pero se re-declara
aca con `CREATE EXTENSION IF NOT EXISTS` para que la migracion sea
auto-contenida, mismo criterio que Capa 1/2). `contract_adjustments` es
el historial de ajustes por indice (RN-C02: USD nunca ajusta: el
CHECK de la tabla `contracts` lo impide a nivel de columna; RN-C03: el
% de ajuste es siempre manual, nunca automatico -- por eso `pct_applied`
es NULL mientras el ajuste esta `pending`).

Alcance estrictamente de migracion (mismo criterio que los issues #12/#14):
no se agregan modelos ORM (`modules/contratos/models.py`) ni
router/service/repository -- eso es el issue #17 (Modulo contratos).
"""

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "20260815_110000"
down_revision: str | None = "20260815_100000"
branch_labels: tuple[str, ...] | None = None
depends_on: tuple[str, ...] | None = None

_TENANT_SCOPED_TABLES = ("contracts", "contract_adjustments")


def _enable_rls_with_force(table: str) -> None:
    """Aplica el patron RLS canonico (ENABLE + politica + FORCE) a `table`.

    Mismo patron que `20260815_100000_create_capa2_propiedades.py` /
    `20260815_090000_create_capa1_personas.py` (docs/skills/database-migration.md):
    missing_ok=true + NULLIF normalizan tanto "nunca seteado" (NULL) como
    "limpiado a ''" (rutas /superadmin/*) a NULL antes del cast a uuid,
    evitando un 500 y cerrando el acceso (0 filas) en ambos casos.
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
    # btree_gist ya fue instalada por el issue #3, pero se re-declara para
    # que esta migracion sea auto-contenida (mismo criterio de pgcrypto en
    # Capa 1) y no dependa silenciosamente de otra migracion para correr en
    # un entorno limpio.
    op.execute("CREATE EXTENSION IF NOT EXISTS btree_gist")

    # ─── contracts ──────────────────────────────────────────────────────
    # spec_data_model.md §Capa 3 "contracts": el contrato de locacion.
    # `current_amount` solo cambia via ajuste (RN-C04); `initial_amount`
    # queda como referencia historica. `daily_late_fee_pct` es NOT NULL
    # (el % de mora diaria se define al crear el contrato, sin default de
    # negocio en el spec). `adjustment_frequency_months`/`adjustment_index`
    # nullable -- CHECK impide setearlos cuando currency='USD' (RN-C02).
    op.execute("""
        CREATE TABLE contracts (
            id                            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            organization_id               UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
            property_id                   UUID NOT NULL REFERENCES properties(id),
            renter_id                     UUID NOT NULL REFERENCES renters(id),
            currency                      TEXT NOT NULL
                CHECK (currency IN ('ARS', 'USD')),
            initial_amount                NUMERIC(14,2) NOT NULL
                CHECK (initial_amount > 0),
            current_amount                NUMERIC(14,2) NOT NULL,
            start_date                    DATE NOT NULL,
            end_date                      DATE NOT NULL,
            daily_late_fee_pct            NUMERIC(14,4) NOT NULL
                CHECK (daily_late_fee_pct >= 0),
            adjustment_frequency_months   SMALLINT
                CHECK (adjustment_frequency_months > 0),
            adjustment_index              TEXT
                CHECK (adjustment_index IN ('icl', 'ipc_cordoba', 'otro')),
            adjustment_index_notes        TEXT,
            status                        TEXT NOT NULL DEFAULT 'draft'
                CHECK (status IN ('draft', 'active', 'expired', 'terminated')),
            notes                         TEXT,
            metadata                      JSONB NOT NULL DEFAULT '{}'::JSONB,
            created_at                    TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at                    TIMESTAMPTZ NOT NULL DEFAULT now(),
            deleted_at                    TIMESTAMPTZ,
            CHECK (end_date > start_date),
            CHECK (
                currency <> 'USD'
                OR (adjustment_frequency_months IS NULL AND adjustment_index IS NULL)
            )
        )
    """)

    # ─── RN-C01: no-solapamiento de contratos activos sobre una propiedad ──
    # EXCLUDE via btree_gist: dos contratos `active` (no borrados) con
    # daterange solapado sobre la misma property_id fallan al insertar.
    # Contratos en otros estados (draft/expired/terminated) o sobre
    # propiedades distintas no chocan.
    op.execute("""
        ALTER TABLE contracts ADD CONSTRAINT contracts_no_overlap
        EXCLUDE USING gist (
            property_id WITH =,
            daterange(start_date, end_date, '[]') WITH &&
        ) WHERE (status = 'active' AND deleted_at IS NULL)
    """)

    # ─── contract_adjustments ──────────────────────────────────────────
    # spec_data_model.md §Capa 3 "contract_adjustments": historial de
    # ajustes por indice. `due_period` normalizado a dia 1 del mes (CHECK
    # date_trunc). `pct_applied`/`applied_by`/`applied_at` NULL mientras
    # `pending` -- el operador los completa al aplicar (RN-C03: % manual,
    # nunca automatico). Sin `deleted_at`: el spec no lo declara para esta
    # tabla -- las correcciones se modelan como un ajuste nuevo con nota
    # (backend CLAUDE.md §5, "Inmutables/append-only": ajustes `applied`).
    op.execute("""
        CREATE TABLE contract_adjustments (
            id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            organization_id   UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
            contract_id       UUID NOT NULL REFERENCES contracts(id),
            due_period        DATE NOT NULL
                CHECK (due_period = date_trunc('month', due_period)::date),
            status            TEXT NOT NULL DEFAULT 'pending'
                CHECK (status IN ('pending', 'applied')),
            pct_applied       NUMERIC(14,4),
            previous_amount   NUMERIC(14,2),
            new_amount        NUMERIC(14,2),
            notes             TEXT,
            applied_by        UUID REFERENCES users(id),
            applied_at        TIMESTAMPTZ,
            created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at        TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)

    # ─── Indices (spec_data_model.md §"Indices PostgreSQL Recomendados") ──
    op.execute(
        "CREATE INDEX idx_contracts_organization_status ON contracts (organization_id, status)"
    )
    op.execute(
        "CREATE INDEX idx_contracts_property_id ON contracts (property_id) WHERE deleted_at IS NULL"
    )
    op.execute(
        "CREATE INDEX idx_contracts_org_active_end_date ON contracts (organization_id, end_date) "
        "WHERE status = 'active'"
    )
    # RN-C03: un solo ajuste `pending` por contrato (CA-16-03).
    op.execute(
        "CREATE UNIQUE INDEX idx_contract_adjustments_one_pending_per_contract "
        "ON contract_adjustments (contract_id) WHERE status = 'pending'"
    )
    # No declarado explicitamente en "Indices Recomendados" para esta tabla,
    # pero aplica el patron general multi-tenant ("toda tabla tenant-scoped
    # indexa organization_id") -- sin filtro deleted_at porque la tabla no
    # tiene esa columna.
    op.execute(
        "CREATE INDEX idx_contract_adjustments_organization_id "
        "ON contract_adjustments (organization_id)"
    )

    # ─── Row Level Security (CA-16-02 aislamiento, no confundir con el
    # CHECK de USD que tambien es "CA-16-02" en el issue) ─────────────────
    for table in _TENANT_SCOPED_TABLES:
        _enable_rls_with_force(table)

    # ─── Grants ───────────────────────────────────────────────────────────
    # Piso general (ALTER DEFAULT PRIVILEGES, issue #3) ya otorgo
    # SELECT/INSERT/UPDATE/DELETE a ambos roles -- se deja explicito aca
    # (mismo criterio que Capa 0/1/2) para que la migracion sea
    # auto-contenida y legible sin cruzar con la migracion del issue #3.
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON contracts, contract_adjustments "
        "TO adminprop_app, adminprop_superadmin"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS contract_adjustments CASCADE")
    op.execute("DROP TABLE IF EXISTS contracts CASCADE")
