"""create_neighborhoods_and_alter_properties — catalogo de barrios (issue #99)

SDD: infrastructure/spec_data_model.md §Capa 2 -- Propiedades ("neighborhoods")
     + core/sdd_02_domain_model.md §2.4a (Barrio)
     + core/sdd_03_api_contracts.md §7/7.1
Implements: CA-01-07, CA-01-08, CA-01-09, RN-D01 (aislamiento), RN-D02 (soft delete)

Issue #99 (feedback de uso real, 2026-08-27): catalogo de barrios
parametrizable por organizacion, con la intencion futura de agrupar por
barrio en liquidaciones y vistas. Decision del PO: el campo es
OBLIGATORIO en el alta/edicion de propiedades desde ahora, pero
`properties.neighborhood_id` se agrega NULLABLE en DB (datos legacy
preexistentes) -- la obligatoriedad se enforza a nivel de API
(modulo `properties`, issue #99), no con un CHECK/NOT NULL en la
columna, para no romper las filas ya existentes.
"""

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "20260827_100000"
down_revision: str | None = "20260824_100000"
branch_labels: tuple[str, ...] | None = None
depends_on: tuple[str, ...] | None = None


def upgrade() -> None:
    # ─── neighborhoods ──────────────────────────────────────────────────
    # spec_data_model.md §Capa 2 "neighborhoods": catalogo de barrios por
    # organizacion. Soft delete + RLS FORCE, mismo patron que el resto de
    # las tablas tenant-scoped de esta capa.
    op.execute("""
        CREATE TABLE neighborhoods (
            id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            organization_id UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
            name            TEXT NOT NULL,
            created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
            deleted_at      TIMESTAMPTZ
        )
    """)

    # Indices (spec_data_model.md §Capa 2 "neighborhoods"):
    # - organization_id para el listado del catalogo.
    # - UNIQUE (organization_id, lower(name)) WHERE deleted_at IS NULL:
    #   `name` unico por organizacion, case-insensitive, solo entre filas
    #   vivas -- un barrio borrado no bloquea reusar su nombre.
    op.execute(
        "CREATE INDEX idx_neighborhoods_organization_id ON neighborhoods (organization_id) "
        "WHERE deleted_at IS NULL"
    )
    op.execute(
        "CREATE UNIQUE INDEX uq_neighborhoods_org_name ON neighborhoods "
        "(organization_id, lower(name)) WHERE deleted_at IS NULL"
    )

    # ─── Row Level Security (RN-D01) ──────────────────────────────────────
    op.execute("ALTER TABLE neighborhoods ENABLE ROW LEVEL SECURITY")
    op.execute("""
        CREATE POLICY neighborhoods_tenant_isolation ON neighborhoods
        USING (
            organization_id = NULLIF(current_setting('app.current_tenant_id', true), '')::uuid
        )
        WITH CHECK (
            organization_id = NULLIF(current_setting('app.current_tenant_id', true), '')::uuid
        )
    """)
    op.execute("ALTER TABLE neighborhoods FORCE ROW LEVEL SECURITY")

    # ─── properties.neighborhood_id ────────────────────────────────────────
    # Nullable en DB (datos legacy preexistentes a issue #99) -- la
    # obligatoriedad de ahora en mas rige a nivel de API (modulo
    # `properties`), no en el schema.
    op.execute(
        "ALTER TABLE properties ADD COLUMN neighborhood_id UUID REFERENCES neighborhoods(id)"
    )
    op.execute(
        "CREATE INDEX idx_properties_neighborhood_id ON properties (neighborhood_id) "
        "WHERE deleted_at IS NULL"
    )

    # ─── Grants ────────────────────────────────────────────────────────────
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON neighborhoods "
        "TO adminprop_app, adminprop_superadmin"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE properties DROP COLUMN IF EXISTS neighborhood_id")
    op.execute("DROP TABLE IF EXISTS neighborhoods CASCADE")
