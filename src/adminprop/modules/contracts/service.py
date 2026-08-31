"""Logica de negocio del modulo contratos (issue #17).

SDD: docs/sdd/features/spec_module_03_contratos.md RF-01..RF-03, RF-06
(issue #106). Implements: CA-03-01, 02, 03, 06, 08, CA-01-04 (RN-01/RN-C01,
RN-02, RN-03/RN-C02, RN-04/RN-C04, RN-06, RN-07/RN-C05); CA-03-16..22
(RN-09, serie mensual de valores locativos).

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
from decimal import Decimal
from uuid import UUID

from fastapi import Depends

from adminprop.modules.contracts.adjustment_repository import (
    ContractAdjustmentRepository,
    get_contract_adjustment_repository,
)
from adminprop.modules.contracts.historical_amounts import (
    build_synthetic_chain,
    expected_tramo_count,
    tramo_ranges,
)
from adminprop.modules.contracts.models import Contract
from adminprop.modules.contracts.monthly_amounts import (
    AppliedAdjustment,
    MonthlyAmountRow,
    compute_monthly_amounts,
)
from adminprop.modules.contracts.rent_period_hook import (
    generate_initial_load_history,
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
    InvalidDateRangeException,
    InvalidStatusTransitionException,
    NotFoundException,
    ValidationError,
)
from adminprop.shared.notifications import service as notifications_service


class ContractService:
    """RF-01 (listado y consulta) + RF-02 (alta) + RF-03 (ciclo de vida)."""

    def __init__(
        self,
        repo: ContractRepository,
        property_repo: PropertyRepository,
        adjustment_repo: ContractAdjustmentRepository | None = None,
    ) -> None:
        self._repo = repo
        self._property_repo = property_repo
        # RN-08/RN-C06 (issue #100): opcional con fallback a la MISMA
        # `session` de `repo` -- asi `workers/notification_worker.py`, que
        # instancia `ContractService(contract_repo, property_repo)`
        # posicional sin este 3er argumento, sigue funcionando sin
        # cambios (nunca llama a `create`, solo a `detect_expiring_and_expired`).
        self._adjustment_repo = adjustment_repo or ContractAdjustmentRepository(repo.session)

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
        current_amount: Decimal | None = None,
        current_amount_since: date | None = None,
        historical_amounts: list[Decimal] | None = None,
        actor_user_id: UUID | None = None,
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

        # RN-08/RN-C06 v2 (issue #107, supersede parcialmente el issue
        # #100): alta de contrato en curso. Dos mecanismos mutuamente
        # excluyentes segun `adjustment_frequency_months`, ya enforzados a
        # nivel de shape por `ContractCreate` (Pydantic). Este bloque
        # resuelve las validaciones que necesitan estado/"hoy" y arma el
        # monto de arranque final del contrato -- `final_current_amount`
        # va al INSERT de `contracts` (una sola escritura, sin importar el
        # mecanismo).
        final_current_amount = current_amount
        synthetic_chain: list[tuple[date, Decimal, Decimal]] = []
        # RN-11/RN-P09 (issue #119): "hoy" unico para todo `create()` --
        # tanto las validaciones de fecha de los dos mecanismos de carga
        # inicial (abajo) como el calculo de cobros retroactivos
        # (despues del INSERT) usan el MISMO valor.
        today = datetime.now(UTC).date()

        if current_amount is not None and current_amount_since is not None:
            # Camino sin `adjustment_frequency_months` (USD siempre; ARS
            # sin ajuste) -- comportamiento del issue #100, sin cambios.
            # CA-03-14: ambos extremos del rango -- `>= start_date` y
            # `<= hoy` -- son 400 INVALID_DATE_RANGE (no VALIDATION_ERROR
            # generico), asi que ambos se validan aca con el mismo
            # `error.code`.
            if current_amount_since < start_date:
                raise InvalidDateRangeException(
                    field="current_amount_since",
                    message="current_amount_since no puede ser anterior a start_date.",
                    details={
                        "current_amount_since": current_amount_since.isoformat(),
                        "start_date": start_date.isoformat(),
                    },
                )
            if current_amount_since > today:
                raise InvalidDateRangeException(
                    field="current_amount_since",
                    message="current_amount_since no puede ser posterior a hoy.",
                    details={"current_amount_since": current_amount_since.isoformat()},
                )
            synthetic_chain = [(current_amount_since, initial_amount, current_amount)]

        elif historical_amounts is not None:
            # Camino con `adjustment_frequency_months` configurado (v2,
            # issue #107) -- cadena guiada de tramos. CA-03-13: el primer
            # valor debe coincidir con `initial_amount` (es el monto del
            # tramo 0, ya declarado por ese campo).
            if historical_amounts[0] != initial_amount:
                raise ValidationError(
                    field="historical_amounts",
                    message="historical_amounts[0] debe ser igual a initial_amount.",
                    details={
                        "historical_amounts_0": str(historical_amounts[0]),
                        "initial_amount": str(initial_amount),
                    },
                )
            # CA-03-09/10/11/12: cantidad exacta de tramos transcurridos
            # (necesita "hoy" -- por eso se valida aca, no en Pydantic,
            # mismo criterio que `current_amount_since <= hoy`).
            expected_count = expected_tramo_count(
                start_date=start_date,
                adjustment_frequency_months=adjustment_frequency_months,
                today=today,
            )
            if len(historical_amounts) != expected_count:
                ranges = tramo_ranges(
                    start_date=start_date,
                    adjustment_frequency_months=adjustment_frequency_months,
                    count=expected_count,
                )
                raise ValidationError(
                    field="historical_amounts",
                    message=(
                        f"Se esperan {expected_count} valores en historical_amounts "
                        f"({expected_count} tramos transcurridos desde {start_date.isoformat()} "
                        f"con ajuste cada {adjustment_frequency_months} meses), "
                        f"se recibieron {len(historical_amounts)}."
                    ),
                    details={
                        "expected_count": expected_count,
                        "received_count": len(historical_amounts),
                        "tramos": [
                            {
                                "index": tramo.index,
                                "start": tramo.start.isoformat(),
                                "end": tramo.end.isoformat(),
                            }
                            for tramo in ranges
                        ],
                    },
                )
            final_current_amount = historical_amounts[-1]
            chain = build_synthetic_chain(
                start_date=start_date,
                adjustment_frequency_months=adjustment_frequency_months,
                historical_amounts=historical_amounts,
            )
            synthetic_chain = [
                (link.due_period, link.previous_amount, link.new_amount) for link in chain
            ]

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
            current_amount=final_current_amount,
        )

        if synthetic_chain:
            # RN-08/RN-C06 v2, CA-03-09/10/11: ajuste(s) sintetico(s)
            # "applied" de carga inicial -- MISMA transaccion que el
            # INSERT del contrato (si algo falla, nada de esto queda a
            # medias). El ULTIMO de la cadena es el ancla de
            # `detect_due_adjustments` (RN-C03) sin tocar esa logica.
            for due_period, previous_amount, new_amount in synthetic_chain:
                await self._adjustment_repo.create_applied_initial(
                    organization_id=organization_id,
                    contract_id=row.id,
                    due_period=due_period,
                    previous_amount=previous_amount,
                    new_amount=new_amount,
                    actor_user_id=actor_user_id,
                )
            await audit(
                self._repo.session,
                organization_id=organization_id,
                action="contract.current_amount_declared",
                entity_type="contract",
                entity_id=row.id,
                before={"current_amount": str(initial_amount)},
                after={
                    "current_amount": str(final_current_amount),
                    "synthetic_adjustments": len(synthetic_chain),
                },
                user_id=actor_user_id,
            )

        # RN-11/RN-P09 (issue #119, feedback #3 del PO): alta de contrato
        # en curso -- los meses YA TRANSCURRIDOS (desde `start_date` hasta
        # el mes ANTERIOR al actual) nacen `paid` con un cobro
        # `initial_load` automatico. Dispara para CUALQUIER `start_date`
        # anterior al mes actual, con o sin `synthetic_chain` (RN-08): el
        # caso "arranco el mes pasado sin ningun tramo de ajuste" tambien
        # cuenta -- `compute_monthly_amounts` ya resuelve ambos casos
        # (serie plana en `initial_amount` si no hay ajustes `applied`).
        # El mes actual NUNCA entra aca (se filtra abajo): sigue naciendo
        # `pending` por la via normal (`activate`/job mensual, sin
        # cambios).
        applied_for_calc = [
            AppliedAdjustment(due_period=due_period, new_amount=new_amount)
            for due_period, _previous_amount, new_amount in synthetic_chain
        ]
        monthly_rows = compute_monthly_amounts(
            status="draft",
            start_date=start_date,
            end_date=end_date,
            initial_amount=initial_amount,
            applied_adjustments=applied_for_calc,
            today=today,
            terminated_at=None,
        )
        current_period = date(today.year, today.month, 1)
        past_rows = [r for r in reversed(monthly_rows) if r.period < current_period]

        if past_rows:
            periods_generated = await generate_initial_load_history(
                self._repo.session,
                contract_id=row.id,
                organization_id=organization_id,
                currency=currency,
                past_periods=past_rows,
                actor_user_id=actor_user_id,
            )
            if periods_generated:
                # RN-11: evento resumen de la carga masiva (cantidad de
                # periodos/cobros generados), no uno por periodo.
                await audit(
                    self._repo.session,
                    organization_id=organization_id,
                    action="contract.initial_load_generated",
                    entity_type="contract",
                    entity_id=row.id,
                    after={
                        "periods_generated": periods_generated,
                        "first_period": past_rows[0].period.isoformat(),
                        "last_period": past_rows[-1].period.isoformat(),
                    },
                    user_id=actor_user_id,
                )

        await self._repo.commit()
        return row

    async def get(self, contract_id: UUID, organization_id: UUID):
        return await self._repo.get_by_id(contract_id, organization_id)

    async def get_monthly_amounts(
        self, contract: Contract, organization_id: UUID, *, today: date
    ) -> list[MonthlyAmountRow]:
        """RF-06/RN-09 (issue #106): `GET /contracts/:id` §"monthly_amounts[]".
        Esta capa solo resuelve los datos de DB que necesita el calculo
        puro (`monthly_amounts.compute_monthly_amounts`): los ajustes
        `applied` del contrato, y -- solo si esta `terminated` -- la fecha
        de terminacion efectiva (evento `contract.terminated` de
        `audit_logs`, ver `repository.py.get_terminated_at`)."""
        applied = await self._adjustment_repo.list_applied_by_contract(contract.id, organization_id)
        terminated_at = None
        if contract.status == "terminated":
            terminated_at = await self._repo.get_terminated_at(contract.id, organization_id)
        return compute_monthly_amounts(
            status=contract.status,
            start_date=contract.start_date,
            end_date=contract.end_date,
            initial_amount=contract.initial_amount,
            applied_adjustments=[
                AppliedAdjustment(due_period=row.due_period, new_amount=row.new_amount)
                for row in applied
            ],
            today=today,
            terminated_at=terminated_at,
        )

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
    adjustment_repo: ContractAdjustmentRepository = Depends(get_contract_adjustment_repository),
) -> ContractService:
    return ContractService(repo, property_repo, adjustment_repo)
