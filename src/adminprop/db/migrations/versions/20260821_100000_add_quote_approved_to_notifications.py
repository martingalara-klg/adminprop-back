"""add_quote_approved_to_notifications — sexto valor del enum event_type (issue #31)

SDD: infrastructure/spec_notificaciones.md v1.1 "Eventos del MVP y
     enrutamiento por rol" (decision #115: `quote_approved` agregado --
     aviso al encargado al aprobarse su cotizacion, cierra la brecha
     CA-06-03 detectada en el issue #26) + §Historial.
Implements: CA-06-03 (parte diferida del #26), RN-01 (enrutamiento por
            rol: `quote_approved` -> usuarios `maintenance`).

La migracion original (`20260814_201500_create_notifications.py`, issue
#11) NO se toca -- regla de oro de `docs/prompts/session-start.md`
("nunca modificar una migracion ya mergeada"). El CHECK de
`event_type` se recrea via DROP CONSTRAINT + ADD CONSTRAINT con el mismo
nombre autogenerado por Postgres para una CHECK inline sin nombre
explicito (`<tabla>_<columna>_check`), verificado contra el esquema real
de la migracion #11 (`notifications_event_type_check`).
"""

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "20260821_100000"
down_revision: str | None = "20260820_090000"
branch_labels: tuple[str, ...] | None = None
depends_on: tuple[str, ...] | None = None

_CONSTRAINT_NAME = "notifications_event_type_check"


def upgrade() -> None:
    op.execute(f"ALTER TABLE notifications DROP CONSTRAINT {_CONSTRAINT_NAME}")
    op.execute(f"""
        ALTER TABLE notifications ADD CONSTRAINT {_CONSTRAINT_NAME} CHECK (event_type IN (
            'adjustment_pending',
            'contract_expiring',
            'quote_submitted',
            'quote_approved',
            'work_order_created',
            'work_order_closed'
        ))
    """)


def downgrade() -> None:
    # Downgrade de un valor de enum (via CHECK) es inherentemente lossy --
    # mismo criterio que un `DROP TYPE`/`ALTER TYPE ... DROP VALUE` real de
    # Postgres: no hay forma de "reversionar" filas que ya usan el sexto
    # valor a uno de los 5 originales sin inventar un mapeo de negocio. Se
    # borran explicitamente (no silenciosamente: el nombre del evento
    # borrado queda documentado aca) antes de re-angostar el CHECK. Sin
    # infra cloud/CD en el MVP (decision #111), `downgrade` solo se ejerce
    # en dev/test -- nunca contra datos de produccion reales.
    op.execute("DELETE FROM notifications WHERE event_type = 'quote_approved'")
    op.execute(f"ALTER TABLE notifications DROP CONSTRAINT {_CONSTRAINT_NAME}")
    op.execute(f"""
        ALTER TABLE notifications ADD CONSTRAINT {_CONSTRAINT_NAME} CHECK (event_type IN (
            'adjustment_pending',
            'contract_expiring',
            'quote_submitted',
            'work_order_created',
            'work_order_closed'
        ))
    """)
