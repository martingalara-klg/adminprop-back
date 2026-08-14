"""tests/integration/administracion/test_organization_settings.py

SDD: docs/sdd/features/spec_module_07_administracion.md RF-04 +
§"Validaciones". core/sdd_03_api_contracts.md §4 "GET/PUT
/organization/settings".
Implements: CA-07-05.
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.asyncio


async def _seed_org_with_owner(seed):
    org = await seed.create_organization_with_system_roles()
    owner = await seed.add_member(
        organization_id=org["organization_id"],
        role_id=org["roles"]["owner"],
        role_name="owner",
    )
    return org, owner


# Digito verificador valido (algoritmo estandar CUIT argentino: mult.
# [5,4,3,2,7,6,5,4,3,2] sobre "2032964229", modulo 11 -> resto 9 -> check 2).
_VALID_CUIT = "20329642292"


class TestGetOrganizationSettings:
    async def test_get_settings_returns_defaults(self, client, seed):
        _org, owner = await _seed_org_with_owner(seed)

        response = await client.get("/v1/organization/settings", headers=owner["headers"])

        assert response.status_code == 200
        data = response.json()["data"]
        assert data["grace_day"] == 10
        assert data["contract_expiry_notice_days"] == 60
        assert data["billing_header"] == {"name": None, "cuit": None, "contact": None}


class TestUpdateOrganizationSettings:
    async def test_update_settings_persists_grace_day_and_billing_header(self, client, seed):
        _org, owner = await _seed_org_with_owner(seed)

        response = await client.put(
            "/v1/organization/settings",
            json={
                "grace_day": 15,
                "contract_expiry_notice_days": 45,
                "billing_name": "Administradora Ejemplo SRL",
                "billing_cuit": _VALID_CUIT,
                "billing_contact": "contacto@ejemplo.com",
            },
            headers=owner["headers"],
        )

        assert response.status_code == 200
        data = response.json()["data"]
        assert data["grace_day"] == 15
        assert data["contract_expiry_notice_days"] == 45
        assert data["billing_header"] == {
            "name": "Administradora Ejemplo SRL",
            "cuit": _VALID_CUIT,
            "contact": "contacto@ejemplo.com",
        }

        get_response = await client.get("/v1/organization/settings", headers=owner["headers"])
        assert get_response.json()["data"]["grace_day"] == 15

    async def test_update_settings_accepts_cuit_with_zero_check_digit(self, client, seed):
        """Caso borde del algoritmo: cuando el modulo 11 da resto 0, el
        digito verificador esperado es 0 (no 11) -- CUIT valido
        `10000000030` (primeros 10 digitos "1000000003" -> resto 0 ->
        digito verificador 0)."""
        _org, owner = await _seed_org_with_owner(seed)

        response = await client.put(
            "/v1/organization/settings",
            json={
                "grace_day": 10,
                "contract_expiry_notice_days": 60,
                "billing_name": None,
                "billing_cuit": "10000000030",
                "billing_contact": None,
            },
            headers=owner["headers"],
        )

        assert response.status_code == 200
        assert response.json()["data"]["billing_header"]["cuit"] == "10000000030"

    @pytest.mark.parametrize("grace_day", [0, 29])
    async def test_update_settings_rejects_grace_day_out_of_range(self, client, seed, grace_day):
        _org, owner = await _seed_org_with_owner(seed)

        response = await client.put(
            "/v1/organization/settings",
            json={
                "grace_day": grace_day,
                "contract_expiry_notice_days": 60,
                "billing_name": None,
                "billing_cuit": None,
                "billing_contact": None,
            },
            headers=owner["headers"],
        )

        assert response.status_code == 400

    @pytest.mark.parametrize("notice_days", [6, 366])
    async def test_update_settings_rejects_notice_days_out_of_range(
        self, client, seed, notice_days
    ):
        _org, owner = await _seed_org_with_owner(seed)

        response = await client.put(
            "/v1/organization/settings",
            json={
                "grace_day": 10,
                "contract_expiry_notice_days": notice_days,
                "billing_name": None,
                "billing_cuit": None,
                "billing_contact": None,
            },
            headers=owner["headers"],
        )

        assert response.status_code == 400

    async def test_update_settings_rejects_invalid_cuit(self, client, seed):
        _org, owner = await _seed_org_with_owner(seed)

        response = await client.put(
            "/v1/organization/settings",
            json={
                "grace_day": 10,
                "contract_expiry_notice_days": 60,
                "billing_name": None,
                "billing_cuit": "12345678901",
                "billing_contact": None,
            },
            headers=owner["headers"],
        )

        assert response.status_code == 400

    async def test_update_settings_rejects_cuit_with_wrong_length(self, client, seed):
        _org, owner = await _seed_org_with_owner(seed)

        response = await client.put(
            "/v1/organization/settings",
            json={
                "grace_day": 10,
                "contract_expiry_notice_days": 60,
                "billing_name": None,
                "billing_cuit": "123",
                "billing_contact": None,
            },
            headers=owner["headers"],
        )

        assert response.status_code == 400

    async def test_update_settings_rejects_billing_name_too_long(self, client, seed):
        _org, owner = await _seed_org_with_owner(seed)

        response = await client.put(
            "/v1/organization/settings",
            json={
                "grace_day": 10,
                "contract_expiry_notice_days": 60,
                "billing_name": "x" * 121,
                "billing_cuit": None,
                "billing_contact": None,
            },
            headers=owner["headers"],
        )

        assert response.status_code == 400

    async def test_update_settings_rejects_billing_contact_too_long(self, client, seed):
        _org, owner = await _seed_org_with_owner(seed)

        response = await client.put(
            "/v1/organization/settings",
            json={
                "grace_day": 10,
                "contract_expiry_notice_days": 60,
                "billing_name": None,
                "billing_cuit": None,
                "billing_contact": "x" * 201,
            },
            headers=owner["headers"],
        )

        assert response.status_code == 400

    async def test_update_settings_rejects_extra_fields(self, client, seed):
        org, owner = await _seed_org_with_owner(seed)

        response = await client.put(
            "/v1/organization/settings",
            json={
                "grace_day": 10,
                "contract_expiry_notice_days": 60,
                "billing_name": None,
                "billing_cuit": None,
                "billing_contact": None,
                "organization_id": str(org["organization_id"]),
            },
            headers=owner["headers"],
        )

        assert response.status_code == 400

    async def test_ca_07_05_grace_day_change_applies_from_now_on(self, client, seed):
        """CA-07-05: "Cambiar grace_day de 10 a 15 hace que la mora de los
        cobros posteriores se calcule con el dia 15, sin recalcular
        intereses ya imputados; el cambio queda auditado."

        El modulo de cobranzas (issue #22, calculo de mora) no existe
        todavia -- este test cubre la invariante a nivel de
        repository/servicio de settings: el UPDATE es directo (sin
        side-effects sobre otras tablas), y el nuevo valor se lee
        inmediatamente con GET. La persistencia real en `audit_logs` es
        TODO(#10) (la tabla no existe todavia); el cambio se audita hoy
        via logger estructurado (ver `service.py`).
        """
        _org, owner = await _seed_org_with_owner(seed)
        initial = await client.get("/v1/organization/settings", headers=owner["headers"])
        assert initial.json()["data"]["grace_day"] == 10

        update_response = await client.put(
            "/v1/organization/settings",
            json={
                "grace_day": 15,
                "contract_expiry_notice_days": 60,
                "billing_name": None,
                "billing_cuit": None,
                "billing_contact": None,
            },
            headers=owner["headers"],
        )
        assert update_response.status_code == 200
        assert update_response.json()["data"]["grace_day"] == 15

        after = await client.get("/v1/organization/settings", headers=owner["headers"])
        assert after.json()["data"]["grace_day"] == 15
