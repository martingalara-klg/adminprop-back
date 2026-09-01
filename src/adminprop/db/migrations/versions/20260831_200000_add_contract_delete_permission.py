"""add_contract_delete_permission — permiso atomico dedicado a eliminar
(logicamente) un contrato (issue #124)

SDD: core/sdd_03_api_contracts.md v1.17 §"Catalogo de Permisos" (decision
     #130: `contract:delete` agregado, exclusivo de `owner`) +
     infrastructure/spec_data_model.md v1.5 §"Estrategia de Seed Data".
Implements: CA-03-36 (seed de roles + migracion de datos para orgs
            existentes).
Rule: RN-C08 (sdd_02 v1.9) / RN-13 (spec_module_03 v1.7).

Feedback #4 del PO (2026-08-31): eliminar un contrato — borrado LOGICO,
en CUALQUIER estado, incluso `active` — debe poder hacerlo SOLO el owner.
Esta migracion es puramente de DATOS — no toca el schema (`roles.permissions`
ya es JSONB desde `20260812_212704_create_capa0_fundacion.py`; la columna
`contracts.deleted_at` ya existe desde `20260815_110000_create_capa3_contratos.py`)
— y agrega `contract:delete` al array `permissions` del rol `owner` de
TODA organizacion YA EXISTENTE. Mismo patron que
`20260828_130000_add_contract_terminate_permission.py` (issue #105,
decision #124).

Leccion del issue #116 (bug de permisos doblemente codificados): el
UPDATE solo toca filas cuyo `permissions` es REALMENTE un array JSONB
(`jsonb_typeof(permissions) = 'array'`) — concatenar `|| '[...]'::jsonb`
sobre un escalar string produciria el array mixto que el issue #116 tuvo
que reparar. Tras `20260829_090000_normalize_double_encoded_json_columns.py`
todas las filas ya son arrays; la guarda es la red de seguridad exigida
por la decision #130.

El seed de organizaciones NUEVAS (`OrganizationProvisioningService` /
`ROLE_DEFINITIONS` en `modules/superadmin/provisioning.py`) ya incluye el
permiso porque `OWNER_PERMISSIONS = ALL_PERMISSIONS` lo lista — esta
migracion solo cubre el INSERT ya hecho de organizaciones anteriores.

Idempotente via `NOT (permissions @> '["contract:delete"]'::jsonb)`.
"""

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "20260831_200000"
down_revision: str | None = "20260829_100000"
branch_labels: tuple[str, ...] | None = None
depends_on: tuple[str, ...] | None = None

_PERMISSION = "contract:delete"
_OWNER_ROLE_NAME = "owner"


def upgrade() -> None:
    # RN-C08: "solo owner ... elimina contratos" — se agrega el permiso
    # nuevo al array existente sin tocar el resto, solo en filas que
    # todavia no lo tienen (idempotente) y cuyo `permissions` es un array
    # JSONB real (leccion del issue #116: nunca concatenar sobre un
    # escalar string doblemente codificado).
    op.execute(
        f"""
        UPDATE roles
        SET permissions = permissions || '["{_PERMISSION}"]'::jsonb,
            updated_at = now()
        WHERE name = '{_OWNER_ROLE_NAME}'
          AND jsonb_typeof(permissions) = 'array'
          AND NOT (permissions @> '["{_PERMISSION}"]'::jsonb)
        """
    )


def downgrade() -> None:
    # Quita unicamente el elemento agregado — preserva el resto del array
    # `permissions` de cada rol `owner` tal cual estaba (mismo criterio de
    # reversion dirigida que `20260828_130000_add_contract_terminate_permission.py`).
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
          AND jsonb_typeof(permissions) = 'array'
          AND permissions @> '["{_PERMISSION}"]'::jsonb
        """
    )
