"""Hashing de passwords con bcrypt cost 12 (issue #6).

SDD: core/sdd_04_nonfunctional.md §2.2 ("Passwords: bcrypt cost 12.").
La politica de longitud/complejidad (>= 10 caracteres, mayuscula, numero)
es responsabilidad de los flujos que *crean* un password (accept-invitation,
reset-password -- issue #8, fuera de alcance de login/logout/refresh) y se
implementa junto con esos endpoints para no dejar codigo sin consumidor
real en este issue (YAGNI).
"""

from __future__ import annotations

import bcrypt

BCRYPT_ROUNDS = 12

# Hash dummy fijo (no corresponde a ningun password real) usado para
# normalizar el tiempo de respuesta cuando el email no existe -- evita que
# un atacante distinga "email inexistente" de "password incorrecta" por
# timing (sdd_04 §2.1 "Enumeracion de usuarios").
_DUMMY_HASH = bcrypt.hashpw(b"dummy-password-for-timing", bcrypt.gensalt(rounds=BCRYPT_ROUNDS))


def hash_password(plain_password: str) -> str:
    """Hashea `plain_password` con bcrypt cost 12. Retorna el hash como str utf-8."""
    salt = bcrypt.gensalt(rounds=BCRYPT_ROUNDS)
    return bcrypt.hashpw(plain_password.encode("utf-8"), salt).decode("utf-8")


def verify_password(plain_password: str, password_hash: str | None) -> bool:
    """Verifica `plain_password` contra `password_hash`. Nunca levanta excepcion.

    `password_hash=None` (usuario inexistente) igual ejecuta un bcrypt.checkpw
    contra un hash dummy para mantener el tiempo de respuesta constante
    (mitigacion de timing attack, sdd_04 §2.1).
    """
    target = password_hash.encode("utf-8") if password_hash is not None else _DUMMY_HASH
    try:
        result = bcrypt.checkpw(plain_password.encode("utf-8"), target)
    except ValueError:
        return False
    return result and password_hash is not None
