"""add_contracts_expiring_notified_at — marca de idempotencia del aviso de vencimiento

SDD: features/spec_module_03_contratos.md §RF-05 "Alertas de vencimiento":
"Una sola notificacion por contrato y umbral". Implements: CA-03-07.

Issue #19: el job diario `detect_expiring_contracts` necesita distinguir
un contrato ya avisado de uno que todavia no, para no reenviar el email
en cada corrida mientras el contrato siga dentro de la ventana de
`contract_expiry_notice_days`. Se elige una columna en `contracts` (en
vez de deduplicar por payload en `notifications` o crear una tabla
auxiliar) porque es el mismo criterio ya usado por
`notifications.email_sent_at` (marca simple + filtro `IS NULL`) y evita
depender de un indice GIN sobre el JSONB de `notifications.payload` para
un chequeo que se ejecuta una vez por dia por organizacion.

Alcance estrictamente de migracion (mismo criterio que el issue #16): sin
cambios de RLS -- `contracts` ya tiene `ENABLE ROW LEVEL SECURITY` +
politica + `FORCE` desde la migracion #16 (20260815_110000), una columna
nueva no requiere volver a declararlas.
"""

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "20260819_123059"
down_revision: str | None = "20260815_110000"
branch_labels: tuple[str, ...] | None = None
depends_on: tuple[str, ...] | None = None


def upgrade() -> None:
    op.execute("ALTER TABLE contracts ADD COLUMN expiring_notified_at TIMESTAMPTZ")


def downgrade() -> None:
    op.execute("ALTER TABLE contracts DROP COLUMN IF EXISTS expiring_notified_at")
