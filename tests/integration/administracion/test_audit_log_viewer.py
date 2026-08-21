"""tests/integration/administracion/test_audit_log_viewer.py

SDD: docs/sdd/features/spec_module_07_administracion.md §RF-05 +
core/sdd_03_api_contracts.md §16 "Audit Logs" (page/page_size, filtros
entity_type/entity_id/user_id/action/date range) +
core/sdd_02_domain_model.md §2.17 "Log de Auditoria (AuditLog)".
Implements: CA-07-06 (issue #32).

Los eventos usados como fixture son eventos REALES generados por el
`AuditService` transversal a traves de los endpoints de escritura ya
existentes del modulo (`user.role_changed`, `user.deactivated`,
`settings.changed`, issue #10) -- no se fabrican filas de `audit_logs` a
mano, la unica via de escritura es `shared/audit/service.py.audit()`
(RN-D03, append-only).
"""

from __future__ import annotations

import uuid

import pytest

pytestmark = pytest.mark.asyncio


async def _seed_org_with_owner_and_admin(seed):
    org = await seed.create_organization_with_system_roles()
    owner = await seed.add_member(
        organization_id=org["organization_id"],
        role_id=org["roles"]["owner"],
        role_name="owner",
    )
    admin = await seed.add_member(
        organization_id=org["organization_id"],
        role_id=org["roles"]["admin"],
        role_name="admin",
    )
    return org, owner, admin


class TestCA0706FilterByEntityAndUser:
    """CA-07-06: "El visor de auditoria filtra por entidad y usuario,
    pagina con page/page_size, y muestra valores anterior/nuevo de cada
    cambio."""

    async def test_ca_07_06_filters_by_entity_type_and_entity_id(self, client, seed):
        _org, owner, admin = await _seed_org_with_owner_and_admin(seed)

        role_change = await client.patch(
            f"/v1/users/{admin['id']}",
            json={"role": "maintenance"},
            headers=owner["headers"],
        )
        assert role_change.status_code == 200

        settings_change = await client.put(
            "/v1/organization/settings",
            json={
                "grace_day": 20,
                "contract_expiry_notice_days": 90,
                "billing_name": "Acme",
                "billing_cuit": None,
                "billing_contact": None,
            },
            headers=owner["headers"],
        )
        assert settings_change.status_code == 200

        response = await client.get(
            "/v1/audit-logs",
            params={"entity_type": "organization_member", "entity_id": str(admin["id"])},
            headers=owner["headers"],
        )

        assert response.status_code == 200
        body = response.json()
        assert len(body["data"]) == 1
        entry = body["data"][0]
        assert entry["entity_type"] == "organization_member"
        assert entry["entity_id"] == str(admin["id"])
        assert entry["action"] == "user.role_changed"

    async def test_ca_07_06_filters_by_user_id(self, client, seed):
        _org, owner, admin = await _seed_org_with_owner_and_admin(seed)

        await client.patch(
            f"/v1/users/{admin['id']}",
            json={"role": "maintenance"},
            headers=owner["headers"],
        )
        await client.put(
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

        response = await client.get(
            "/v1/audit-logs",
            params={"user_id": str(owner["id"])},
            headers=owner["headers"],
        )

        assert response.status_code == 200
        body = response.json()
        assert len(body["data"]) == 2
        assert all(item["user_id"] == str(owner["id"]) for item in body["data"])

    async def test_ca_07_06_shows_before_and_after_state(self, client, seed):
        _org, owner, admin = await _seed_org_with_owner_and_admin(seed)

        await client.patch(
            f"/v1/users/{admin['id']}",
            json={"role": "maintenance"},
            headers=owner["headers"],
        )

        response = await client.get(
            "/v1/audit-logs",
            params={"action": "user.role_changed"},
            headers=owner["headers"],
        )

        assert response.status_code == 200
        entry = response.json()["data"][0]
        assert entry["before_state"] == {"role": "admin"}
        assert entry["after_state"] == {"role": "maintenance"}
        assert entry["request_id"] is not None
        assert entry["user_id"] == str(owner["id"])

    async def test_ca_07_06_paginates_with_page_and_page_size(self, client, seed):
        _org, owner, _admin = await _seed_org_with_owner_and_admin(seed)

        # Genera 3 eventos "settings.changed" reales (uno por PUT distinto).
        for grace_day in (11, 12, 13):
            resp = await client.put(
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
            assert resp.status_code == 200

        page_1 = await client.get(
            "/v1/audit-logs",
            params={"action": "settings.changed", "page": 1, "page_size": 2},
            headers=owner["headers"],
        )
        assert page_1.status_code == 200
        body_1 = page_1.json()
        assert len(body_1["data"]) == 2
        assert body_1["meta"] == {"page": 1, "page_size": 2, "total": 3}

        page_2 = await client.get(
            "/v1/audit-logs",
            params={"action": "settings.changed", "page": 2, "page_size": 2},
            headers=owner["headers"],
        )
        assert page_2.status_code == 200
        body_2 = page_2.json()
        assert len(body_2["data"]) == 1
        assert body_2["meta"] == {"page": 2, "page_size": 2, "total": 3}

        page_1_ids = {item["id"] for item in body_1["data"]}
        page_2_ids = {item["id"] for item in body_2["data"]}
        assert page_1_ids.isdisjoint(page_2_ids)

    async def test_list_audit_logs_default_page_size_is_50(self, client, seed):
        """sdd_03 §16: default 50 -- se verifica via meta.page_size sin
        necesidad de sembrar 50 filas."""
        _org, owner, _admin = await _seed_org_with_owner_and_admin(seed)

        response = await client.get("/v1/audit-logs", headers=owner["headers"])

        assert response.status_code == 200
        assert response.json()["meta"]["page_size"] == 50

    async def test_list_audit_logs_page_size_above_100_returns_validation_error(
        self, client, seed
    ):
        """sdd_03 §16: maximo 100 -- Pydantic/FastAPI rechaza con
        400 VALIDATION_ERROR (sdd_03 §"Codigos de Error Globales") antes
        de tocar el repository."""
        _org, owner, _admin = await _seed_org_with_owner_and_admin(seed)

        response = await client.get(
            "/v1/audit-logs", params={"page_size": 101}, headers=owner["headers"]
        )

        assert response.status_code == 400
        assert response.json()["error"]["code"] == "VALIDATION_ERROR"

    async def test_get_audit_log_by_id_returns_full_entry(self, client, seed):
        _org, owner, admin = await _seed_org_with_owner_and_admin(seed)

        await client.patch(
            f"/v1/users/{admin['id']}",
            json={"role": "maintenance"},
            headers=owner["headers"],
        )
        listed = await client.get(
            "/v1/audit-logs",
            params={"action": "user.role_changed"},
            headers=owner["headers"],
        )
        audit_log_id = listed.json()["data"][0]["id"]

        response = await client.get(f"/v1/audit-logs/{audit_log_id}", headers=owner["headers"])

        assert response.status_code == 200
        entry = response.json()["data"]
        assert entry["id"] == audit_log_id
        assert entry["action"] == "user.role_changed"


class TestCA0704AdminReadsAuditMaintenanceForbidden:
    """CA-07-04: "Un admin recibe 403 FORBIDDEN al intentar invitar
    usuarios o cambiar la configuracion; puede leer el log de
    auditoria."""

    async def test_admin_can_list_audit_logs(self, client, seed):
        _org, owner, admin = await _seed_org_with_owner_and_admin(seed)
        await client.put(
            "/v1/organization/settings",
            json={
                "grace_day": 12,
                "contract_expiry_notice_days": 60,
                "billing_name": None,
                "billing_cuit": None,
                "billing_contact": None,
            },
            headers=owner["headers"],
        )

        response = await client.get("/v1/audit-logs", headers=admin["headers"])

        assert response.status_code == 200

    async def test_maintenance_cannot_list_audit_logs_returns_403(self, client, seed):
        org = await seed.create_organization_with_system_roles()
        maintenance = await seed.add_member(
            organization_id=org["organization_id"],
            role_id=org["roles"]["maintenance"],
            role_name="maintenance",
        )

        response = await client.get("/v1/audit-logs", headers=maintenance["headers"])

        assert response.status_code == 403
        assert response.json()["error"]["code"] == "FORBIDDEN"

    async def test_maintenance_cannot_get_audit_log_detail_returns_403(self, client, seed):
        org = await seed.create_organization_with_system_roles()
        maintenance = await seed.add_member(
            organization_id=org["organization_id"],
            role_id=org["roles"]["maintenance"],
            role_name="maintenance",
        )

        response = await client.get(
            f"/v1/audit-logs/{uuid.uuid4()}", headers=maintenance["headers"]
        )

        assert response.status_code == 403
        assert response.json()["error"]["code"] == "FORBIDDEN"
