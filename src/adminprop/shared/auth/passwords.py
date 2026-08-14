"""Hashing de passwords con bcrypt cost 12 (issue #6).

SDD: core/sdd_04_nonfunctional.md §2.2 ("Passwords: bcrypt cost 12.").
La politica de longitud/complejidad (>= 10 caracteres, mayuscula, numero)
es responsabilidad de los flujos que *crean* un password (accept-invitation,
reset-password -- issue #8, fuera de alcance de login/logout/refresh) y se
implementa junto con esos endpoints para no dejar codigo sin consumidor
real en este issue (YAGNI).
"""

from __future__ import annotations

import re

import bcrypt

BCRYPT_ROUNDS = 12

# sdd_04 §2.2: ">= 10 caracteres, >= 1 mayuscula, >= 1 numero" (issue #8:
# accept-invitation y reset-password son los dos flujos que crean un
# password, ver docstring del modulo arriba).
_PASSWORD_MIN_LENGTH = 10
_UPPERCASE_RE = re.compile(r"[A-Z]")
_DIGIT_RE = re.compile(r"[0-9]")

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


def validate_password_policy(password: str) -> None:
    """sdd_04 §2.2: ">= 10 caracteres, >= 1 mayuscula, >= 1 numero".

    Compartida por `AcceptInvitationRequest` y `ResetPasswordRequest`
    (issue #8, modules/auth/schemas.py) via `@field_validator` -- levanta
    `ValueError`, que Pydantic mapea a `RequestValidationError` ->
    `VALIDATION_ERROR` (400) via el handler global
    (shared/errors/handlers.py), sin necesidad de un `error.code` nuevo.
    """
    if len(password) < _PASSWORD_MIN_LENGTH:
        raise ValueError(f"La contrasena debe tener al menos {_PASSWORD_MIN_LENGTH} caracteres.")
    if not _UPPERCASE_RE.search(password):
        raise ValueError("La contrasena debe tener al menos una mayuscula.")
    if not _DIGIT_RE.search(password):
        raise ValueError("La contrasena debe tener al menos un numero.")
