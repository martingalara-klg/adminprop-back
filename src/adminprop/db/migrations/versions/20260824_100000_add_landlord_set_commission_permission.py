"""add_landlord_set_commission_permission — permiso atomico dedicado al
cambio de `commission_pct` (issue #51)

SDD: core/sdd_03_api_contracts.md v1.5 §"Catalogo de Permisos" (decision
     #116: `landlord:set-commission` agregado, exclusivo de `owner`) +
     infrastructure/spec_data_model.md §"Estrategia de Seed Data".
Implements: CA-R50-02 (seed de roles + migracion de datos para orgs
            existentes).

El PR #50 (issue #13) restringia el cambio de `commission_pct` comparando
`payload.role != "owner"` en `LandlordService.update` porque el catalogo
de permisos no tenia un permiso atomico dedicado (owner y admin comparian
`landlord:manage`). Esta migracion es puramente de DATOS -- no toca el
schema de `roles` (la columna `permissions` ya es JSONB desde
`20260812_212704_create_capa0_fundacion.py`) -- y agrega
`landlord:set-commission` al array `permissions` del rol `owner` de TODA
organizacion YA EXISTENTE, para que el refactor del service (que ahora
exige el permiso via `requires_permission`) no deje a los owners actuales
sin poder seguir cambiando el % de comision de sus propietarios.

El seed de organizaciones NUEVAS (via `OrganizationProvisioningService` /
`SuperAdminRepository.create_organization_with_roles`) ya incluye el
permiso nuevo automaticamente porque `ROLE_DEFINITIONS`
(`modules/superadmin/provisioning.py`) lo agrega a `OWNER_PERMISSIONS`
(= `ALL_PERMISSIONS`, que ya lo lista) -- esta migracion solo cubre el
INSERT ya hecho de organizaciones anteriores a este commit.

Idempotente via `permissions @> '["landlord:set-commission"]'::jsonb`
(no re-agrega si ya esta, ej: re-ejecucion de `alembic upgrade head` o
organizaciones creadas entre el deploy del codigo y el de esta migracion).
"""

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "20260824_100000"
down_revision: str | None = "20260823_090000"
branch_labels: tuple[str, ...] | None = None
depends_on: tuple[str, ...] | None = None

_PERMISSION = "landlord:set-commission"
_OWNER_ROLE_NAME = "owner"


def upgrade() -> None:
    # RN-A (accesos): "solo owner gestiona ... comision" -- se agrega el
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
    # `20260821_100000_add_quote_approved_to_notifications.py`, pero sin
    # perdida de datos aca: ningun `landlord` deja de existir por esto).
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
