"""create_capa6_liquidaciones — recurring_charges + charge_entries +
settlements + settlement_line_items (+ ALTER work_orders)

SDD: infrastructure/spec_data_model.md §Capa 6 — Liquidaciones
     + §"Indices PostgreSQL Recomendados" + §"Orden de Migracion"
     + §Apendice B "Politica de soft delete por entidad"
     + core/sdd_02_domain_model.md (RecurringCharge / ChargeEntry /
       Settlement / SettlementLineItem)
Implements: CA-27-01 (UNIQUE (landlord_id, period) en settlements),
            CA-27-02 (UNIQUE (recurring_charge_id, period) en
            charge_entries), CA-27-03 (ALTER de work_orders que agrega
            settled_in_settlement_id, FK a settlements), RN-D01
            (aislamiento multi-tenant), RN-L04 (vinculo reparacion ->
            liquidacion)

Issue #27: septima capa del modelo de datos (depende de la Capa 4, issue
#20 -- `rent_periods`/`payments`, y de la Capa 5, issue #25 --
`work_orders`). `recurring_charges` es el concepto recurrente de la
propiedad (rentas, muni, UC-11); `charge_entries` es el importe del mes
de ese concepto, ingresado a mano (UC-11); `settlements` es la
liquidacion mensual por propietario, toda en ARS (UC-12, RN-L01, RN-L06);
`settlement_line_items` es el detalle linea por linea de la liquidacion.

`work_orders.settled_in_settlement_id` (FK a `settlements`) es la
referencia diferida documentada explicitamente en el spec ("FK agregada
por ALTER en Capa 6") y en `20260819_150000_create_capa5_mantenimiento.py`
("NO se agrega en esta migracion... se agrega con ALTER TABLE en la
migracion de la Capa 6") -- `settlements` recien existe a partir de esta
migracion, de ahi el ALTER posterior a su creacion.

`recurring_charges` tiene `deleted_at` (Apendice B la incluye junto con
`work_orders`/`attachments`). `charge_entries`, `settlements` y
`settlement_line_items` NO tienen `deleted_at`: Apendice B declara "Sin
delete" para las tres -- se corrigen/regeneran (RN-L03: "regeneracion
libre pero auditada"), nunca se borran.

Alcance estrictamente de migracion (mismo criterio que los issues
#14/#16/#20/#25): no se agregan modelos ORM (`modules/settlements/
models.py`), router/service/repository, ni se implementa la logica de
generacion/regeneracion de liquidaciones -- eso es el issue #28
(cargos del mes) en adelante. Tampoco se toca `settlement_hook.
is_work_order_settled` del modulo de mantenimiento (llega con el modulo
de liquidaciones) ni se mapea `settled_in_settlement_id` en el modelo
ORM `WorkOrder` -- la DB puede tener columnas que el ORM aun no mapea.
"""

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "20260820_090000"
down_revision: str | None = "20260819_150000"
branch_labels: tuple[str, ...] | None = None
depends_on: tuple[str, ...] | None = None

_TENANT_SCOPED_TABLES = (
    "recurring_charges",
    "charge_entries",
    "settlements",
    "settlement_line_items",
)


def _enable_rls_with_force(table: str) -> None:
    """Aplica el patron RLS canonico (ENABLE + politica + FORCE) a `table`.

    Mismo patron que `20260819_150000_create_capa5_mantenimiento.py`
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
    # ─── recurring_charges ──────────────────────────────────────────────────
    # spec_data_model.md §Capa 6 "recurring_charges": el concepto recurrente
    # de la propiedad (rentas, muni, UC-11). Con `deleted_at` (Apendice B).
    op.execute("""
        CREATE TABLE recurring_charges (
            id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            organization_id UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
            property_id     UUID NOT NULL REFERENCES properties(id),
            charge_type     TEXT NOT NULL
                CHECK (charge_type IN ('rentas', 'municipalidad', 'otro')),
            label           TEXT NOT NULL,
            is_active       BOOLEAN NOT NULL DEFAULT TRUE,
            created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
            deleted_at      TIMESTAMPTZ
        )
    """)

    # ─── charge_entries ─────────────────────────────────────────────────────
    # spec_data_model.md §Capa 6 "charge_entries": el importe del mes de un
    # concepto, ingresado a mano (UC-11). `period` normalizado a dia 1 del
    # mes (mismo CHECK date_trunc que `rent_periods.period`, Capa 4).
    # CA-27-02: UNIQUE (recurring_charge_id, period). Sin `deleted_at`
    # (Apendice B: "Sin delete" -- se corrigen/regeneran).
    op.execute("""
        CREATE TABLE charge_entries (
            id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            organization_id     UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
            recurring_charge_id UUID NOT NULL REFERENCES recurring_charges(id),
            period              DATE NOT NULL
                CHECK (period = date_trunc('month', period)::date),
            amount              NUMERIC(14,2) NOT NULL
                CHECK (amount >= 0),
            notes               TEXT,
            created_by          UUID NOT NULL REFERENCES users(id),
            created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at          TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)

    # CA-27-02: un solo importe por concepto y mes. El UNIQUE constraint
    # crea su propio indice compuesto (recurring_charge_id, period) --
    # mismo criterio que `rent_periods_contract_period_unique` en
    # `20260819_140000_create_capa4_cobranzas.py`: satisface a la vez el
    # criterio de aceptacion y la recomendacion de indice homonima del
    # spec ("CREATE INDEX ON charge_entries (organization_id, period)" se
    # agrega aparte mas abajo, con distinta columna lider).
    op.execute(
        "ALTER TABLE charge_entries ADD CONSTRAINT charge_entries_recurring_charge_period_unique "
        "UNIQUE (recurring_charge_id, period)"
    )

    # ─── settlements ────────────────────────────────────────────────────────
    # spec_data_model.md §Capa 6 "settlements": la liquidacion mensual por
    # propietario, toda en ARS (UC-12, RN-L01, RN-L06). `exchange_rate`
    # NULL por defecto -- CHECK (> 0) no rechaza NULL (comparacion contra
    # NULL es UNKNOWN, no FALSE, en Postgres); la obligatoriedad cuando hay
    # montos USD en el periodo es RN-L06, validacion app-level. CA-27-01:
    # UNIQUE (landlord_id, period). Sin `deleted_at` (Apendice B: "Sin
    # delete" -- RN-L03 regeneracion libre pero auditada).
    op.execute("""
        CREATE TABLE settlements (
            id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            organization_id         UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
            landlord_id             UUID NOT NULL REFERENCES landlords(id),
            period                  DATE NOT NULL
                CHECK (period = date_trunc('month', period)::date),
            status                  TEXT NOT NULL DEFAULT 'draft'
                CHECK (status IN ('draft', 'issued')),
            exchange_rate           NUMERIC(14,4)
                CHECK (exchange_rate > 0),
            total_collected         NUMERIC(14,2) NOT NULL DEFAULT 0,
            commission_total        NUMERIC(14,2) NOT NULL DEFAULT 0,
            charges_total           NUMERIC(14,2) NOT NULL DEFAULT 0,
            repairs_total           NUMERIC(14,2) NOT NULL DEFAULT 0,
            already_settled_total   NUMERIC(14,2) NOT NULL DEFAULT 0,
            net_amount              NUMERIC(14,2) NOT NULL DEFAULT 0,
            commission_pct_used     NUMERIC(14,4) NOT NULL,
            regenerated_count       SMALLINT NOT NULL DEFAULT 0,
            generated_by            UUID NOT NULL REFERENCES users(id),
            issued_at               TIMESTAMPTZ,
            created_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at              TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)

    # CA-27-01: una liquidacion por propietario y mes. El UNIQUE constraint
    # crea su propio indice compuesto (landlord_id, period) -- satisface a
    # la vez el criterio de aceptacion y la recomendacion de indice
    # homonima del spec ("CREATE UNIQUE INDEX ON settlements (landlord_id,
    # period)"), sin duplicar un indice manual redundante.
    op.execute(
        "ALTER TABLE settlements ADD CONSTRAINT settlements_landlord_period_unique "
        "UNIQUE (landlord_id, period)"
    )

    # ─── settlement_line_items ──────────────────────────────────────────────
    # spec_data_model.md §Capa 6 "settlement_line_items": el detalle linea
    # por linea de la liquidacion. `source_entity_id` es polimorfica igual
    # que `attachments.entity_id` (Capa 5) -- sin FK fisica, integridad
    # app-level (`payment` / `charge_entry` / `work_order`). Sin
    # `deleted_at` (Apendice B: "Sin delete").
    op.execute("""
        CREATE TABLE settlement_line_items (
            id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            organization_id     UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
            settlement_id       UUID NOT NULL REFERENCES settlements(id),
            line_type           TEXT NOT NULL
                CHECK (line_type IN (
                    'rent_collected', 'commission', 'tax_charge', 'repair', 'already_settled'
                )),
            property_id         UUID REFERENCES properties(id),
            source_entity_type  TEXT,
            source_entity_id    UUID,
            original_amount     NUMERIC(14,2) NOT NULL,
            original_currency   TEXT NOT NULL
                CHECK (original_currency IN ('ARS', 'USD')),
            amount_ars          NUMERIC(14,2) NOT NULL,
            description         TEXT,
            created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)

    # ─── ALTER work_orders: referencia diferida a settlements (capa 5 -> 6) ─
    # spec_data_model.md §Capa 6 "ALTER en esta capa:
    # work_orders.settled_in_settlement_id -> FK a settlements (referencia
    # diferida entre capas)" + §"Orden de Migracion" nota final. RN-L04:
    # una reparacion se marca como liquidada al incluirse en una settlement.
    op.execute(
        "ALTER TABLE work_orders ADD COLUMN settled_in_settlement_id "
        "UUID REFERENCES settlements(id)"
    )

    # ─── Indices (spec_data_model.md §"Indices PostgreSQL Recomendados") ──
    op.execute(
        "CREATE INDEX idx_settlement_line_items_settlement_id ON settlement_line_items "
        "(settlement_id)"
    )
    op.execute(
        "CREATE INDEX idx_charge_entries_organization_period ON charge_entries "
        "(organization_id, period)"
    )
    # Patron general del spec: "toda tabla tenant-scoped indexa
    # organization_id" -- recurring_charges no tiene una recomendacion
    # compuesta especifica en la lista, se sigue el patron general
    # (organization_id) WHERE deleted_at IS NULL, igual que las demas
    # tablas con soft delete.
    op.execute(
        "CREATE INDEX idx_recurring_charges_organization_id ON recurring_charges "
        "(organization_id) WHERE deleted_at IS NULL"
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
        "GRANT SELECT, INSERT, UPDATE, DELETE ON recurring_charges, charge_entries, "
        "settlements, settlement_line_items TO adminprop_app, adminprop_superadmin"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE work_orders DROP COLUMN IF EXISTS settled_in_settlement_id")
    op.execute("DROP TABLE IF EXISTS settlement_line_items CASCADE")
    op.execute("DROP TABLE IF EXISTS settlements CASCADE")
    op.execute("DROP TABLE IF EXISTS charge_entries CASCADE")
    op.execute("DROP TABLE IF EXISTS recurring_charges CASCADE")
