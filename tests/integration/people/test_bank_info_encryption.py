"""tests/integration/people/test_bank_info_encryption.py

SDD: core/sdd_04_nonfunctional.md §2.4 "Cifrado y CSRF" +
docs/sdd/features/spec_module_02_personas.md RF-01.
Implements: CA-02-04.
"""

from __future__ import annotations

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


class TestCA0204BankInfoEncryptedAtRest:
    """CA-02-04: `bank_info` se persiste cifrado (verificable a nivel DB)
    y jamas aparece en logs ni en respuestas de listado (solo en el
    detalle para owner/admin)."""

    async def test_ca_02_04_bank_info_ciphertext_never_equals_plaintext_in_db(self, client, seed):
        plaintext = "CBU 2850590940090418135201 - Banco Nacion"
        _org, owner, _admin = await _seed_org_with_owner_and_admin(seed)

        created = await client.post(
            "/v1/landlords",
            json={"name": "Con Banco", "commission_pct": "10", "bank_info": plaintext},
            headers=owner["headers"],
        )
        landlord_id = created.json()["data"]["id"]

        raw = await seed.raw_bank_info(landlord_id)
        assert raw is not None
        assert isinstance(raw, (bytes, bytearray))
        assert plaintext.encode("utf-8") not in bytes(raw)

    async def test_ca_02_04_bank_info_is_decrypted_in_detail_response(self, client, seed):
        plaintext = "Alias: juan.perez.mp"
        _org, owner, admin = await _seed_org_with_owner_and_admin(seed)

        created = await client.post(
            "/v1/landlords",
            json={"name": "Con Alias", "commission_pct": "10", "bank_info": plaintext},
            headers=owner["headers"],
        )
        landlord_id = created.json()["data"]["id"]

        owner_detail = await client.get(f"/v1/landlords/{landlord_id}", headers=owner["headers"])
        assert owner_detail.json()["data"]["bank_info"] == plaintext

        admin_detail = await client.get(f"/v1/landlords/{landlord_id}", headers=admin["headers"])
        assert admin_detail.json()["data"]["bank_info"] == plaintext

    async def test_ca_02_04_bank_info_never_appears_in_list_response(self, client, seed):
        plaintext = "CBU secreto que no debe listarse"
        _org, owner, _admin = await _seed_org_with_owner_and_admin(seed)

        await client.post(
            "/v1/landlords",
            json={"name": "En Listado", "commission_pct": "10", "bank_info": plaintext},
            headers=owner["headers"],
        )

        response = await client.get("/v1/landlords", headers=owner["headers"])

        assert response.status_code == 200
        body_text = response.text
        assert "bank_info" not in body_text
        assert plaintext not in body_text
