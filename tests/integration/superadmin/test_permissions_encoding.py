"""tests/integration/superadmin/test_permissions_encoding.py

Issue #116 [BUG CRITICO]: `roles.permissions` (y `organizations.settings`)
se guardaban doblemente codificados -- `INSERT` con
`bindparam(..., type_=sa.JSON)` (que YA serializa el valor Python a JSON)
recibiendo ademas un valor pre-serializado con `json.dumps()`. La columna
JSONB quedaba con un escalar string (el JSON del array/objeto real)
en vez del array/objeto real -- ver
`modules/superadmin/repository.py.create_organization_with_roles`.

SDD: infrastructure/spec_data_model.md §Capa 0 "roles"/"organizations"
     + core/sdd_03_api_contracts.md §"Resumen de Autorizacion por Recurso"
     ("el chequeo es siempre por permiso atomico ... nunca por
     `role_name`" -- solo es posible si `permissions` es realmente un
     array de strings, no un escalar string).
"""

import uuid

import pytest
import sqlalchemy as sa

from adminprop.db.session import get_session_factory
from adminprop.modules.administracion.repository import AdministracionRepository

pytestmark = pytest.mark.asyncio


def _unique_name(base: str) -> str:
    return f"{base} {uuid.uuid4().hex[:8]}"


class TestCA116RolesPermissionsIsAGenuineJsonbArray:
    """CA-116-01: tras provisionar una organizacion (RF-02, mismo endpoint
    que `test_organization_creation.py`), `roles.permissions` de cada uno
    de los 3 roles de sistema es un array JSONB real -- no un escalar
    string con el array serializado adentro (issue #116 causa raiz)."""

    async def test_ca_116_01_every_seeded_role_permissions_jsonb_typeof_is_array(
        self, client, super_admin_headers
    ):
        response = await client.post(
            "/v1/superadmin/organizations",
            json={"name": _unique_name("Encoding Check Org")},
            headers=super_admin_headers,
        )
        org_id = response.json()["data"]["id"]

        session_factory = get_session_factory()
        async with session_factory() as session:
            await session.execute(sa.text("SET LOCAL ROLE adminprop_superadmin"))
            result = await session.execute(
                sa.text(
                    "SELECT name, jsonb_typeof(permissions) AS typeof FROM roles "
                    "WHERE organization_id = :org_id"
                ),
                {"org_id": org_id},
            )
            typeof_by_role = {row.name: row.typeof for row in result}

        assert typeof_by_role == {"owner": "array", "admin": "array", "maintenance": "array"}

    async def test_ca_116_01_organization_settings_jsonb_typeof_is_object(
        self, client, super_admin_headers
    ):
        response = await client.post(
            "/v1/superadmin/organizations",
            json={"name": _unique_name("Settings Encoding Check Org")},
            headers=super_admin_headers,
        )
        org_id = response.json()["data"]["id"]

        session_factory = get_session_factory()
        async with session_factory() as session:
            await session.execute(sa.text("SET LOCAL ROLE adminprop_superadmin"))
            result = await session.execute(
                sa.text("SELECT jsonb_typeof(settings) FROM organizations WHERE id = :id"),
                {"id": org_id},
            )
            typeof = result.scalar_one()

        assert typeof == "object"

    async def test_ca_116_01_owner_permissions_are_genuinely_a_list_not_a_json_string(
        self, client, super_admin_headers, db_roles
    ):
        """Antes del fix: `db_roles` (helper de test que hace UN
        `json.loads` sobre la lectura cruda de `permissions`) devolvia el
        STRING con el array serializado adentro para `roles["owner"]`
        (no una lista) -- las aserciones `"x" in roles["owner"]` de
        `test_organization_creation.py` pasaban por matching de
        subcadena sobre ese string, no porque `permissions` fuera
        realmente una lista (la causa exacta por la que el bug "funciono
        por accidente", segun el issue #116). Este test verifica el TIPO
        real ademas del contenido."""
        response = await client.post(
            "/v1/superadmin/organizations",
            json={"name": _unique_name("Owner List Type Check Org")},
            headers=super_admin_headers,
        )
        org_id = response.json()["data"]["id"]

        roles = {role["name"]: role["permissions"] for role in await db_roles(org_id)}

        assert isinstance(roles["owner"], list)
        assert all(isinstance(item, str) for item in roles["owner"])
        assert "landlord:set-commission" in roles["owner"]
        assert "user:manage" in roles["owner"]
        assert "user:manage" not in roles["admin"]


class TestCA116OrganizationSettingsUpdateIsNotDoubleEncoded:
    """CA-116-01: `AdministracionRepository.update_organization_settings`
    (`modules/administracion/repository.py`) tenia el mismo patron de
    doble-codificacion que el seed de organizaciones."""

    async def test_ca_116_01_update_organization_settings_stores_a_genuine_jsonb_object(
        self, client, super_admin_headers
    ):
        response = await client.post(
            "/v1/superadmin/organizations",
            json={"name": _unique_name("Administracion Settings Encoding Org")},
            headers=super_admin_headers,
        )
        org_id = response.json()["data"]["id"]

        session_factory = get_session_factory()
        async with session_factory() as session, session.begin():
            await session.execute(sa.text("SET LOCAL ROLE adminprop_superadmin"))
            repo = AdministracionRepository(session)
            await repo.update_organization_settings(org_id, {"grace_day": 15})

        async with session_factory() as session:
            await session.execute(sa.text("SET LOCAL ROLE adminprop_superadmin"))
            result = await session.execute(
                sa.text(
                    "SELECT settings, jsonb_typeof(settings) FROM organizations WHERE id = :id"
                ),
                {"id": org_id},
            )
            settings, typeof = result.one()

        assert typeof == "object"
        assert settings == {"grace_day": 15}
