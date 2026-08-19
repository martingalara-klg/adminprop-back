"""Logica de negocio del modulo contratos (issue #17).

SDD: docs/sdd/features/spec_module_03_contratos.md RF-01..RF-03.
Implements: CA-03-01, 02, 03, 06, 08, CA-01-04 (RN-01/RN-C01, RN-02,
RN-03/RN-C02, RN-04/RN-C04, RN-06, RN-07/RN-C05).

Fuera de alcance (ver PR "Decisiones de implementacion"):
- RF-04 (ajustes por indice: deteccion diaria, bandeja, aplicar %) y
  RF-05 (alertas de vencimiento) son los issues #18 y #19.
- El rent_period del mes en curso al activar (RF-03) se genera via
  `rent_period_hook.maybe_generate_current_month_rent_period` (issue #21;
  la tabla `rent_periods` es del issue #20).
- RN-03/RN-C02 (USD sin ajuste) se enforza en `schemas.py.ContractCreate`
  a nivel Pydantic (produce el mismo `error.code` VALIDATION_ERROR via el
  handler global) -- este service no necesita revalidarlo.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from uuid import UUID

from fastapi import Depends

from adminprop.modules.contracts.rent_period_hook import (
    maybe_generate_current_month_rent_period,
)
from adminprop.modules.contracts.repository import (
    ContractFilters,
    ContractRepository,
    get_contract_repository,
)
from adminprop.modules.properties.repository import (
    PropertyRepository,
    get_property_repository,
)
from adminprop.shared.audit.service import audit
from adminprop.shared.errors.codes import (
    BusinessRuleViolationException,
    ContractNotActiveException,
    ContractOverlapException,
    InvalidStatusTransitionException,
    NotFoundException,
)
from adminprop.shared.notifications import service as notifications_service


class ContractService:
    """RF-01 (listado y consulta) + RF-02 (alta) + RF-03 (ciclo de vida)."""

    def __init__(self, repo: ContractRepository, property_repo: PropertyRepository) -> None:
        self._repo = repo
        self._property_repo = property_repo

    async def create(
        self,
        *,
        organization_id: UUID,
        property_id: UUID,
        renter_id: UUID,
        currency: str,
        initial_amount,
        start_date: date,
        end_date: date,
        daily_late_fee_pct,
        adjustment_frequency_months: int | None,
        adjustment_index: str | None,
        adjustment_index_notes: str | None,
        notes: str | None,
    ):
        """RF-02 + CA-03-01/03: `property_id`/`renter_id` validados contra
        el mismo tenant (RN-06/RN-D01); RN-03/RN-C02 (USD sin ajuste) ya
        lo enforza `ContractCreate` a nivel Pydantic -- aca solo quedan
        las validaciones que necesitan estado de DB."""
        if not await self._repo.property_exists(property_id, organization_id):
            raise NotFoundException(message="La propiedad indicada no existe.", field="property_id")
        if not await self._repo.renter_exists(renter_id, organization_id):
            raise NotFoundException(message="El inquilino indicado no existe.", field="renter_id")

        # RN-01/RN-C01, CA-03-02: "crear o activar" -- tambien se valida
        # al crear (aunque el contrato nazca `draft`, RF-02 lo exige
        # explicitamente para no dejar pasar un `draft` que jamas podria
        # activarse sin chocar).
        conflicting = await self._repo.find_overlapping_active_contract(
            property_id=property_id,
            organization_id=organization_id,
            start_date=start_date,
            end_date=end_date,
        )
        if conflicting is not None:
            raise ContractOverlapException(
                field="start_date", details={"conflicting_contract_id": str(conflicting.id)}
            )

        row = await self._repo.create(
            organization_id=organization_id,
            property_id=property_id,
            renter_id=renter_id,
            currency=currency,
            initial_amount=initial_amount,
            start_date=start_date,
            end_date=end_date,
            daily_late_fee_pct=daily_late_fee_pct,
            adjustment_frequency_months=adjustment_frequency_months,
            adjustment_index=adjustment_index,
            adjustment_index_notes=adjustment_index_notes,
            notes=notes,
        )
        await self._repo.commit()
        return row

    async def get(self, contract_id: UUID, organization_id: UUID):
        return await self._repo.get_by_id(contract_id, organization_id)

    async def list(
        self,
        *,
        organization_id: UUID,
        cursor: str | None,
        limit: int,
        filters: ContractFilters,
    ) -> tuple[list, str | None]:
        return await self._repo.list(
            organization_id=organization_id, cursor=cursor, limit=limit, filters=filters
        )

    async def update(
        self,
        contract_id: UUID,
        organization_id: UUID,
        *,
        notes: str | None,
        end_date: date | None,
        current_amount,
        actor_user_id: UUID,
        fields_set: set[str],
    ):
        """RF-03 + CA-03-06: `sdd_03` §8 -- PATCH "solo notes/metadata;
        montos NUNCA (RN-C04)". `current_amount` en el body (sin importar
        el valor) es siempre 422 BUSINESS_RULE_VIOLATION: el monto
        vigente solo cambia via un ajuste registrado (issue #18), nunca
        por PATCH -- independientemente de si el contrato esta `draft` o
        `active`. `end_date` es la unica condicion editable siempre
        (RF-03: "fechas de fin se pueden extender... quedando auditado")."""
        current = await self._repo.get_by_id(contract_id, organization_id)
        if current is None:
            raise NotFoundException()

        if "current_amount" in fields_set and current_amount is not None:
            raise BusinessRuleViolationException(
                field="current_amount",
                message=(
                    "El monto vigente no puede editarse por PATCH; "
                    "solo cambia mediante un ajuste registrado (RN-C04)."
                ),
                details={"contract_id": str(contract_id)},
            )

        update_fields: dict[str, object] = {}
        if "notes" in fields_set:
            update_fields["notes"] = notes

        previous_end_date = current.end_date
        if "end_date" in fields_set and end_date is not None:
            update_fields["end_date"] = end_date

        updated = await self._repo.update(contract_id, organization_id, fields=update_fields)
        if updated is None:  # pragma: no cover -- defensivo, ya se valido existencia arriba
            raise NotFoundException()

        if "end_date" in fields_set and end_date is not None and end_date != previous_end_date:
            # RF-03: extension de fecha de fin queda auditada.
            await audit(
                self._repo.session,
                organization_id=organization_id,
                action="contract.end_date_extended",
                entity_type="contract",
                entity_id=contract_id,
                before={"end_date": previous_end_date.isoformat()},
                after={"end_date": end_date.isoformat()},
                user_id=actor_user_id,
            )

        await self._repo.commit()
        return updated

    async def activate(self, contract_id: UUID, organization_id: UUID, *, actor_user_id: UUID):
        """RF-03 + CA-03-01/02, CA-01-04: `draft -> active`. Revalida
        solapamiento (RN-01/RN-C01), pone la propiedad en `rented`
        (CA-01-04) y dispara el hook (no-op hoy) de generacion del
        rent_period del mes en curso si `start_date` ya paso."""
        contract = await self._repo.get_by_id(contract_id, organization_id)
        if contract is None:
            raise NotFoundException()

        if contract.status != "draft":
            raise InvalidStatusTransitionException(
                message="Solo un contrato en borrador puede activarse.",
                details={"current_status": contract.status},
            )

        conflicting = await self._repo.find_overlapping_active_contract(
            property_id=contract.property_id,
            organization_id=organization_id,
            start_date=contract.start_date,
            end_date=contract.end_date,
            exclude_contract_id=contract.id,
        )
        if conflicting is not None:
            raise ContractOverlapException(
                field="start_date", details={"conflicting_contract_id": str(conflicting.id)}
            )

        updated = await self._repo.update(contract_id, organization_id, fields={"status": "active"})
        if updated is None:  # pragma: no cover -- defensivo
            raise NotFoundException()

        # CA-01-04: la propiedad pasa a `rented` automaticamente.
        await self._property_repo.update(
            contract.property_id, organization_id, fields={"status": "rented"}
        )

        await maybe_generate_current_month_rent_period(
            self._repo.session,
            contract_id=contract.id,
            organization_id=organization_id,
            start_date=contract.start_date,
            today=datetime.now(UTC).date(),
            amount_due=contract.current_amount,
            currency=contract.currency,
        )

        await audit(
            self._repo.session,
            organization_id=organization_id,
            action="contract.activated",
            entity_type="contract",
            entity_id=contract_id,
            before={"status": "draft"},
            after={"status": "active"},
            user_id=actor_user_id,
        )

        await self._repo.commit()
        return updated

    async def terminate(
        self,
        contract_id: UUID,
        organization_id: UUID,
        *,
        reason: str,
        actor_user_id: UUID,
    ):
        """RF-03 + CA-03-08: `active -> terminated` con motivo. La
        propiedad vuelve a `available`; las deudas existentes siguen
        cobrables (RN-07/RN-C05 -- no hay `rent_periods` que tocar
        todavia, issue #20). El `reason` se persiste en `audit_logs`
        (la tabla `contracts` no declara una columna propia para el
        motivo -- ver `sdd_02` §2.7, sin ese atributo)."""
        contract = await self._repo.get_by_id(contract_id, organization_id)
        if contract is None:
            raise NotFoundException()

        if contract.status != "active":
            raise ContractNotActiveException(details={"current_status": contract.status})

        updated = await self._repo.update(
            contract_id, organization_id, fields={"status": "terminated"}
        )
        if updated is None:  # pragma: no cover -- defensivo
            raise NotFoundException()

        # CA-01-04/CA-03-08: la propiedad vuelve a `available`.
        await self._property_repo.update(
            contract.property_id, organization_id, fields={"status": "available"}
        )

        await audit(
            self._repo.session,
            organization_id=organization_id,
            action="contract.terminated",
            entity_type="contract",
            entity_id=contract_id,
            before={"status": "active"},
            after={"status": "terminated", "reason": reason},
            user_id=actor_user_id,
        )

        await self._repo.commit()
        return updated

    # ─── RF-03/RF-05 (issue #19): job diario `detect_expiring_contracts` ──

    async def detect_expiring_and_expired(
        self, *, organization_id: UUID, today: date
    ) -> list[UUID]:
        """Cuerpo de negocio del job diario `detect_expiring_contracts`
        (`sdd_04` §1.3), llamado por el worker Beat por cada organizacion
        `active` -- mismo patron que
        `adjustment_service.py.detect_due_adjustments`.

        Dos pasos independientes, en este orden:

        1. RF-03 (`active -> expired` automatico, RN-C05/RN-07): todo
           contrato `active` cuyo `end_date` ya paso pasa a `expired` y su
           propiedad vuelve a `available` -- mismo efecto que `terminate`
           (issue #17), auditado con `user_id=None` (accion del sistema,
           `shared/audit/service.py`). Las deudas existentes siguen
           cobrables (RN-07): este metodo no toca `rent_periods`.
        2. RF-05/CA-03-07: de los contratos que siguen `active` (los recien
           expirados en el paso 1 ya no califican), notifica una sola vez
           los que vencen dentro de `contract_expiry_notice_days` de la
           organizacion (idempotencia: `expiring_notified_at IS NULL`,
           filtrado por el repository).

        Devuelve los IDs de notificacion `contract_expiring` creadas -- el
        worker las usa DESPUES de su commit para encolar el email (patron
        outbox, igual que `detect_due_adjustments`).
        """
        # Paso 1 -- RF-03: transicion automatica active -> expired.
        expired_contracts = await self._repo.list_active_past_end_date(organization_id, today=today)
        for contract in expired_contracts:
            await self._repo.update(contract.id, organization_id, fields={"status": "expired"})
            # CA-01-04/CA-03-08 (mismo efecto que terminate): la propiedad
            # vuelve a `available`.
            await self._property_repo.update(
                contract.property_id, organization_id, fields={"status": "available"}
            )
            await audit(
                self._repo.session,
                organization_id=organization_id,
                action="contract.expired",
                entity_type="contract",
                entity_id=contract.id,
                before={"status": "active"},
                after={"status": "expired"},
                user_id=None,  # RN-D: accion automatica del sistema, sin actor humano
            )

        # Paso 2 -- RF-05/CA-03-07: aviso de vencimiento, una sola vez.
        notice_days = await self._repo.get_expiry_notice_days(organization_id)
        due_contracts = await self._repo.list_active_due_for_expiry_notice(
            organization_id, today=today, notice_days=notice_days
        )
        notification_ids: list[UUID] = []
        notified_at = datetime.now(UTC)
        for contract in due_contracts:
            # RF-01 (spec_notificaciones.md): notificacion in-app en la
            # MISMA transaccion (CA-NT-02); el email se encola DESPUES del
            # commit (patron outbox, ver el worker).
            ids = await notifications_service.emit(
                self._repo.session,
                organization_id=organization_id,
                event_type="contract_expiring",
                payload={"contract_id": str(contract.id)},
            )
            notification_ids.extend(ids)
            # CA-03-07: marca ANTES del commit, en la misma transaccion --
            # si algo de este loop rollbackea, la marca tambien (no queda
            # un aviso enviado sin su notificacion in-app persistida).
            await self._repo.mark_expiring_notified(
                contract.id, organization_id, notified_at=notified_at
            )

        await self._repo.commit()
        return notification_ids


def get_contract_service(
    repo: ContractRepository = Depends(get_contract_repository),
    property_repo: PropertyRepository = Depends(get_property_repository),
) -> ContractService:
    return ContractService(repo, property_repo)
