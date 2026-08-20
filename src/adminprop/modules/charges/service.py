"""Logica de negocio de `charges`: ABM de conceptos recurrentes + carga
mensual + vista de verificacion (issue #28).

SDD: docs/sdd/features/spec_module_05_liquidaciones.md §RF-05.
Implements: CA-05-08 (verificacion mensual + duplicado 409), RN-D01
(aislamiento multi-tenant, 404 no 403), RN-D04 (correccion de
`charge_entries` siempre trazada en el log de auditoria).
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import UUID

from fastapi import Depends

from adminprop.modules.charges.repository import (
    ChargeEntryRepository,
    ChargeVerificationRow,
    RecurringChargeRepository,
    get_charge_entry_repository,
    get_recurring_charge_repository,
)
from adminprop.shared.audit.service import audit
from adminprop.shared.errors.codes import (
    ChargeEntryAlreadyExistsException,
    NotFoundException,
    ValidationError,
)


def _period_not_future(period: date, *, today: date) -> bool:
    """RF-05 §Validaciones: "period: mes valido no futuro"."""
    current_month = date(today.year, today.month, 1)
    return period <= current_month


class RecurringChargeService:
    """RF-05 §1: ABM de conceptos recurrentes por propiedad."""

    def __init__(self, repo: RecurringChargeRepository) -> None:
        self._repo = repo

    async def create(
        self,
        *,
        property_id: UUID,
        organization_id: UUID,
        charge_type: str,
        label: str,
    ):
        """RF-05: alta de un concepto -- `property_id` validado contra el
        mismo tenant (RN-D01, 404 si no existe/es de otra org)."""
        if not await self._repo.property_exists(property_id, organization_id):
            raise NotFoundException(message="La propiedad indicada no existe.", field="property_id")

        row = await self._repo.create(
            organization_id=organization_id,
            property_id=property_id,
            charge_type=charge_type,
            label=label,
        )
        await self._repo.commit()
        return row

    async def list_by_property(self, property_id: UUID, organization_id: UUID) -> list:
        """RF-05: "todos los conceptos de la propiedad" (activos e
        inactivos) -- 404 si la propiedad no existe/es de otro tenant
        (RN-D01)."""
        if not await self._repo.property_exists(property_id, organization_id):
            raise NotFoundException()
        return await self._repo.list_by_property(property_id, organization_id)

    async def update(
        self,
        recurring_charge_id: UUID,
        organization_id: UUID,
        *,
        label: str | None,
        is_active: bool | None,
        fields_set: set[str],
    ):
        """sdd_03 §10: `PATCH /recurring-charges/:id (label, is_active)`."""
        existing = await self._repo.get_by_id(recurring_charge_id, organization_id)
        if existing is None:
            raise NotFoundException()

        update_fields: dict[str, object] = {}
        if "label" in fields_set:
            update_fields["label"] = label
        if "is_active" in fields_set:
            update_fields["is_active"] = is_active

        updated = await self._repo.update(
            recurring_charge_id, organization_id, fields=update_fields
        )
        if updated is None:  # pragma: no cover -- defensivo, ya se valido existencia arriba
            raise NotFoundException()

        await self._repo.commit()
        return updated


def get_recurring_charge_service(
    repo: RecurringChargeRepository = Depends(get_recurring_charge_repository),
) -> RecurringChargeService:
    return RecurringChargeService(repo)


class ChargeEntryService:
    """RF-05 §2/§3: carga mensual (`POST .../entries`), correccion
    auditada (`PATCH /charge-entries/:id`) y vista de verificacion
    (`GET /charge-entries?period=`, CA-05-08)."""

    def __init__(
        self,
        entry_repo: ChargeEntryRepository,
        charge_repo: RecurringChargeRepository,
    ) -> None:
        self._entry_repo = entry_repo
        self._charge_repo = charge_repo

    async def create_entry(
        self,
        recurring_charge_id: UUID,
        organization_id: UUID,
        *,
        period: date,
        amount: Decimal,
        notes: str | None,
        actor_user_id: UUID,
        today: date | None = None,
    ):
        """RF-05: "el importe varia mes a mes y se ingresa a mano" (UC-11).
        RN-D01: 404 si el concepto no existe/es de otro tenant. CA-05-08:
        `409 CHARGE_ENTRY_ALREADY_EXISTS` si ya hay un `charge_entry` para
        ese `(recurring_charge_id, period)` -- corregir es un PATCH."""
        charge = await self._charge_repo.get_by_id(recurring_charge_id, organization_id)
        if charge is None:
            raise NotFoundException()

        today = today if today is not None else datetime.now(tz=UTC).date()
        if not _period_not_future(period, today=today):
            # RF-05 §Validaciones: "mes valido no futuro".
            raise ValidationError(field="period", message="El period no puede ser futuro.")

        existing = await self._entry_repo.get_by_charge_and_period(
            recurring_charge_id, organization_id, period
        )
        if existing is not None:
            raise ChargeEntryAlreadyExistsException(
                field="period",
                details={
                    "recurring_charge_id": str(recurring_charge_id),
                    "period": period.isoformat(),
                },
            )

        entry = await self._entry_repo.create(
            organization_id=organization_id,
            recurring_charge_id=recurring_charge_id,
            period=period,
            amount=amount,
            notes=notes,
            created_by=actor_user_id,
        )
        await self._entry_repo.commit()
        return entry

    async def correct_entry(
        self,
        charge_entry_id: UUID,
        organization_id: UUID,
        *,
        amount: Decimal | None,
        notes: str | None,
        fields_set: set[str],
        actor_user_id: UUID,
    ):
        """RN-D04: "las correcciones de plata siempre quedan trazadas en
        el log de auditoria con valor anterior y nuevo" -- `PATCH
        /charge-entries/:id`."""
        existing = await self._entry_repo.get_by_id(charge_entry_id, organization_id)
        if existing is None:
            raise NotFoundException()

        update_fields: dict[str, object] = {}
        before: dict[str, object] = {}
        after: dict[str, object] = {}
        if "amount" in fields_set and amount is not None and amount != existing.amount:
            update_fields["amount"] = amount
            before["amount"] = str(existing.amount)
            after["amount"] = str(amount)
        if "notes" in fields_set and notes != existing.notes:
            update_fields["notes"] = notes
            before["notes"] = existing.notes
            after["notes"] = notes

        if not update_fields:
            # Nada cambio -- no genera un evento de auditoria vacio.
            return existing

        updated = await self._entry_repo.update(
            charge_entry_id, organization_id, fields=update_fields
        )
        if updated is None:  # pragma: no cover -- defensivo, ya se valido existencia arriba
            raise NotFoundException()

        # RN-D04: misma transaccion que el UPDATE (confirmados juntos por
        # el `commit()` de abajo).
        await audit(
            self._entry_repo.session,
            organization_id=organization_id,
            action="charge_entry.corrected",
            entity_type="charge_entry",
            entity_id=charge_entry_id,
            before=before,
            after=after,
            user_id=actor_user_id,
        )

        await self._entry_repo.commit()
        return updated

    async def list_verification(
        self, *, organization_id: UUID, period: date
    ) -> list[ChargeVerificationRow]:
        """RF-05/CA-05-08: `GET /charge-entries?period=` -- checklist
        mensual: que propiedades tienen sus cargos cargados y cuales
        faltan (conceptos `is_active` unicamente, ver `repository.py`)."""
        return await self._entry_repo.list_verification(
            organization_id=organization_id, period=period
        )


def get_charge_entry_service(
    entry_repo: ChargeEntryRepository = Depends(get_charge_entry_repository),
    charge_repo: RecurringChargeRepository = Depends(get_recurring_charge_repository),
) -> ChargeEntryService:
    return ChargeEntryService(entry_repo, charge_repo)
