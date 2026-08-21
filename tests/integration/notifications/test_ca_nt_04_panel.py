"""tests/integration/notifications/test_ca_nt_04_panel.py -- issue #31.

SDD: infrastructure/spec_notificaciones.md RF-02 + core/sdd_03_api_contracts.md
     §13 "Notificaciones".
Implements: CA-NT-04: "El badge muestra las no leídas del usuario;
            `read-all` las marca todas y el badge queda en cero."

Ejercicio via HTTP real (`GET /v1/notifications`, `POST
/v1/notifications/:id/read`, `POST /v1/notifications/read-all`) --
`test_ca_nt_01_event_routing.py` y `test_emit_and_outbox.py` ya cubren la
emision (`emit()`); esta suite siembra filas directamente
(`seed.insert_notification_row`) y ejerce el panel de lectura.
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.asyncio


async def _seed_owner_with_notifications(seed, *, unread: int, read: int):
    org = await seed.create_org_with_roles()
    owner = await seed.add_member(
        organization_id=org["organization_id"],
        role_id=org["roles"]["owner"],
        role_name="owner",
        permissions=["notification:read"],
    )
    for _ in range(unread):
        await seed.insert_notification_row(
            organization_id=org["organization_id"], user_id=owner["id"]
        )
    for _ in range(read):
        await seed.insert_notification_row(
            organization_id=org["organization_id"], user_id=owner["id"], read=True
        )
    return org, owner


class TestCaNt04Badge:
    async def test_ca_nt_04_badge_shows_unread_count_of_the_current_user(self, client, seed):
        _org, owner = await _seed_owner_with_notifications(seed, unread=3, read=2)

        response = await client.get("/v1/notifications", headers=owner["headers"])

        assert response.status_code == 200
        body = response.json()
        assert body["meta"]["unread_count"] == 3
        assert len(body["data"]) == 5

    async def test_ca_nt_04_unread_query_param_filters_only_unread(self, client, seed):
        _org, owner = await _seed_owner_with_notifications(seed, unread=3, read=2)

        response = await client.get("/v1/notifications?unread=true", headers=owner["headers"])

        assert response.status_code == 200
        body = response.json()
        assert len(body["data"]) == 3
        assert all(item["read_at"] is None for item in body["data"])
        assert body["meta"]["unread_count"] == 3

    async def test_ca_nt_04_maintenance_role_can_read_its_own_notifications(self, client, seed):
        """sdd_03 §"Resumen de Autorizacion por Recurso": "Notificaciones
        propias" -- maintenance tiene acceso total (issue #31 agrego
        `notification:read` a `MAINTENANCE_PERMISSIONS`)."""
        org = await seed.create_org_with_roles()
        maintenance_user = await seed.add_member(
            organization_id=org["organization_id"],
            role_id=org["roles"]["maintenance"],
            role_name="maintenance",
            permissions=["notification:read"],
        )
        await seed.insert_notification_row(
            organization_id=org["organization_id"],
            user_id=maintenance_user["id"],
            event_type="quote_approved",
        )

        response = await client.get("/v1/notifications", headers=maintenance_user["headers"])

        assert response.status_code == 200
        assert response.json()["meta"]["unread_count"] == 1

    async def test_notification_read_without_permission_returns_403(self, client, seed):
        org = await seed.create_org_with_roles()
        no_perm_user = await seed.add_member(
            organization_id=org["organization_id"],
            role_id=org["roles"]["owner"],
            role_name="owner",
            permissions=[],
        )

        response = await client.get("/v1/notifications", headers=no_perm_user["headers"])

        assert response.status_code == 403


class TestCaNt04MarkRead:
    async def test_ca_nt_04_mark_read_removes_it_from_unread_count(self, client, seed):
        org = await seed.create_org_with_roles()
        owner = await seed.add_member(
            organization_id=org["organization_id"],
            role_id=org["roles"]["owner"],
            role_name="owner",
            permissions=["notification:read"],
        )
        notification_id = await seed.insert_notification_row(
            organization_id=org["organization_id"], user_id=owner["id"]
        )

        response = await client.post(
            f"/v1/notifications/{notification_id}/read", headers=owner["headers"]
        )

        assert response.status_code == 200
        assert response.json()["data"]["read_at"] is not None

        listing = await client.get("/v1/notifications", headers=owner["headers"])
        assert listing.json()["meta"]["unread_count"] == 0

    async def test_mark_read_is_idempotent_on_an_already_read_notification(self, client, seed):
        org = await seed.create_org_with_roles()
        owner = await seed.add_member(
            organization_id=org["organization_id"],
            role_id=org["roles"]["owner"],
            role_name="owner",
            permissions=["notification:read"],
        )
        notification_id = await seed.insert_notification_row(
            organization_id=org["organization_id"], user_id=owner["id"], read=True
        )

        response = await client.post(
            f"/v1/notifications/{notification_id}/read", headers=owner["headers"]
        )

        assert response.status_code == 200

    async def test_mark_read_unknown_notification_returns_404(self, client, seed):
        import uuid

        org = await seed.create_org_with_roles()
        owner = await seed.add_member(
            organization_id=org["organization_id"],
            role_id=org["roles"]["owner"],
            role_name="owner",
            permissions=["notification:read"],
        )

        response = await client.post(
            f"/v1/notifications/{uuid.uuid4()}/read", headers=owner["headers"]
        )

        assert response.status_code == 404
        assert response.json()["error"]["code"] == "NOT_FOUND"

    async def test_mark_read_of_another_users_notification_in_same_org_returns_404(
        self, client, seed
    ):
        """RN-D01 aplicado a nivel de fila propia: una notificacion es de
        UN destinatario -- otro miembro de la misma organizacion no puede
        marcarla como leida."""
        org = await seed.create_org_with_roles()
        owner = await seed.add_member(
            organization_id=org["organization_id"],
            role_id=org["roles"]["owner"],
            role_name="owner",
            permissions=["notification:read"],
        )
        admin = await seed.add_member(
            organization_id=org["organization_id"],
            role_id=org["roles"]["admin"],
            role_name="admin",
            permissions=["notification:read"],
        )
        notification_id = await seed.insert_notification_row(
            organization_id=org["organization_id"], user_id=owner["id"]
        )

        response = await client.post(
            f"/v1/notifications/{notification_id}/read", headers=admin["headers"]
        )

        assert response.status_code == 404


class TestCaNt04ReadAll:
    async def test_ca_nt_04_read_all_marks_every_unread_and_badge_goes_to_zero(self, client, seed):
        org = await seed.create_org_with_roles()
        owner = await seed.add_member(
            organization_id=org["organization_id"],
            role_id=org["roles"]["owner"],
            role_name="owner",
            permissions=["notification:read"],
        )
        for _ in range(4):
            await seed.insert_notification_row(
                organization_id=org["organization_id"], user_id=owner["id"]
            )

        response = await client.post("/v1/notifications/read-all", headers=owner["headers"])

        assert response.status_code == 200
        assert response.json()["data"]["marked"] == 4

        listing = await client.get("/v1/notifications", headers=owner["headers"])
        assert listing.json()["meta"]["unread_count"] == 0
        assert all(item["read_at"] is not None for item in listing.json()["data"])

    async def test_read_all_does_not_affect_other_users_unread_notifications(self, client, seed):
        org = await seed.create_org_with_roles()
        owner = await seed.add_member(
            organization_id=org["organization_id"],
            role_id=org["roles"]["owner"],
            role_name="owner",
            permissions=["notification:read"],
        )
        admin = await seed.add_member(
            organization_id=org["organization_id"],
            role_id=org["roles"]["admin"],
            role_name="admin",
            permissions=["notification:read"],
        )
        await seed.insert_notification_row(
            organization_id=org["organization_id"], user_id=owner["id"]
        )
        await seed.insert_notification_row(
            organization_id=org["organization_id"], user_id=admin["id"]
        )

        response = await client.post("/v1/notifications/read-all", headers=owner["headers"])
        assert response.status_code == 200
        assert response.json()["data"]["marked"] == 1

        admin_listing = await client.get("/v1/notifications", headers=admin["headers"])
        assert admin_listing.json()["meta"]["unread_count"] == 1


class TestCaNt04TenantIsolation:
    """RN-D01: el panel HTTP nunca expone notificaciones de otra organizacion."""

    async def test_list_notifications_never_returns_other_tenant_rows(self, client, seed):
        org_a = await seed.create_org_with_roles()
        org_b = await seed.create_org_with_roles()
        owner_a = await seed.add_member(
            organization_id=org_a["organization_id"],
            role_id=org_a["roles"]["owner"],
            role_name="owner",
            permissions=["notification:read"],
        )
        owner_b = await seed.add_member(
            organization_id=org_b["organization_id"],
            role_id=org_b["roles"]["owner"],
            role_name="owner",
            permissions=["notification:read"],
        )
        await seed.insert_notification_row(
            organization_id=org_a["organization_id"], user_id=owner_a["id"]
        )
        await seed.insert_notification_row(
            organization_id=org_b["organization_id"], user_id=owner_b["id"]
        )

        response = await client.get("/v1/notifications", headers=owner_a["headers"])

        assert response.status_code == 200
        assert response.json()["meta"]["unread_count"] == 1

    async def test_mark_read_of_other_tenant_notification_returns_404(self, client, seed):
        org_a = await seed.create_org_with_roles()
        org_b = await seed.create_org_with_roles()
        owner_a = await seed.add_member(
            organization_id=org_a["organization_id"],
            role_id=org_a["roles"]["owner"],
            role_name="owner",
            permissions=["notification:read"],
        )
        owner_b = await seed.add_member(
            organization_id=org_b["organization_id"],
            role_id=org_b["roles"]["owner"],
            role_name="owner",
            permissions=["notification:read"],
        )
        notification_id_b = await seed.insert_notification_row(
            organization_id=org_b["organization_id"], user_id=owner_b["id"]
        )

        response = await client.post(
            f"/v1/notifications/{notification_id_b}/read", headers=owner_a["headers"]
        )

        assert response.status_code == 404
        assert response.json()["error"]["code"] == "NOT_FOUND"
