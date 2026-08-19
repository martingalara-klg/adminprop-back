"""create_capa4_cobranzas — rent_periods + payments

SDD: infrastructure/spec_data_model.md §Capa 4 — Cobranzas
     + §"Indices PostgreSQL Recomendados" + §"Orden de Migracion"
     + §Apendice B "Politica de soft delete por entidad"
     + core/sdd_02_domain_model.md §2.9 (RentPeriod) / §2.10 (Payment)
Implements: CA-20-01 (UNIQUE (contract_id, period)), CA-20-02 (CHECK
            paid_total <= amount_due), CA-20-03 (indices del spec creados),
            RN-D01 (aislamiento multi-tenant), RN-P01 (un solo periodo por
            contrato+mes), RN-P05 (pagos parciales -- status 'partial'),
            RN-D04 (anulacion logica de cobros via voided_at/voided_by)

Issue #20: quinta capa del modelo de datos (depende de la Capa 3, issue
#16 -- `contracts`). Puerta de entrada a la Fase 5 (cobranzas):
`rent_periods` es el alquiler de un mes de un contrato (RN-P01, generado
el 1 de cada mes por el issue #21 -- este issue solo crea el schema, no
el job de generacion). `payments` es la imputacion de un cobro contra un
periodo (RN-P02..RN-P07 -- issue #22/#23 implementan la logica de mora e
imputacion; aca solo se modelan las columnas donde esa logica persiste).

`rent_periods` NO tiene `deleted_at`: Apendice B declara "Sin delete" para
esta tabla -- los periodos se corrigen/regeneran, nunca se borran.
`payments` tampoco tiene `deleted_at`: su mecanismo de baja logica es
`voided_at` + `voided_by` (anulacion auditada, RN-D04), no soft delete
generico.

Alcance estrictamente de migracion (mismo criterio que los issues
#12/#14/#16): no se agregan modelos ORM (`modules/payments/models.py`) ni
router/service/repository -- eso es el issue #21 (generacion mensual) y
posteriores del modulo de cobranzas.
"""

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "20260819_140000"
down_revision: str | None = "20260819_123059"
branch_labels: tuple[str, ...] | None = None
depends_on: tuple[str, ...] | None = None

_TENANT_SCOPED_TABLES = ("rent_periods", "payments")


def _enable_rls_with_force(table: str) -> None:
    """Aplica el patron RLS canonico (ENABLE + politica + FORCE) a `table`.

    Mismo patron que `20260815_110000_create_capa3_contratos.py` /
    `20260815_100000_create_capa2_propiedades.py`
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
    # ─── rent_periods ──────────────────────────────────────────────────────
    # spec_data_model.md §Capa 4 "rent_periods": el alquiler de un mes de un
    # contrato. `period` normalizado a dia 1 del mes (mismo CHECK
    # date_trunc que `contract_adjustments.due_period`, Capa 3).
    # `amount_due`/`currency` son una copia congelada del contrato al
    # generarse (sdd_02 §2.9: "amount_due refleja el monto vigente del
    # contrato al momento de generarse") -- no FK a `contracts.current_amount`
    # porque ese valor cambia con ajustes futuros y no debe arrastrar el
    # historico. Sin `deleted_at` (Apendice B: "Sin delete").
    op.execute("""
        CREATE TABLE rent_periods (
            id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            organization_id UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
            contract_id     UUID NOT NULL REFERENCES contracts(id),
            period          DATE NOT NULL
                CHECK (period = date_trunc('month', period)::date),
            amount_due      NUMERIC(14,2) NOT NULL,
            currency        TEXT NOT NULL
                CHECK (currency IN ('ARS', 'USD')),
            status          TEXT NOT NULL DEFAULT 'pending'
                CHECK (status IN ('pending', 'partial', 'paid')),
            paid_total      NUMERIC(14,2) NOT NULL DEFAULT 0
                CHECK (paid_total <= amount_due),
            created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)

    # RN-P01: un solo periodo por contrato y mes. El UNIQUE constraint crea
    # su propio indice compuesto (contract_id, period) -- satisface a la vez
    # CA-20-01 y la recomendacion de indice homonima del spec, sin duplicar
    # un indice manual redundante.
    op.execute(
        "ALTER TABLE rent_periods ADD CONSTRAINT rent_periods_contract_period_unique "
        "UNIQUE (contract_id, period)"
    )

    # ─── payments ────────────────────────────────────────────────────────
    # spec_data_model.md §Capa 4 "payments": la imputacion de un cobro.
    # `exchange_rate` NULL por defecto -- CHECK (> 0) no rechaza NULL (una
    # comparacion contra NULL es UNKNOWN, no FALSE, en Postgres); la
    # obligatoriedad cuando `payment_currency` difiere de la moneda del
    # contrato es RN-P06, validacion app-level (requiere leer
    # `contracts.currency`, fuera del alcance de un CHECK de esta tabla).
    # `voided_at`/`voided_by` son la anulacion logica (RN-D04) -- sin
    # `deleted_at` (Apendice B).
    op.execute("""
        CREATE TABLE payments (
            id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            organization_id     UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
            rent_period_id      UUID NOT NULL REFERENCES rent_periods(id),
            payment_date        DATE NOT NULL,
            method              TEXT NOT NULL
                CHECK (method IN ('cash', 'transfer')),
            payment_currency    TEXT NOT NULL
                CHECK (payment_currency IN ('ARS', 'USD')),
            amount              NUMERIC(14,2) NOT NULL
                CHECK (amount > 0),
            exchange_rate       NUMERIC(14,4)
                CHECK (exchange_rate > 0),
            destination         TEXT NOT NULL
                CHECK (destination IN ('agency_account', 'landlord_account')),
            suggested_interest  NUMERIC(14,2) NOT NULL DEFAULT 0,
            charged_interest    NUMERIC(14,2) NOT NULL DEFAULT 0,
            forgiven_interest   NUMERIC(14,2) NOT NULL DEFAULT 0,
            days_late           SMALLINT NOT NULL DEFAULT 0,
            notes               TEXT,
            voided_at           TIMESTAMPTZ,
            voided_by           UUID REFERENCES users(id),
            created_by          UUID NOT NULL REFERENCES users(id),
            created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at          TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)

    # ─── Indices (spec_data_model.md §"Indices PostgreSQL Recomendados") ──
    op.execute(
        "CREATE INDEX idx_rent_periods_organization_period ON rent_periods "
        "(organization_id, period)"
    )
    op.execute(
        "CREATE INDEX idx_rent_periods_org_status_not_paid ON rent_periods "
        "(organization_id, status) WHERE status <> 'paid'"
    )
    op.execute(
        "CREATE INDEX idx_payments_rent_period_id_not_voided ON payments "
        "(rent_period_id) WHERE voided_at IS NULL"
    )
    op.execute(
        "CREATE INDEX idx_payments_organization_payment_date ON payments "
        "(organization_id, payment_date)"
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
        "GRANT SELECT, INSERT, UPDATE, DELETE ON rent_periods, payments "
        "TO adminprop_app, adminprop_superadmin"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS payments CASCADE")
    op.execute("DROP TABLE IF EXISTS rent_periods CASCADE")
