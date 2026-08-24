"""tests/unit/modules/superadmin/test_schemas.py

Validacion pura de `OrganizationUpdate` (issue #44), sin I/O: al menos
un campo presente, `timezone` IANA valida, `extra="forbid"`.
SDD: core/sdd_03_api_contracts.md §2.
"""

import pytest
from pydantic import ValidationError

from adminprop.modules.superadmin.schemas import OrganizationUpdate


class TestOrganizationUpdateAtLeastOneField:
    def test_accepts_name_only(self):
        dto = OrganizationUpdate(name="Nueva Org")
        assert dto.name == "Nueva Org"
        assert dto.timezone is None

    def test_accepts_timezone_only(self):
        dto = OrganizationUpdate(timezone="America/New_York")
        assert dto.timezone == "America/New_York"
        assert dto.name is None

    def test_accepts_both_fields(self):
        dto = OrganizationUpdate(name="Nueva Org", timezone="Europe/Madrid")
        assert dto.name == "Nueva Org"
        assert dto.timezone == "Europe/Madrid"

    def test_rejects_empty_body(self):
        with pytest.raises(ValidationError):
            OrganizationUpdate()


class TestOrganizationUpdateTimezoneValidation:
    def test_accepts_valid_iana_timezone(self):
        dto = OrganizationUpdate(timezone="America/Argentina/Cordoba")
        assert dto.timezone == "America/Argentina/Cordoba"

    def test_rejects_unknown_timezone(self):
        with pytest.raises(ValidationError) as exc_info:
            OrganizationUpdate(timezone="No/Existe")
        assert "timezone" in str(exc_info.value)


class TestOrganizationUpdateForbidsExtraFields:
    def test_rejects_slug(self):
        with pytest.raises(ValidationError):
            OrganizationUpdate(name="Nueva Org", slug="otro-slug")

    def test_rejects_status(self):
        with pytest.raises(ValidationError):
            OrganizationUpdate(name="Nueva Org", status="disabled")

    def test_rejects_settings(self):
        with pytest.raises(ValidationError):
            OrganizationUpdate(name="Nueva Org", settings={"grace_day": 5})
