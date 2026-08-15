"""Issue #12 — `shared/encryption/pgcrypto.py`: cifrado columnar AES-256.

Requiere Postgres real (usa `pgp_sym_encrypt`/`pgp_sym_decrypt` de la
extension `pgcrypto`, ya creada por las migraciones de Capa 0/Capa 1) —
mismo criterio que el resto de `tests/integration/db/*`.

SDD: core/sdd_04_nonfunctional.md §2.4 "Cifrado y CSRF"
Implements: CA-12-01 (cifrado de bank_info verificable a nivel de DB:
            el valor crudo de la columna nunca es texto plano legible)
"""

from __future__ import annotations

import uuid

import pytest
import sqlalchemy as sa

from adminprop.db.session import get_session_factory
from adminprop.shared.encryption.pgcrypto import decrypt_value, encrypt_value

pytestmark = pytest.mark.asyncio


async def test_ca_12_01_encrypt_then_decrypt_roundtrips_to_original_plaintext():
    plaintext = "CBU 0000003100000000000000 - Banco Nacion - Titular: Juan Perez"
    session_factory = get_session_factory()
    async with session_factory() as session:
        ciphertext = await encrypt_value(session, plaintext)
        recovered = await decrypt_value(session, ciphertext)

    assert recovered == plaintext


async def test_ca_12_01_ciphertext_is_not_plaintext_readable():
    """El ciphertext no contiene el texto plano en ninguna forma trivial
    (ni siquiera como substring de sus bytes crudos)."""
    plaintext = "CBU 0000003100000000000000 secreto-bancario-unico"
    session_factory = get_session_factory()
    async with session_factory() as session:
        ciphertext = await encrypt_value(session, plaintext)

    assert isinstance(ciphertext, (bytes, bytearray))
    assert plaintext.encode("utf-8") not in bytes(ciphertext)


async def test_ca_12_01_landlords_bank_info_es_ilegible_consultando_la_columna_cruda():
    """CA-12-01 end-to-end: se inserta un landlord con `bank_info` cifrado
    via `encrypt_value` y se verifica, con una query SQL cruda sobre la
    columna (sin pasar por `decrypt_value`), que el valor persistido no es
    el texto plano — "verificable a nivel de base de datos" (issue #12)."""
    plaintext = "Banco Galicia - CBU 0070999530004025640001 - Titular: Maria Lopez"
    organization_id = uuid.uuid4()
    session_factory = get_session_factory()

    async with session_factory() as session, session.begin():
        await session.execute(
            sa.text("INSERT INTO organizations (id, slug, name) VALUES (:id, :slug, :name)"),
            {
                "id": str(organization_id),
                "slug": f"org-{organization_id.hex[:8]}",
                "name": "Org cifrado",
            },
        )
        ciphertext = await encrypt_value(session, plaintext)
        await session.execute(
            sa.text(
                "INSERT INTO landlords (organization_id, name, commission_pct, bank_info) "
                "VALUES (:org_id, 'Landlord cifrado', 10, :bank_info)"
            ),
            {"org_id": str(organization_id), "bank_info": ciphertext},
        )

    async with session_factory() as session:
        result = await session.execute(
            sa.text("SELECT bank_info FROM landlords WHERE organization_id = :org_id"),
            {"org_id": str(organization_id)},
        )
        raw_value = result.scalar_one()

    assert bytes(raw_value) != plaintext.encode("utf-8")
    assert plaintext.encode("utf-8") not in bytes(raw_value)

    async with session_factory() as session:
        recovered = await decrypt_value(session, raw_value)
    assert recovered == plaintext

    async with session_factory() as session, session.begin():
        await session.execute(
            sa.text("DELETE FROM landlords WHERE organization_id = :org_id"),
            {"org_id": str(organization_id)},
        )
        await session.execute(
            sa.text("DELETE FROM organizations WHERE id = :org_id"),
            {"org_id": str(organization_id)},
        )


async def test_ca_12_01_decrypt_with_wrong_key_fails_loudly(monkeypatch):
    """Si la clave no coincide, `pgp_sym_decrypt` falla (nunca devuelve
    silenciosamente basura ni el texto plano de otra clave)."""
    from adminprop.config import get_settings

    plaintext = "dato bancario sensible"
    session_factory = get_session_factory()
    async with session_factory() as session:
        ciphertext = await encrypt_value(session, plaintext)

    get_settings.cache_clear()
    monkeypatch.setenv("ENCRYPTION_KEY", "una-clave-completamente-distinta")
    get_settings.cache_clear()
    try:
        async with session_factory() as session:
            with pytest.raises(sa.exc.DBAPIError):
                await decrypt_value(session, ciphertext)
    finally:
        get_settings.cache_clear()
