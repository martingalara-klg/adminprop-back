"""add_contract_terminate_permission — permiso atomico dedicado a
terminar un contrato (issue #105)

SDD: core/sdd_03_api_contracts.md v1.11 §"Catalogo de Permisos" (decision
     #124: `contract:terminate` agregado, exclusivo de `owner`) +
     infrastructure/spec_data_model.md §"Estrategia de Seed Data".
Implements: CA-R124-02 (seed de roles + migracion de datos para orgs
            existentes).

Feedback #2 del PO (2026-08-28): terminar un contrato debe poder hacerlo
SOLO el owner -- hasta ahora `contract:manage` se lo permitia tambien al
admin (mismo permiso que crear/actualizar/activar). Esta migracion es
puramente de DATOS -- no toca el schema de `roles` (la columna
`permissions` ya es JSONB desde `20260812_212704_create_capa0_fundacion.py`)
-- y agrega `contract:terminate` al array `permissions` del rol `owner`
de TODA organizacion YA EXISTENTE, para que el endpoint (que ahora exige
el permiso via `requires_permission("contract:terminate")` en vez de
`contract:manage`) no deje a los owners actuales sin poder seguir
terminando contratos.

El seed de organizaciones NUEVAS (via `OrganizationProvisioningService` /
`SuperAdminRepository.create_organization_with_roles`) ya incluye el
permiso nuevo automaticamente porque `ROLE_DEFINITIONS`
(`modules/superadmin/provisioning.py`) lo agrega a `OWNER_PERMISSIONS`
(= `ALL_PERMISSIONS`, que ya lo lista) -- esta migracion solo cubre el
INSERT ya hecho de organizaciones anteriores a este commit.

Idempotente via `permissions @> '["contract:terminate"]'::jsonb` (no
re-agrega si ya esta, ej: re-ejecucion de `alembic upgrade head` u
organizaciones creadas entre el deploy del codigo y el de esta migracion)
-- mismo patron que `20260824_100000_add_landlord_set_commission_permission.py`
(issue #51, decision #116).
"""

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "20260828_130000"
down_revision: str | None = "20260828_123003"
branch_labels: tuple[str, ...] | None = None
depends_on: tuple[str, ...] | None = None

_PERMISSION = "contract:terminate"
_OWNER_ROLE_NAME = "owner"


def upgrade() -> None:
    # RN-A (accesos): "solo owner ... termina contratos" -- se agrega el
    # permiso nuevo al array existente sin tocar el resto (`permissions ||
    # '[...]'::jsonb` concatena arrays JSONB), solo en filas que todavia
    # no lo tienen (idempotente).
    op.execute(
        f"""
        UPDATE roles
        SET permissions = permissions || '["{_PERMISSION}"]'::jsonb,
            updated_at = now()
        WHERE name = '{_OWNER_ROLE_NAME}'
          AND NOT (permissions @> '["{_PERMISSION}"]'::jsonb)
        """
    )


def downgrade() -> None:
    # Quita unicamente el elemento agregado -- preserva el resto del
    # array `permissions` de cada rol `owner` tal cual estaba (mismo
    # criterio de reversion dirigida que
    # `20260824_100000_add_landlord_set_commission_permission.py`, sin
    # perdida de datos aca: ningun `role` deja de existir por esto).
    op.execute(
        f"""
        UPDATE roles
        SET permissions = (
                SELECT COALESCE(jsonb_agg(elem), '[]'::jsonb)
                FROM jsonb_array_elements(permissions) AS elem
                WHERE elem <> '"{_PERMISSION}"'::jsonb
            ),
            updated_at = now()
        WHERE name = '{_OWNER_ROLE_NAME}'
          AND permissions @> '["{_PERMISSION}"]'::jsonb
        """
    )
