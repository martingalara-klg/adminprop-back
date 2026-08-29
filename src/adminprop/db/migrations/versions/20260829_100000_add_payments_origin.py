"""add_payments_origin — marca de origen del cobro (issue #119)

SDD: infrastructure/spec_data_model.md v1.4 §Capa 4 "payments" +
     core/sdd_02_domain_model.md v1.8 §2.10 (RN-P09) + §3 RN-P09.
Implements: CA-04-13/14/15 (issue #119).

Feedback #3 del PO (2026-08-29): al dar de alta un contrato en curso
(`start_date` anterior al mes actual), el backend genera automaticamente
un `Payment` por cada mes ya transcurrido -- estos cobros NO son un cobro
real ocurrido ante la administradora, sino un registro historico de lo
que ya se cobro fuera del sistema antes del alta. Se necesita distinguir
estos cobros de los registrados manualmente por un operador para
excluirlos de liquidaciones (RN-L02/RN-06 de `spec_module_05`), de
recibos (RF-07) y de anulacion (RF-05) -- reusar `destination =
landlord_account` ("ya rendido") NO alcanza porque ese destino SI integra
la base de comision (RN-P07/RN-L02), y estos cobros de carga inicial no
deben aportar comision alguna.

Columna nueva: `payments.origin TEXT NOT NULL DEFAULT 'manual' CHECK
(origin IN ('manual', 'initial_load'))`. Todo cobro existente (registrado
antes de esta migracion) es `manual` por definicion -- el DEFAULT cubre
el backfill sin necesitar un UPDATE explicito.

Alcance estrictamente de migracion (mismo criterio que los issues
#16/#20): no se agrega logica de negocio aca -- el service.py del modulo
`contracts` (generacion) y el `_PAYMENTS_SQL` del modulo `settlements`
(exclusion) son PRs de codigo aparte, dentro del mismo commit de esta
tarea pero en su propia capa.
"""

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "20260829_100000"
down_revision: str | None = "20260829_090000"
branch_labels: tuple[str, ...] | None = None
depends_on: tuple[str, ...] | None = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE payments
        ADD COLUMN origin TEXT NOT NULL DEFAULT 'manual'
            CHECK (origin IN ('manual', 'initial_load'))
        """
    )


def downgrade() -> None:
    op.execute("ALTER TABLE payments DROP COLUMN IF EXISTS origin")
