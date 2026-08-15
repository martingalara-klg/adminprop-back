"""create_capa1_personas — landlords + renters (bank_info cifrado pgcrypto)

SDD: infrastructure/spec_data_model.md §Capa 1 — Personas (Propietarios e
     Inquilinos) + §"Indices PostgreSQL Recomendados" + §"Orden de Migracion"
     + core/sdd_04_nonfunctional.md §2.4 "Cifrado y CSRF"
Implements: CA-12-01 (cifrado de `bank_info` verificable a nivel de DB,
            via pgcrypto), CA-12-02 (RLS habilitado en ambas tablas),
            CA-12-03 (`commission_pct` con CHECK de rango 0-100),
            RN-D01 (aislamiento multi-tenant), RN-D02 (soft delete)

Issue #12: segunda capa del modelo de datos (depende de la Capa 0,
issue #5). `landlords` (el propietario, a quien se le rinde) y `renters`
(el inquilino) son las dos entidades de persona sin login del dominio.

Desviacion deliberada de tipo respecto al spec (documentada, no una
divergencia silenciosa): `spec_data_model.md` lista `landlords.bank_info`
como `TEXT`, pero `sdd_04 §2.4` exige que ese campo este "cifrado
columnar (pgcrypto AES-256)". Ambos documentos son consistentes si se
lee `TEXT` como "el dato de negocio es texto libre" y `sdd_04` como "la
persistencia fisica de ese texto es BYTEA cifrado" — se materializa la
columna como `BYTEA` (ciphertext de `pgp_sym_encrypt`, ver
`docs/skills/database-migration.md` §"Plantilla para columna con
encriptacion columnar (pgcrypto)"). El helper de cifrado/descifrado vive
en `shared/encryption/pgcrypto.py` (issue #13 lo consume desde el
repository de `landlords`; esta migracion solo deja el mecanismo listo).
"""

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "20260815_090000"
down_revision: str | None = "20260814_201500"
branch_labels: tuple[str, ...] | None = None
depends_on: tuple[str, ...] | None = None

_TENANT_SCOPED_TABLES = ("landlords", "renters")


def _enable_rls_with_force(table: str) -> None:
    """Aplica el patron RLS canonico (ENABLE + politica + FORCE) a `table`.

    Mismo patron que `20260812_212704_create_capa0_fundacion.py` /
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
    op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")

    # ─── landlords ────────────────────────────────────────────────────────
    # spec_data_model.md §Capa 1 "landlords": el dueno de las propiedades,
    # a quien se le rinde. `bank_info` en BYTEA (cifrado pgcrypto AES-256,
    # ver docstring del modulo). `commission_pct` con CHECK 0-100 (CA-12-03).
    op.execute("""
        CREATE TABLE landlords (
            id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            organization_id UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
            name            TEXT NOT NULL,
            tax_id          TEXT,
            phone           TEXT,
            email           TEXT,
            bank_info       BYTEA,
            commission_pct  NUMERIC(14,4) NOT NULL
                CHECK (commission_pct >= 0 AND commission_pct <= 100),
            notes           TEXT,
            metadata        JSONB NOT NULL DEFAULT '{}'::JSONB,
            created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
            deleted_at      TIMESTAMPTZ
        )
    """)

    # ─── renters ──────────────────────────────────────────────────────────
    # spec_data_model.md §Capa 1 "renters": el inquilino. Sin datos
    # bancarios ni comision (no aplica: no se le rinde).
    op.execute("""
        CREATE TABLE renters (
            id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            organization_id UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
            name            TEXT NOT NULL,
            tax_id          TEXT,
            phone           TEXT,
            email           TEXT,
            notes           TEXT,
            metadata        JSONB NOT NULL DEFAULT '{}'::JSONB,
            created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
            deleted_at      TIMESTAMPTZ
        )
    """)

    # ─── Indices (spec_data_model.md §"Indices PostgreSQL Recomendados") ──
    # Patron general multi-tenant: "CREATE INDEX ON <tabla> (organization_id)
    # WHERE deleted_at IS NULL" — ambas tablas son soft-delete.
    op.execute(
        "CREATE INDEX idx_landlords_organization_id ON landlords (organization_id) "
        "WHERE deleted_at IS NULL"
    )
    op.execute(
        "CREATE INDEX idx_renters_organization_id ON renters (organization_id) "
        "WHERE deleted_at IS NULL"
    )

    # ─── Row Level Security (CA-12-02) ───────────────────────────────────
    for table in _TENANT_SCOPED_TABLES:
        _enable_rls_with_force(table)

    # ─── Grants ───────────────────────────────────────────────────────────
    # Piso general (ALTER DEFAULT PRIVILEGES, issue #3) ya otorgo
    # SELECT/INSERT/UPDATE/DELETE a ambos roles — se deja explicito aca
    # (mismo criterio que Capa 0 / audit_logs) para que la migracion sea
    # auto-contenida y legible sin cruzar con la migracion del issue #3.
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON landlords, renters "
        "TO adminprop_app, adminprop_superadmin"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS renters CASCADE")
    op.execute("DROP TABLE IF EXISTS landlords CASCADE")
