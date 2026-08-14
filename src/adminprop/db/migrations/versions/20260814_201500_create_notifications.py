"""create_notifications — avisos in-app + outbox de email (issue #11)

SDD: infrastructure/spec_data_model.md §Capa 7 "notifications" (tabla
     adelantada a la Fase 1 -- solo depende de Capa 0, mismo criterio
     que `audit_logs` en `20260814_190741_create_audit_logs.py`)
     + §"Indices PostgreSQL Recomendados"
     + infrastructure/spec_notificaciones.md RF-01 (patron outbox
       `email_sent_at IS NULL`), RF-04 (retry de email)
Implements: CA-NT-02 (rollback de negocio no deja notificacion --
            invariante que da la MISMA transaccion, no esta migracion),
            RN-D01 (aislamiento multi-tenant)

`shared/notifications/service.py.emit()` (issue #11) es el unico
escritor de esta tabla. Mismo patron RLS `NULLIF(current_setting(...),
'')::uuid` + FORCE que `20260812_212704_create_capa0_fundacion.py` y
`20260814_190741_create_audit_logs.py`. A diferencia de `audit_logs`,
`notifications` SI es mutable (`read_at`/`email_sent_at` se actualizan
in-place -- spec_data_model.md §"Estrategia de mutabilidad": "notifications
| read_at | No se borran en MVP"), asi que `adminprop_app` conserva UPDATE
(necesario para marcar `email_sent_at`/`read_at`), sin el REVOKE que
`audit_logs` aplica para su invariante append-only (RN-D03, que no aplica
aca).
"""

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "20260814_201500"
down_revision: str | None = "20260814_190741"
branch_labels: tuple[str, ...] | None = None
depends_on: tuple[str, ...] | None = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")

    # ─── notifications ───────────────────────────────────────────────────
    # spec_data_model.md §Capa 7 "notifications": columnas exactas del
    # spec. `event_type` CHECK con los 5 valores del MVP
    # (spec_notificaciones.md "Eventos del MVP y enrutamiento por rol");
    # agregar un evento nuevo = actualizar el SDD primero (regla de oro).
    op.execute("""
        CREATE TABLE notifications (
            id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            organization_id UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
            user_id         UUID NOT NULL REFERENCES users(id),
            event_type      TEXT NOT NULL CHECK (event_type IN (
                'adjustment_pending',
                'contract_expiring',
                'quote_submitted',
                'work_order_created',
                'work_order_closed'
            )),
            payload         JSONB NOT NULL DEFAULT '{}'::JSONB,
            read_at         TIMESTAMPTZ,
            email_sent_at   TIMESTAMPTZ,
            created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)

    # ─── Indices (spec_data_model.md §"Indices PostgreSQL Recomendados") ──
    # `(user_id) WHERE read_at IS NULL`: consulta canonica del panel
    # in-app (badge de no leidas, issue #31).
    op.execute(
        "CREATE INDEX idx_notifications_user_id_unread "
        "ON notifications (user_id) WHERE read_at IS NULL"
    )
    # Decision de implementacion (issue #11, no en el SDD literal): el
    # worker de outbox (`notification_worker.send_notification_email`)
    # consulta por `organization_id` + `email_sent_at IS NULL` para
    # resolver el nombre/reply-to de la organizacion y lockear la fila
    # pendiente -- este indice parcial evita un seq scan sobre
    # `notifications` a medida que la tabla crece (mismo criterio del
    # patron general "CREATE INDEX ON <tabla> (organization_id) WHERE
    # <condicion-de-filtrado-frecuente>" de spec_data_model.md).
    op.execute(
        "CREATE INDEX idx_notifications_organization_id_pending_email "
        "ON notifications (organization_id) WHERE email_sent_at IS NULL"
    )

    # ─── Row Level Security (RN-D01) ─────────────────────────────────────
    op.execute("ALTER TABLE notifications ENABLE ROW LEVEL SECURITY")
    op.execute("""
        CREATE POLICY notifications_tenant_isolation ON notifications
        USING (
            organization_id = NULLIF(current_setting('app.current_tenant_id', true), '')::uuid
        )
        WITH CHECK (
            organization_id = NULLIF(current_setting('app.current_tenant_id', true), '')::uuid
        )
    """)
    op.execute("ALTER TABLE notifications FORCE ROW LEVEL SECURITY")

    # ─── Grants ─────────────────────────────────────────────────────────
    # A diferencia de `audit_logs` (append-only, RN-D03), `notifications`
    # es mutable (`read_at`/`email_sent_at`) -- `adminprop_app` conserva
    # el grant completo que `ALTER DEFAULT PRIVILEGES` (issue #3) ya
    # otorga por default; se deja explicito aca para que la migracion sea
    # auto-contenida (mismo criterio que Capa 0 y `audit_logs`).
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON notifications "
        "TO adminprop_app, adminprop_superadmin"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS notifications CASCADE")
