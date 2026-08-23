"""create_capa2_propiedades — properties + property_service_accounts

SDD: infrastructure/spec_data_model.md §Capa 2 — Propiedades
     + §"Indices PostgreSQL Recomendados" + §"Orden de Migracion"
Implements: CA-14-01 (tablas identicas al spec), CA-14-02 (RLS + FORCE en
            ambas tablas), CA-14-03 (indices declarados en el spec creados),
            RN-D01 (aislamiento multi-tenant), RN-D02 (soft delete)

Issue #14: tercera capa del modelo de datos (depende de la Capa 1,
issue #12 — `landlords`). `properties` es el inmueble administrado
(`landlord_id` FK NOT NULL a `landlords`); `property_service_accounts`
son los numeros de cuenta de servicios/impuestos, solo informativos
(ninguna logica de negocio depende de ellos, UC-01).

Alcance estrictamente de migracion (mismo criterio que el issue #12):
no se agregan modelos ORM (`modules/propiedades/models.py`) ni
router/service/repository — eso es el issue #15 (Modulo propiedades).
`db/base.py` documenta que las tablas de negocio sin modulo propio
todavia quedan en SQL crudo hasta que su modulo las consume.
"""

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "20260815_100000"
down_revision: str | None = "20260815_090000"
branch_labels: tuple[str, ...] | None = None
depends_on: tuple[str, ...] | None = None

_TENANT_SCOPED_TABLES = ("properties", "property_service_accounts")


def _enable_rls_with_force(table: str) -> None:
    """Aplica el patron RLS canonico (ENABLE + politica + FORCE) a `table`.

    Mismo patron que `20260815_090000_create_capa1_personas.py` /
    `20260814_190741_create_audit_logs.py` (docs/skills/database-migration.md):
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
    # ─── properties ─────────────────────────────────────────────────────
    # spec_data_model.md §Capa 2 "properties": el inmueble administrado.
    # `landlord_id` FK NOT NULL (todo inmueble tiene un propietario a quien
    # rendirle). `status` deriva de si hay contrato activo ('rented'), pero
    # se persiste en la tabla (no calculado) — el modulo de contratos (issue
    # #16) lo actualiza. `property_type` es texto libre sugerido en UI, sin
    # CHECK (spec no lo restringe a un catalogo cerrado).
    op.execute("""
        CREATE TABLE properties (
            id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            organization_id UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
            landlord_id     UUID NOT NULL REFERENCES landlords(id),
            address         TEXT NOT NULL,
            property_type   TEXT NOT NULL DEFAULT 'departamento',
            status          TEXT NOT NULL DEFAULT 'available'
                CHECK (status IN ('available', 'rented', 'unavailable')),
            notes           TEXT,
            metadata        JSONB NOT NULL DEFAULT '{}'::JSONB,
            created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
            deleted_at      TIMESTAMPTZ
        )
    """)

    # ─── property_service_accounts ──────────────────────────────────────
    # spec_data_model.md §Capa 2 "property_service_accounts": numeros de
    # cuenta de servicios e impuestos, solo informativos (UC-01).
    # `secondary_number` nullable (caso luz: n° de contrato adicional).
    op.execute("""
        CREATE TABLE property_service_accounts (
            id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            organization_id   UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
            property_id       UUID NOT NULL REFERENCES properties(id),
            service_type      TEXT NOT NULL
                CHECK (service_type IN (
                    'rentas', 'municipalidad', 'luz', 'gas', 'agua', 'expensas', 'otro'
                )),
            account_number    TEXT NOT NULL,
            secondary_number  TEXT,
            notes             TEXT,
            created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
            deleted_at        TIMESTAMPTZ
        )
    """)

    # ─── Indices (spec_data_model.md §"Indices PostgreSQL Recomendados") ──
    # Patron general multi-tenant: "CREATE INDEX ON <tabla> (organization_id)
    # WHERE deleted_at IS NULL" en ambas tablas (ambas son soft-delete).
    # `property_service_accounts` ademas indexa el patron compuesto
    # (organization_id, property_id) WHERE deleted_at IS NULL — la consulta
    # natural es "cuentas de servicio de esta propiedad, en este tenant".
    op.execute(
        "CREATE INDEX idx_properties_organization_id ON properties (organization_id) "
        "WHERE deleted_at IS NULL"
    )
    op.execute(
        "CREATE INDEX idx_property_service_accounts_organization_id "
        "ON property_service_accounts (organization_id) WHERE deleted_at IS NULL"
    )
    op.execute(
        "CREATE INDEX idx_property_service_accounts_org_property "
        "ON property_service_accounts (organization_id, property_id) "
        "WHERE deleted_at IS NULL"
    )

    # ─── Row Level Security (CA-14-02) ───────────────────────────────────
    for table in _TENANT_SCOPED_TABLES:
        _enable_rls_with_force(table)

    # ─── Grants ───────────────────────────────────────────────────────────
    # Piso general (ALTER DEFAULT PRIVILEGES, issue #3) ya otorgo
    # SELECT/INSERT/UPDATE/DELETE a ambos roles — se deja explicito aca
    # (mismo criterio que Capa 0 / audit_logs / Capa 1) para que la
    # migracion sea auto-contenida y legible sin cruzar con la migracion
    # del issue #3.
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON properties, property_service_accounts "
        "TO adminprop_app, adminprop_superadmin"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS property_service_accounts CASCADE")
    op.execute("DROP TABLE IF EXISTS properties CASCADE")
