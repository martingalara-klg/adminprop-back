"""tests/integration/superadmin/test_dashboard_listing.py

SDD: core/spec_module_00_superadmin.md RF-01 "Dashboard de Organizaciones"
     + core/sdd_03_api_contracts.md §"Paginacion" (cursor-based).
"""

import uuid

import pytest

pytestmark = pytest.mark.asyncio


def _unique_name(base: str) -> str:
    return f"{base} {uuid.uuid4().hex[:8]}"


async def _create_org(client, super_admin_headers, name: str) -> dict:
    response = await client.post(
        "/v1/superadmin/organizations", json={"name": name}, headers=super_admin_headers
    )
    return response.json()["data"]


class TestRF01DashboardFilters:
    """RF-01: filtros por status y busqueda por nombre/slug."""

    async def test_filters_by_status(self, client, super_admin_headers):
        pending = await _create_org(
            client, super_admin_headers, _unique_name("Org Pending Filter")
        )
        disabled_org = await _create_org(
            client, super_admin_headers, _unique_name("Org Disabled Filter")
        )
        await client.post(
            f"/v1/superadmin/organizations/{disabled_org['id']}/disable",
            json={"reason": "para probar el filtro de status"},
            headers=super_admin_headers,
        )

        response = await client.get(
            "/v1/superadmin/organizations",
            params={"status": "disabled"},
            headers=super_admin_headers,
        )

        assert response.status_code == 200
        ids = {item["id"] for item in response.json()["data"]}
        assert disabled_org["id"] in ids
        assert pending["id"] not in ids

    async def test_search_matches_by_name_substring(self, client, super_admin_headers):
        unique_token = uuid.uuid4().hex[:10]
        org = await _create_org(client, super_admin_headers, f"Buscable {unique_token} SRL")

        response = await client.get(
            "/v1/superadmin/organizations",
            params={"search": unique_token},
            headers=super_admin_headers,
        )

        assert response.status_code == 200
        ids = {item["id"] for item in response.json()["data"]}
        assert org["id"] in ids

    async def test_search_matches_by_slug_substring(self, client, super_admin_headers):
        unique_token = uuid.uuid4().hex[:10]
        org = await _create_org(client, super_admin_headers, f"Slug Match {unique_token}")

        response = await client.get(
            "/v1/superadmin/organizations",
            params={"search": unique_token},
            headers=super_admin_headers,
        )

        assert response.status_code == 200
        ids = {item["id"] for item in response.json()["data"]}
        assert org["id"] in ids


class TestRF01DashboardPagination:
    """sdd_03 §"Paginacion": cursor-based, `meta.next_cursor`."""

    async def test_cursor_pagination_returns_all_items_without_duplicates(
        self, client, super_admin_headers
    ):
        unique_token = uuid.uuid4().hex[:10]
        created_ids = {
            (await _create_org(client, super_admin_headers, f"Page {unique_token} {i}"))["id"]
            for i in range(3)
        }

        collected: set[str] = set()
        cursor = None
        for _ in range(10):  # limite de seguridad ante un bug de loop infinito
            params = {"search": unique_token, "limit": 1}
            if cursor:
                params["cursor"] = cursor
            response = await client.get(
                "/v1/superadmin/organizations", headers=super_admin_headers, params=params
            )
            assert response.status_code == 200
            body = response.json()
            collected.update(item["id"] for item in body["data"])
            cursor = body["meta"]["next_cursor"]
            if cursor is None:
                break

        assert created_ids <= collected
