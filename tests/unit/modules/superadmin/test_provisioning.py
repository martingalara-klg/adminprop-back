"""tests/unit/modules/superadmin/test_provisioning.py

SDD: infrastructure/spec_data_model.md §"Estrategia de Seed Data"
     + core/spec_module_00_superadmin.md RF-02.
"""

from adminprop.modules.superadmin.provisioning import (
    ADMIN_PERMISSIONS,
    ALL_PERMISSIONS,
    DEFAULT_ORGANIZATION_SETTINGS,
    MAINTENANCE_PERMISSIONS,
    OWNER_PERMISSIONS,
    ROLE_DEFINITIONS,
    slugify,
)


class TestSlugify:
    """RF-02: slug kebab-case, `^[a-z0-9-]+$`."""

    def test_kebab_cases_a_simple_name(self):
        assert slugify("Acme Propiedades") == "acme-propiedades"

    def test_collapses_consecutive_invalid_characters_into_one_hyphen(self):
        assert slugify("  Café & Co.  ") == "caf-co"

    def test_collapses_repeated_separators(self):
        assert slugify("A---B   C") == "a-b-c"

    def test_empty_or_fully_invalid_name_falls_back_to_org(self):
        assert slugify("...") == "org"

    def test_result_matches_slug_charset(self):
        import re

        assert re.fullmatch(r"[a-z0-9-]+", slugify("N4me With Nums 123!!")) is not None


class TestRoleProvisioningCatalog:
    """spec_data_model.md §"Estrategia de Seed Data": 3 roles de sistema."""

    def test_seeds_exactly_three_system_roles_in_stable_order(self):
        assert [name for name, _ in ROLE_DEFINITIONS] == ["owner", "admin", "maintenance"]

    def test_owner_has_every_permission_in_the_catalog(self):
        assert set(OWNER_PERMISSIONS) == set(ALL_PERMISSIONS)

    def test_admin_excludes_user_management_role_read_and_org_config(self):
        excluded = {"user:manage", "role:read", "organization:configure"}
        assert set(ADMIN_PERMISSIONS) == set(ALL_PERMISSIONS) - excluded
        assert excluded.isdisjoint(ADMIN_PERMISSIONS)

    def test_maintenance_is_scoped_to_work_orders_attachments_and_own_notifications(self):
        """Issue #31: agrega `notification:read` -- sdd_03 §"Resumen de
        Autorizacion por Recurso" fila "Notificaciones propias" otorga
        acceso completo a los 3 roles (owner/admin/maintenance)."""
        assert set(MAINTENANCE_PERMISSIONS) == {
            "work-order:read",
            "work-order:quote",
            "work-order:close",
            "attachment:manage",
            "notification:read",
        }
        # RN-A01: maintenance nunca contract:*/payment:*/settlement:*.
        forbidden = {"contract:manage", "payment:create", "settlement:generate"}
        assert forbidden.isdisjoint(MAINTENANCE_PERMISSIONS)

    def test_default_settings_match_spec_data_model(self):
        assert DEFAULT_ORGANIZATION_SETTINGS == {
            "grace_day": 10,
            "contract_expiry_notice_days": 60,
        }
