"""tests/unit/modules/people/test_schemas.py

SDD: docs/sdd/features/spec_module_02_personas.md §"Validaciones".
Unit tests puros (sin DB) de los validators de Pydantic del modulo.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from adminprop.modules.people.schemas import LandlordCreate, LandlordUpdate, RenterCreate


class TestTaxIdValidation:
    """§"Validaciones": "CUIT de 11 digitos con digito verificador valido,
    o DNI de 7-8 digitos (campo flexible, validacion por formato
    detectado)"."""

    def test_valid_cuit_with_correct_check_digit_is_accepted(self):
        landlord = LandlordCreate(name="Juan Perez", tax_id="20-12345678-6", commission_pct="10")
        assert landlord.tax_id == "20123456786"

    def test_cuit_with_wrong_check_digit_is_rejected(self):
        with pytest.raises(ValidationError):
            LandlordCreate(name="Juan Perez", tax_id="20329145294", commission_pct="10")

    def test_valid_dni_of_8_digits_is_accepted(self):
        renter = RenterCreate(name="Maria Lopez", tax_id="12345678")
        assert renter.tax_id == "12345678"

    def test_valid_dni_of_7_digits_is_accepted(self):
        renter = RenterCreate(name="Maria Lopez", tax_id="1234567")
        assert renter.tax_id == "1234567"

    def test_tax_id_of_invalid_length_is_rejected(self):
        with pytest.raises(ValidationError):
            RenterCreate(name="Maria Lopez", tax_id="123")

    def test_tax_id_is_optional(self):
        renter = RenterCreate(name="Maria Lopez")
        assert renter.tax_id is None


class TestCommissionPctValidation:
    """§"Validaciones": "commission_pct: decimal 0-100, hasta 2 decimales"."""

    def test_commission_pct_within_range_is_accepted(self):
        landlord = LandlordCreate(name="Juan Perez", commission_pct="8.50")
        assert landlord.commission_pct == pytest.approx(8.50)

    def test_commission_pct_below_zero_is_rejected(self):
        with pytest.raises(ValidationError):
            LandlordCreate(name="Juan Perez", commission_pct="-1")

    def test_commission_pct_above_100_is_rejected(self):
        with pytest.raises(ValidationError):
            LandlordCreate(name="Juan Perez", commission_pct="100.01")

    def test_commission_pct_with_more_than_2_decimals_is_rejected(self):
        with pytest.raises(ValidationError):
            LandlordCreate(name="Juan Perez", commission_pct="10.123")

    def test_commission_pct_is_required_on_create(self):
        with pytest.raises(ValidationError):
            LandlordCreate(name="Juan Perez")

    def test_commission_pct_is_optional_on_update(self):
        update = LandlordUpdate(phone="351-0000000")
        assert update.commission_pct is None
        assert "commission_pct" not in update.model_fields_set


class TestEmailValidation:
    def test_invalid_email_is_rejected(self):
        with pytest.raises(ValidationError):
            RenterCreate(name="Maria Lopez", email="not-an-email")

    def test_email_is_lowercased(self):
        renter = RenterCreate(name="Maria Lopez", email="MARIA@Example.com")
        assert renter.email == "maria@example.com"
