"""Unit tests: hashing de passwords con bcrypt cost 12 (issue #6).

SDD: core/sdd_04_nonfunctional.md parrafo 2.2.
"""

from adminprop.shared.auth.passwords import BCRYPT_ROUNDS, hash_password, verify_password


class TestPasswordHashing:
    def test_hash_password_uses_bcrypt_cost_12(self):
        """sdd_04 §2.2: "Passwords: bcrypt cost 12."."""
        hashed = hash_password("Password1234")
        assert hashed.startswith("$2b$12$")
        assert BCRYPT_ROUNDS == 12

    def test_verify_password_returns_true_for_matching_password(self):
        hashed = hash_password("Password1234")
        assert verify_password("Password1234", hashed) is True

    def test_verify_password_returns_false_for_wrong_password(self):
        hashed = hash_password("Password1234")
        assert verify_password("WrongPassword1", hashed) is False

    def test_verify_password_returns_false_when_hash_is_none(self):
        """Usuario inexistente: no debe levantar excepcion (anti-enumeration)."""
        assert verify_password("Password1234", None) is False

    def test_verify_password_returns_false_for_malformed_hash(self):
        assert verify_password("Password1234", "not-a-valid-bcrypt-hash") is False

    def test_verify_password_timing_is_constant_for_none_and_wrong_hash(self):
        """sdd_04 §2.1 "Enumeracion de usuarios": no debe haber diferencia
        de comportamiento observable entre email inexistente (hash=None) y
        password incorrecta (hash real, password mal) -- ambas rutas
        ejecutan un bcrypt.checkpw real.
        """
        hashed = hash_password("Password1234")
        assert verify_password("WrongPassword1", hashed) is False
        assert verify_password("WrongPassword1", None) is False
