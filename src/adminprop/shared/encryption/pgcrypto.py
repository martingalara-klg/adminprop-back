"""Helper de cifrado/descifrado columnar via pgcrypto (AES-256).

SDD: core/sdd_04_nonfunctional.md §2.4 "Cifrado y CSRF" — cifrado
columnar (pgcrypto AES-256) para `landlords.bank_info` (datos bancarios
de terceros). La clave vive en la variable de entorno local
`ENCRYPTION_KEY` en MVP (ver `.env.example`, `Settings.encryption_key`);
migra a un gestor de secretos cuando exista infra cloud (CLAUDE.md §3/§8).

Este modulo NO implementa AES en Python: delega en las funciones SQL
`pgp_sym_encrypt`/`pgp_sym_decrypt` de la extension `pgcrypto` (ya
creada por `20260812_114322_setup_extensions_and_roles.py` y reafirmada
idempotentemente por `20260815_090000_create_capa1_personas.py`), que
es la implementacion AES-256 que `sdd_04 §2.4` exige. El texto plano
nunca se persiste ni pasa por una libreria de cifrado adicional en el
proceso Python — solo viaja, cifrado o en claro, dentro de la misma
conexion TLS a la base de datos.

El caller (repository de `landlords`, issue #13) pasa una `AsyncSession`
ya abierta — mismo criterio que `shared/audit/service.py`: estas
funciones no abren ni comitean su propia transaccion.
"""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from adminprop.config import get_settings

# pgcrypto soporta varios cifrados simetricos via pgp_sym_encrypt; se fija
# explicitamente AES-256 (sdd_04 §2.4), en vez de confiar en el default del
# algoritmo (que en pgcrypto es 'aes128').
_CIPHER_ALGO = "cipher-algo=aes256"


async def encrypt_value(session: AsyncSession, plaintext: str) -> bytes:
    """Cifra `plaintext` con AES-256 (pgcrypto `pgp_sym_encrypt`).

    Devuelve el ciphertext listo para persistir en una columna `BYTEA`
    (ej: `landlords.bank_info`). La clave se resuelve de
    `Settings.encryption_key`, nunca hardcodeada ni logueada.
    """
    settings = get_settings()
    result = await session.execute(
        text(f"SELECT pgp_sym_encrypt(:plaintext, :key, '{_CIPHER_ALGO}')"),
        {"plaintext": plaintext, "key": settings.encryption_key},
    )
    return result.scalar_one()


async def decrypt_value(session: AsyncSession, ciphertext: bytes) -> str:
    """Descifra un valor `BYTEA` previamente cifrado con `encrypt_value`.

    Misma clave (`Settings.encryption_key`) usada para cifrar — si la
    clave no coincide, `pgp_sym_decrypt` falla a nivel de PostgreSQL
    (el error queda propagado como `sqlalchemy.exc.DBAPIError`, nunca se
    silencia).
    """
    settings = get_settings()
    result = await session.execute(
        text(f"SELECT pgp_sym_decrypt(:ciphertext, :key, '{_CIPHER_ALGO}')"),
        {"ciphertext": ciphertext, "key": settings.encryption_key},
    )
    return result.scalar_one()
