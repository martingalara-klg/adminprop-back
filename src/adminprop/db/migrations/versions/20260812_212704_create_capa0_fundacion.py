"""create_capa0_fundacion — organizations, users, roles, organization_members,
organization_invitations + RLS

SDD: infrastructure/spec_data_model.md §Capa 0 — Fundacion (Multi-tenancy e Identidad)
     + §"Indices PostgreSQL Recomendados" + §"Orden de Migracion"
Implements: CA-5-01 (tablas identicas al spec), CA-5-02 (RLS + FORCE en
            tablas tenant-scoped), RN-D01 (aislamiento multi-tenant)

Primeras tablas de negocio del modelo de datos (issue #5, sobre la base de
extensiones/roles del issue #3). `organizations` es la raiz del tenant
(sin RLS: el acceso se controla por membresia, solo `adminprop_superadmin`
la escribe). `users` es identidad global de login (sin RLS: un user puede
pertenecer a varias organizaciones). `roles`, `organization_members` y
`organization_invitations` son tenant-scoped: RLS habilitado + politica +
FORCE ROW LEVEL SECURITY, patron `NULLIF(current_setting(...), '')::uuid`
(docs/skills/database-migration.md, fix descubierto en el issue #3).
"""

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "20260812_212704"
down_revision: str | None = "20260812_114322"
branch_labels: tuple[str, ...] | None = None
depends_on: tuple[str, ...] | None = None

_TENANT_SCOPED_TABLES = ("roles", "organization_members", "organization_invitations")


def _enable_rls_with_force(table: str) -> None:
    """Aplica el patron RLS canonico (ENABLE + politica + FORCE) a `table`.

    docs/skills/database-migration.md / tenant-isolation.md: missing_ok=true
    + NULLIF normalizan tanto "nunca seteado" (NULL) como "limpiado a ''"
    (rutas /superadmin/*) a NULL antes del cast a uuid, evitando un 500 y
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
    op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")

    # ─── organizations ───────────────────────────────────────────────────
    # spec_data_model.md §Capa 0 "organizations": raiz del tenant, sin RLS
    # (el acceso se controla por membresia; solo adminprop_superadmin
    # escribe esta tabla desde /superadmin/*).
    op.execute("""
        CREATE TABLE organizations (
            id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            slug        TEXT NOT NULL UNIQUE,
            name        TEXT NOT NULL,
            status      TEXT NOT NULL DEFAULT 'pending_owner'
                CHECK (status IN ('pending_owner', 'active', 'disabled')),
            timezone    TEXT NOT NULL DEFAULT 'America/Argentina/Cordoba',
            settings    JSONB NOT NULL DEFAULT '{}'::JSONB,
            created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
            deleted_at  TIMESTAMPTZ
        )
    """)
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON organizations TO adminprop_superadmin")
    op.execute("GRANT SELECT ON organizations TO adminprop_app")

    # ─── users ────────────────────────────────────────────────────────────
    # spec_data_model.md §Capa 0 "users": identidad global de login, un
    # user puede pertenecer a varias organizaciones. Excluida de RLS.
    op.execute("""
        CREATE TABLE users (
            id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            email           TEXT NOT NULL UNIQUE,
            password_hash   TEXT NOT NULL,
            full_name       TEXT NOT NULL,
            is_super_admin  BOOLEAN NOT NULL DEFAULT FALSE,
            created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
            deleted_at      TIMESTAMPTZ
        )
    """)
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON users TO adminprop_app, adminprop_superadmin")

    # ─── roles ────────────────────────────────────────────────────────────
    # spec_data_model.md §Capa 0 "roles": RBAC data-driven, sembrado por
    # organizacion. UNIQUE (organization_id, name).
    op.execute("""
        CREATE TABLE roles (
            id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            organization_id UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
            name            TEXT NOT NULL,
            permissions     JSONB NOT NULL,
            is_system_role  BOOLEAN NOT NULL DEFAULT TRUE,
            created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
            UNIQUE (organization_id, name)
        )
    """)

    # ─── organization_members ───────────────────────────────────────────
    # spec_data_model.md §Capa 0 "organization_members": membresia
    # user <-> org con su rol. UNIQUE (organization_id, user_id) — un rol
    # por org (RN-A03: siempre >= 1 owner activo, enforzado a nivel app).
    op.execute("""
        CREATE TABLE organization_members (
            id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            organization_id UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
            user_id         UUID NOT NULL REFERENCES users(id),
            role_id         UUID NOT NULL REFERENCES roles(id),
            status          TEXT NOT NULL DEFAULT 'active'
                CHECK (status IN ('active', 'inactive')),
            created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
            UNIQUE (organization_id, user_id)
        )
    """)

    # ─── organization_invitations ───────────────────────────────────────
    # spec_data_model.md §Capa 0 "organization_invitations": toda alta de
    # usuario nace de invitacion (sin auto-registro). `token` es UNIQUE
    # (hash del token, nunca el token en claro).
    op.execute("""
        CREATE TABLE organization_invitations (
            id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            organization_id UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
            email           TEXT NOT NULL,
            role_id         UUID NOT NULL REFERENCES roles(id),
            token           TEXT NOT NULL UNIQUE,
            status          TEXT NOT NULL DEFAULT 'pending'
                CHECK (status IN ('pending', 'accepted', 'expired', 'revoked')),
            expires_at      TIMESTAMPTZ NOT NULL,
            created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)

    # ─── Indices (spec_data_model.md §"Indices PostgreSQL Recomendados") ─
    # Patron general multi-tenant: "CREATE INDEX ON <tabla> (organization_id)
    # WHERE deleted_at IS NULL". Ninguna de las tres tablas tenant-scoped
    # de esta capa tiene `deleted_at` (no son soft-delete en el spec), asi
    # que el indice es simple sobre organization_id.
    op.execute("CREATE INDEX idx_roles_organization_id ON roles (organization_id)")
    op.execute(
        "CREATE INDEX idx_organization_members_organization_id "
        "ON organization_members (organization_id)"
    )
    op.execute(
        "CREATE INDEX idx_organization_invitations_organization_id "
        "ON organization_invitations (organization_id)"
    )

    # ─── Row Level Security (CA-5-02) ────────────────────────────────────
    # `organizations` y `users` quedan sin RLS por diseno (ver comentarios
    # arriba). Las tres tablas tenant-scoped reciben el patron canonico.
    for table in _TENANT_SCOPED_TABLES:
        _enable_rls_with_force(table)

    # ─── Grants para adminprop_app sobre las tablas tenant-scoped ────────
    # ALTER DEFAULT PRIVILEGES (issue #3) ya cubre GRANTs futuros, pero se
    # deja explicito aca para que esta migracion sea auto-contenida y
    # legible sin tener que cruzar con la migracion anterior.
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON roles, organization_members, "
        "organization_invitations TO adminprop_app, adminprop_superadmin"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS organization_invitations CASCADE")
    op.execute("DROP TABLE IF EXISTS organization_members CASCADE")
    op.execute("DROP TABLE IF EXISTS roles CASCADE")
    op.execute("DROP TABLE IF EXISTS users CASCADE")
    op.execute("DROP TABLE IF EXISTS organizations CASCADE")
