<%doc>
Template usado por `alembic revision -m "<slug>"` para generar migraciones
nuevas. docs/skills/database-migration.md: no usar --autogenerate (RLS,
CHECK e indices parciales no se autogeneran bien) — escribir el SQL con
op.execute a mano en el archivo generado a partir de este template.
</%doc>
"""${message}

SDD: <completar ruta del SDD + seccion>
Implements: <RN-XX o decision #N>
"""
from alembic import op
import sqlalchemy as sa
${imports if imports else ""}

# revision identifiers, used by Alembic.
revision: str = ${repr(up_revision)}
down_revision: str | None = ${repr(down_revision)}
branch_labels: tuple[str, ...] | None = ${repr(branch_labels)}
depends_on: tuple[str, ...] | None = ${repr(depends_on)}


def upgrade() -> None:
    ${upgrades if upgrades else "pass"}


def downgrade() -> None:
    ${downgrades if downgrades else "pass"}
