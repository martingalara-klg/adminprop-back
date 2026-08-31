"""Unit tests de helpers puros del repository de auth (issue #6, issue #116)."""

import pytest

from adminprop.modules.auth.repository import _parse_permissions
from adminprop.shared.errors.codes import InternalError


class TestParsePermissions:
    def test_returns_list_of_str_when_input_is_already_a_list(self):
        assert _parse_permissions(["contract:read", "contract:manage"]) == [
            "contract:read",
            "contract:manage",
        ]

    def test_parses_json_string_into_list(self):
        """Forma que asyncpg entrega para `roles.permissions` correctamente
        codificado (issue #6): la columna JSONB llega ya decodificada un
        nivel por el driver -- este `json.loads` final la convierte de
        texto de array a lista real."""
        assert _parse_permissions('["contract:read"]') == ["contract:read"]

    def test_ca_116_01_flattens_the_double_encoded_scalar_string_shape(self):
        """Issue #116, causa raiz: `roles.permissions` doblemente
        codificado (INSERT con `bindparam(type_=sa.JSON)` + `json.dumps()`
        sobre un valor ya serializado) llega como el string JSON del array
        real -- verificado empiricamente en Docker (jsonb_typeof='string').
        `_parse_permissions` debe seguir desenredando esta forma (unico
        motivo por el que el bug "funciono por accidente" hasta el issue
        #105 -- ver docstring de la funcion)."""
        raw = '["landlord:read", "landlord:manage", "contract:manage"]'
        assert _parse_permissions(raw) == [
            "landlord:read",
            "landlord:manage",
            "contract:manage",
        ]

    def test_ca_116_02_flattens_the_post_105_mixed_array_shape(self):
        """Issue #116, evidencia real de Railway: la migracion `permissions
        || '["contract:terminate"]'::jsonb` del issue #105 concateno un
        permiso plano sobre un valor ya doble-codificado, dejando un array
        MIXTO `[<string JSON del array original>, "contract:terminate"]`
        (`login` lo devolvia asi de roto -- ver body del issue). Debe
        aplanarse a una lista plana de strings simples."""
        raw = [
            '["landlord:read", "landlord:manage", "contract:manage"]',
            "contract:terminate",
        ]
        assert _parse_permissions(raw) == [
            "landlord:read",
            "landlord:manage",
            "contract:manage",
            "contract:terminate",
        ]

    def test_ca_116_03_dedupes_while_preserving_first_seen_order(self):
        raw = [
            '["landlord:read", "landlord:manage"]',
            "landlord:read",
            "contract:manage",
        ]
        assert _parse_permissions(raw) == [
            "landlord:read",
            "landlord:manage",
            "contract:manage",
        ]

    def test_ca_116_04_correctly_encoded_array_passes_through_unchanged(self):
        """Forma correcta post-fix (issue #116): array de strings simples,
        ninguno de los cuales es a su vez JSON de un array -- no-op."""
        assert _parse_permissions(["contract:manage", "contract:read"]) == [
            "contract:manage",
            "contract:read",
        ]

    def test_ca_116_05_raises_internal_error_instead_of_returning_garbage(self):
        """Issue #116 Trabajo §4: "ante forma inesperada, loguear error y
        fallar ruidosamente (no devolver basura)" -- reemplaza el
        comportamiento anterior de devolver `[]` silenciosamente."""
        with pytest.raises(InternalError):
            _parse_permissions(None)
        with pytest.raises(InternalError):
            _parse_permissions(42)
        with pytest.raises(InternalError):
            _parse_permissions("not-valid-json")
        with pytest.raises(InternalError):
            _parse_permissions([123, "contract:manage"])
