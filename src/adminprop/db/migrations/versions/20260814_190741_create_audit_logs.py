"""create_audit_logs — tabla append-only de auditoria transversal

SDD: infrastructure/spec_data_model.md §Capa 7 "audit_logs" (tabla
     adelantada a la Fase 1 -- solo depende de Capa 0) + §"Indices
     PostgreSQL Recomendados"
     + core/sdd_02_domain_model.md §2.17 "Log de Auditoria (AuditLog)"
Implements: RN-D03 (append-only e inmutable), RN-D04 (correcciones de
            cobros/liquidaciones siempre trazadas), RN-A04 (accesos
            denegados auditados)

Issue #10: adelanta `audit_logs` (Capa 7 del modelo de datos) porque el
`AuditService` transversal (usado por todos los modulos de negocio desde
esta fase en adelante) solo depende de `organizations`/`users` (Capa 0,
issue #5). Mismo patron RLS `NULLIF(current_setting(...), '')::uuid` +
FORCE que `20260812_212704_create_capa0_fundacion.py`.

RN-D03 "append-only e inmutable" se enforza a nivel de permisos de
PostgreSQL: se REVOCA UPDATE/DELETE sobre `audit_logs` al rol
`adminprop_app` (el rol de runtime de la app), dejando solo INSERT y
SELECT -- ver spec_data_model.md §"audit_logs" ("Sin UPDATE ni DELETE,
revocados a nivel de permisos del rol `adminprop_app`"). El default
otorgado por `ALTER DEFAULT PRIVILEGES` en
`20260812_114322_setup_extensions_and_roles.py` (SELECT/INSERT/UPDATE/
DELETE) queda asi angostado especificamente para esta tabla, tal como
esa migracion ya anticipaba en su comentario.
"""

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "20260814_190741"
down_revision: str | None = "20260812_212704"
branch_labels: tuple[str, ...] | None = None
depends_on: tuple[str, ...] | None = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")

    # ─── audit_logs ──────────────────────────────────────────────────────
    # spec_data_model.md §Capa 7 "audit_logs": columnas exactas del spec.
    # `user_id` NULL para acciones del sistema (jobs, workers sin actor
    # humano). `entity_id` NULL cuando el evento no ata a una entidad
    # puntual (ej: `access.denied` sobre un permiso, no un recurso).
    op.execute("""
        CREATE TABLE audit_logs (
            id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            organization_id UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
            user_id         UUID REFERENCES users(id),
            action          TEXT NOT NULL,
            entity_type     TEXT NOT NULL,
            entity_id       UUID,
            before_state    JSONB,
            after_state     JSONB,
            request_id      TEXT,
            created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)

    # ─── Indices (spec_data_model.md §"Indices PostgreSQL Recomendados") ──
    op.execute(
        "CREATE INDEX idx_audit_logs_organization_id_created_at "
        "ON audit_logs (organization_id, created_at)"
    )
    op.execute(
        "CREATE INDEX idx_audit_logs_entity_type_entity_id ON audit_logs (entity_type, entity_id)"
    )

    # ─── Row Level Security ────────────────────────────────────────────
    # Mismo patron canonico que `_enable_rls_with_force` de
    # `20260812_212704_create_capa0_fundacion.py` (no se importa esa
    # migracion, se repite el DDL inline: Alembic scripts son independientes
    # y no deben importar codigo de otro archivo de version).
    op.execute("ALTER TABLE audit_logs ENABLE ROW LEVEL SECURITY")
    op.execute("""
        CREATE POLICY audit_logs_tenant_isolation ON audit_logs
        USING (
            organization_id = NULLIF(current_setting('app.current_tenant_id', true), '')::uuid
        )
        WITH CHECK (
            organization_id = NULLIF(current_setting('app.current_tenant_id', true), '')::uuid
        )
    """)
    op.execute("ALTER TABLE audit_logs FORCE ROW LEVEL SECURITY")

    # ─── Grants ─────────────────────────────────────────────────────────
    # Piso general (ALTER DEFAULT PRIVILEGES, issue #3) ya otorgo
    # SELECT/INSERT/UPDATE/DELETE a ambos roles -- se deja explicito aca
    # (mismo criterio que Capa 0) y luego se angosta adminprop_app.
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON audit_logs TO adminprop_app, adminprop_superadmin"
    )
    # RN-D03: append-only enforced a nivel de permisos de PostgreSQL --
    # `adminprop_app` (rol de runtime normal) pierde UPDATE/DELETE; solo
    # le quedan INSERT y SELECT. `adminprop_superadmin` conserva el grant
    # completo (spec_data_model.md §"audit_logs" solo angosta explicitamente
    # el rol de runtime; el bypass de Super Admin es auditado aparte via
    # `superadmin_audit_logs`, fuera de alcance de este issue).
    op.execute("REVOKE UPDATE, DELETE ON audit_logs FROM adminprop_app")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS audit_logs CASCADE")
