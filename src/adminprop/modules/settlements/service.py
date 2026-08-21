"""Logica de negocio de `settlements`: validaciones sincronicas +
encolado del calculo asincrono (RF-01) + formula pura de liquidacion
(RF-02) (issue #29).

SDD: docs/sdd/features/spec_module_05_liquidaciones.md §RF-01/RF-02.
Implements: CA-05-01 (formula + redondeo half-even), CA-05-02 (TC
obligatorio con USD, RN-L06), CA-05-03 (202 + polling +
`with_errors`/advertencias), CA-05-04/CA-04-08 (cobro "ya rendido"),
RN-L01/RN-L02/RN-L04/RN-L05.

`calculate_settlement` es una funcion PURA (sin I/O) para que la formula
y el redondeo sean unit-testeables sin Postgres real -- el worker
(`workers/documents_worker.py`) es quien la invoca con los datos ya
leidos por `SettlementRepository.gather_generation_data`.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import ROUND_HALF_EVEN, Decimal
from uuid import UUID

from fastapi import Depends

from adminprop.modules.settlements.exports import group_line_items_by_property
from adminprop.modules.settlements.repository import (
    GatheredSettlementData,
    MissingChargeEntryRow,
    SettlementRepository,
    UnpaidRentPeriodRow,
    get_settlement_repository,
)
from adminprop.shared.errors.codes import (
    BusinessRuleViolationException,
    InvalidStatusTransitionException,
    NotFoundException,
    SettlementAlreadyExistsException,
    SettlementExchangeRateRequiredException,
    ValidationError,
)

# RF-02: "commission_pct del propietario x (alquileres + intereses
# cobrados) del periodo, incluidos los cobrados directo por el dueño" --
# el destino que integra el "neto a rendir" (RN-L01/RN-P07). Valor real
# del CHECK de `payments.destination` (migracion #20,
# `create_capa4_cobranzas.py`: "CHECK (destination IN ('agency_account',
# 'landlord_account'))") -- "administracion" en el texto del SDD se
# traduce a `agency_account` en el schema.
_ADMINISTRATION_DESTINATION = "agency_account"


def round2(value: Decimal) -> Decimal:
    """CA-05-01: "el neto coincide con la formula a centavo (redondeo
    half-even a 2 decimales)" -- aplicado SOLO a los totales finales de
    cada agregado (RN-L01/L02) y a cada linea persistida (valor monetario
    discreto que va a una columna `NUMERIC(14,2)`), nunca a valores
    intermedios sin cerrar."""
    return value.quantize(Decimal("0.01"), rounding=ROUND_HALF_EVEN)


def _convert_to_ars(amount: Decimal, currency: str, exchange_rate: Decimal | None) -> Decimal:
    """RN-L06: "todo en ARS; los montos USD se convierten con el
    exchange_rate de la liquidacion". `exchange_rate` ya fue validado
    como no-None por el caller cuando `currency == 'USD'` (CA-05-02,
    validacion sincronica antes del 202)."""
    if currency == "USD":
        assert exchange_rate is not None
        return amount * exchange_rate
    return amount


@dataclass(frozen=True)
class SettlementLineItemData:
    """Una fila de `settlement_line_items` sin persistir todavia --
    `repository.apply_calculation` la vuelca 1:1 al modelo ORM."""

    line_type: str
    property_id: UUID | None
    source_entity_type: str | None
    source_entity_id: UUID | None
    original_amount: Decimal
    original_currency: str
    amount_ars: Decimal
    description: str | None

    def as_dict(self) -> dict:
        return {
            "line_type": self.line_type,
            "property_id": self.property_id,
            "source_entity_type": self.source_entity_type,
            "source_entity_id": self.source_entity_id,
            "original_amount": self.original_amount,
            "original_currency": self.original_currency,
            "amount_ars": self.amount_ars,
            "description": self.description,
        }


@dataclass(frozen=True)
class SettlementCalculationResult:
    total_collected: Decimal
    commission_total: Decimal
    charges_total: Decimal
    repairs_total: Decimal
    already_settled_total: Decimal
    net_amount: Decimal
    line_items: list[SettlementLineItemData]
    warnings: list[str]
    settled_work_order_ids: list[UUID]


def calculate_settlement(
    *,
    data: GatheredSettlementData,
    commission_pct: Decimal,
    exchange_rate: Decimal | None,
    unpaid_periods: list[UnpaidRentPeriodRow],
    missing_charges: list[MissingChargeEntryRow],
) -> SettlementCalculationResult:
    """RF-02, formula completa:

    ```
    neto = Sigma cobros del periodo destino administracion (capital + intereses)
         - comision = commission_pct x (alquileres + intereses cobrados),
                      TODOS los destinos (RN-L02, "incluidos los directos")
         - Sigma cargos del mes
         - Sigma reparaciones agency closed aun no liquidadas (RN-L04)
    ```

    Los cobros `landlord_account` ("ya rendidos", RN-P07) NO suman ni
    restan del neto -- son puramente informativos (linea
    `already_settled`) y solo integran la base de comision (RN-L02). La
    lectura literal de CA-05-04/CA-04-08 ("descuenta del neto") se
    interpreta como "no incrementa lo que la administracion le debe al
    propietario" -- consistente con la formula RF-02, que es la fuente
    mas precisa (documentado como decision de implementacion del PR)."""
    line_items: list[SettlementLineItemData] = []
    total_collected = Decimal("0.00")
    already_settled_total = Decimal("0.00")
    commission_base = Decimal(0)

    for payment in data.payments:
        gross = payment.amount + payment.charged_interest
        converted_raw = _convert_to_ars(gross, payment.currency, exchange_rate)
        # RN-L02: la base de comision suma TODOS los destinos, sin
        # redondear todavia (se redondea una sola vez al cerrar
        # `commission_total`, mas abajo).
        commission_base += converted_raw
        converted = round2(converted_raw)

        if payment.destination == _ADMINISTRATION_DESTINATION:
            total_collected += converted
            line_type = "rent_collected"
        else:
            # RN-P07/CA-05-04/CA-04-08: "ya rendido" -- informativo.
            already_settled_total += converted
            line_type = "already_settled"

        line_items.append(
            SettlementLineItemData(
                line_type=line_type,
                property_id=payment.property_id,
                source_entity_type="payment",
                source_entity_id=payment.payment_id,
                original_amount=gross,
                original_currency=payment.currency,
                amount_ars=converted,
                description=None,
            )
        )

    # RN-L05: `commission_pct_used` ya viene congelado por el caller (el
    # % vigente del propietario al momento de generar).
    commission_total = round2(commission_base * (commission_pct / Decimal(100)))
    line_items.append(
        SettlementLineItemData(
            line_type="commission",
            property_id=None,
            source_entity_type=None,
            source_entity_id=None,
            original_amount=commission_total,
            original_currency="ARS",
            amount_ars=commission_total,
            description=(
                f"Comision {commission_pct}% sobre alquileres e intereses "
                "cobrados del periodo (incluye cobros directos, RN-L02)."
            ),
        )
    )

    charges_total = Decimal("0.00")
    for charge in data.charge_entries:
        amount = round2(charge.amount)
        charges_total += amount
        line_items.append(
            SettlementLineItemData(
                line_type="tax_charge",
                property_id=charge.property_id,
                source_entity_type="charge_entry",
                source_entity_id=charge.charge_entry_id,
                original_amount=charge.amount,
                original_currency="ARS",
                amount_ars=amount,
                description=None,
            )
        )

    repairs_total = Decimal("0.00")
    settled_work_order_ids: list[UUID] = []
    for repair in data.repairs:
        amount = round2(repair.final_cost)
        repairs_total += amount
        settled_work_order_ids.append(repair.work_order_id)
        line_items.append(
            SettlementLineItemData(
                line_type="repair",
                property_id=repair.property_id,
                source_entity_type="work_order",
                source_entity_id=repair.work_order_id,
                original_amount=repair.final_cost,
                original_currency="ARS",
                amount_ars=amount,
                description=None,
            )
        )

    # RN-L01: "neto a rendir = cobros destino administracion - comision -
    # cargos - reparaciones"; el "ya rendido" no participa (ver docstring).
    net_amount = round2(total_collected - commission_total - charges_total - repairs_total)

    warnings: list[str] = []
    for unpaid in unpaid_periods:
        warnings.append(
            f"Periodo impago en la propiedad {unpaid.property_id} "
            f"(rent_period {unpaid.rent_period_id}, estado: {unpaid.status})."
        )
    for missing in missing_charges:
        warnings.append(
            f"Falta cargar el cargo '{missing.label}' de la propiedad "
            f"{missing.property_id} para el periodo."
        )

    return SettlementCalculationResult(
        total_collected=total_collected,
        commission_total=commission_total,
        charges_total=charges_total,
        repairs_total=repairs_total,
        already_settled_total=already_settled_total,
        net_amount=net_amount,
        line_items=line_items,
        warnings=warnings,
        settled_work_order_ids=settled_work_order_ids,
    )


@dataclass(frozen=True)
class SettlementDetailData:
    settlement: object
    line_items: list
    job_status: str
    warnings: list[str]
    needs_regeneration: bool
    property_groups: list | None = None
    general_items: list | None = None


# RF-04: `GET /settlements/:id?scope=`.
_VALID_SCOPES = ("consolidated", "per_property")


class SettlementService:
    """RF-01: `POST /settlements/generate` (validaciones sincronicas +
    encolado) + `GET /settlements`/`GET /settlements/:id` (lectura)."""

    def __init__(self, repo: SettlementRepository) -> None:
        self._repo = repo

    async def generate(
        self,
        *,
        organization_id: UUID,
        landlord_id: UUID,
        period: date,
        exchange_rate: Decimal | None,
        actor_user_id: UUID,
        request_id: str,
        today: date | None = None,
    ):
        """RF-01: valida sincronicamente (existencia, mes no futuro,
        duplicado, TC requerido, regla de "sin propiedad/movimientos") y
        crea el placeholder `draft` -- el worker
        (`workers/documents_worker.generate_settlement`) completa el
        calculo. Retorna la fila `Settlement` recien creada."""
        if not await self._repo.landlord_exists(landlord_id, organization_id):
            raise NotFoundException()

        today = today if today is not None else datetime.now(tz=UTC).date()
        current_month = date(today.year, today.month, 1)
        if period > current_month:
            # RF-02 §Validaciones: "period: mes valido no futuro".
            raise ValidationError(field="period", message="El period no puede ser futuro.")

        existing = await self._repo.get_by_landlord_and_period(landlord_id, organization_id, period)
        if existing is not None:
            # RN-05/RF-01: "unica por (landlord_id, period)".
            raise SettlementAlreadyExistsException(
                details={"landlord_id": str(landlord_id), "period": period.isoformat()}
            )

        commission_pct = await self._repo.get_landlord_commission_pct(landlord_id, organization_id)
        if commission_pct is None:  # pragma: no cover -- defensivo, ya se valido landlord_exists
            raise NotFoundException()

        gathered = await self._repo.gather_generation_data(landlord_id, organization_id, period)

        # CA-05-02/RN-L06: TC obligatorio SINCRONICO si hay USD en el
        # periodo -- validacion ANTES de crear cualquier fila.
        has_usd = any(payment.currency == "USD" for payment in gathered.payments)
        if has_usd and exchange_rate is None:
            raise SettlementExchangeRateRequiredException(field="exchange_rate")

        has_active_contract = await self._repo.has_active_contract_for_landlord(
            landlord_id, organization_id
        )
        has_movements = bool(gathered.payments) or bool(gathered.charge_entries)
        if not has_active_contract and not has_movements:
            # RF-02 §Validaciones: "no se puede generar la liquidacion de
            # un periodo si el propietario no tiene ninguna propiedad con
            # contrato activo ni movimientos en ese mes".
            raise BusinessRuleViolationException(
                message=(
                    "El propietario no tiene ninguna propiedad con contrato activo "
                    "ni movimientos en ese periodo."
                )
            )

        settlement = await self._repo.create_placeholder(
            organization_id=organization_id,
            landlord_id=landlord_id,
            period=period,
            exchange_rate=exchange_rate,
            commission_pct_used=commission_pct,
            generated_by=actor_user_id,
        )
        await self._repo.commit()

        # Import diferido: `workers.documents_worker` importa
        # `modules.settlements.repository`/`service` -- un import a nivel
        # de modulo aca crearia un ciclo real (mismo criterio defensivo
        # que `shared/notifications/service.py.enqueue_pending_emails`).
        from adminprop.modules.settlements.job_status import set_job_status
        from adminprop.workers.documents_worker import generate_settlement

        await set_job_status(settlement.id, "pending")
        generate_settlement.apply_async(args=[str(settlement.id), str(organization_id), request_id])
        return settlement

    async def get_detail(
        self, settlement_id: UUID, organization_id: UUID, *, scope: str = "consolidated"
    ) -> SettlementDetailData:
        """RF-01/RF-02/RF-04: `GET /settlements/:id?scope=` -- mezcla la
        fila de Postgres (totales + line items, siempre reales una vez que
        el job termino) con el estado del job (Redis, ver `job_status.py`).
        `scope=per_property` (RF-04) agrupa las lineas por propiedad con
        subtotal; `consolidated` (default) devuelve solo el detalle plano
        (ya incluido siempre en `line_items`, sdd_03 §11: "consolidated
        ... devuelve los totales + detalle plano")."""
        if scope not in _VALID_SCOPES:
            raise ValidationError(field="scope", message="scope debe ser consolidated o per_property.")

        settlement = await self._repo.get_by_id(settlement_id, organization_id)
        if settlement is None:
            raise NotFoundException()

        line_items = await self._repo.list_line_items(settlement_id, organization_id)

        from adminprop.modules.settlements.job_status import get_job_status

        job = await get_job_status(settlement_id)
        job_status = job["status"] if job is not None else "completed"
        warnings = job["warnings"] if job is not None else []

        flags = await self._repo.list_needs_regeneration_flags([settlement_id], organization_id)
        needs_regeneration = flags.get(settlement_id, False)

        property_groups = None
        general_items = None
        if scope == "per_property":
            # RF-04: agrupa por propiedad -- reutiliza la funcion pura
            # `group_line_items_by_property` (misma que usan los exports,
            # `exports.py`).
            property_ids = {item.property_id for item in line_items if item.property_id is not None}
            labels = await self._repo.list_property_labels(list(property_ids), organization_id)
            property_groups, general_items = group_line_items_by_property(line_items, labels)

        return SettlementDetailData(
            settlement=settlement,
            line_items=line_items,
            job_status=job_status,
            warnings=warnings,
            needs_regeneration=needs_regeneration,
            property_groups=property_groups,
            general_items=general_items,
        )

    async def list(
        self,
        *,
        organization_id: UUID,
        period: date | None,
        landlord_id: UUID | None,
        status: str | None,
    ) -> tuple[list, dict[UUID, bool]]:
        """sdd_03 §11: `GET /settlements?period=&landlord_id=&status=`.
        Devuelve tambien el mapa de "requiere regeneracion" por id
        (CA-05-06: "visible asi en el listado")."""
        settlements = await self._repo.list(
            organization_id=organization_id, period=period, landlord_id=landlord_id, status=status
        )
        flags = await self._repo.list_needs_regeneration_flags(
            [s.id for s in settlements], organization_id
        )
        return settlements, flags

    async def issue(
        self, settlement_id: UUID, organization_id: UUID, *, actor_user_id: UUID, request_id: str
    ):
        """RF-03: `POST /settlements/:id/issue` -- `draft -> issued`
        (unica transicion valida, RF-03). No se puede emitir mientras el
        job de calculo (generacion o regeneracion) todavia esta
        `pending`/`processing` -- los totales todavia no son definitivos."""
        settlement = await self._repo.get_by_id(settlement_id, organization_id)
        if settlement is None:
            raise NotFoundException()
        if settlement.status != "draft":
            # RF-03: "draft -> issued" es la unica transicion -- una
            # liquidacion ya `issued` no vuelve a emitirse (se REGENERA,
            # RN-L03, pero sigue `issued`).
            raise InvalidStatusTransitionException(
                details={"from_status": settlement.status, "to_status": "issued"}
            )

        from adminprop.modules.settlements.job_status import get_job_status

        job = await get_job_status(settlement_id)
        job_status = job["status"] if job is not None else "completed"
        if job_status in ("pending", "processing"):
            raise BusinessRuleViolationException(
                message="La liquidacion todavia se esta calculando; espera a que termine antes de emitirla."
            )

        updated = await self._repo.issue(settlement_id, organization_id)
        if updated is None:  # pragma: no cover -- defensivo, ya se valido existencia arriba
            raise NotFoundException()

        from adminprop.shared.audit.service import audit

        # RF-03: la emision queda auditada (RN-D04, correccion/estado de
        # liquidaciones siempre trazado).
        await audit(
            self._repo.session,
            organization_id=organization_id,
            action="settlement.issued",
            entity_type="settlement",
            entity_id=settlement_id,
            before={"status": "draft"},
            after={"status": "issued"},
            user_id=actor_user_id,
            request_id=request_id,
        )
        await self._repo.commit()
        return updated

    async def regenerate(
        self,
        *,
        settlement_id: UUID,
        organization_id: UUID,
        exchange_rate: Decimal | None,
        actor_user_id: UUID,
        request_id: str,
    ):
        """RF-03/RN-L03: `POST /settlements/:id/regenerate` -- recalcula
        con los datos actuales (cobros anulados/agregados, cargos
        corregidos, TC nuevo si se pasa). Una liquidacion `issued` sigue
        siendo regenerable (R-04, "la flexibilidad es deliberada") --
        SIN transicion de estado (`status` no cambia). Validaciones
        sincronicas antes del 202, mismo patron que `generate`."""
        settlement = await self._repo.get_by_id(settlement_id, organization_id)
        if settlement is None:
            raise NotFoundException()

        from adminprop.modules.settlements.job_status import get_job_status, set_job_status

        job = await get_job_status(settlement_id)
        job_status = job["status"] if job is not None else "completed"
        if job_status in ("pending", "processing"):
            raise BusinessRuleViolationException(
                message="Ya hay un calculo en curso para esta liquidacion."
            )

        effective_rate = exchange_rate if exchange_rate is not None else settlement.exchange_rate
        gathered = await self._repo.gather_generation_data(
            settlement.landlord_id, organization_id, settlement.period
        )
        # RN-L06/CA-05-02: TC obligatorio SINCRONICO si hay USD, igual que
        # `generate` -- puede haber cobros nuevos en USD desde la
        # generacion original.
        has_usd = any(payment.currency == "USD" for payment in gathered.payments)
        if has_usd and effective_rate is None:
            raise SettlementExchangeRateRequiredException(field="exchange_rate")

        # Import diferido: mismo motivo que `generate` (ciclo real con
        # `workers.documents_worker`).
        from adminprop.workers.documents_worker import regenerate_settlement

        await set_job_status(settlement_id, "pending")
        regenerate_settlement.apply_async(
            args=[
                str(settlement_id),
                str(organization_id),
                request_id,
                str(exchange_rate) if exchange_rate is not None else None,
                str(actor_user_id),
            ]
        )
        return settlement


def get_settlement_service(
    repo: SettlementRepository = Depends(get_settlement_repository),
) -> SettlementService:
    return SettlementService(repo)
