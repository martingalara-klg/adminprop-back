"""Acceso a datos de `settlements` y `settlement_line_items`, mas las
queries de agregacion (payments/charge_entries/work_orders) que arman la
formula de liquidacion (issue #29).

SDD: infrastructure/spec_data_model.md §Capa 6 + docs/sdd/features/
spec_module_05_liquidaciones.md §RF-01/RF-02. docs/skills/
tenant-isolation.md ("todo metodo del repository recibe organization_id
como parametro y lo aplica en el WHERE" -- defense in depth sobre RLS,
RN-D01).

Las queries de agregacion usan SQL crudo (`text()`) para los JOINs contra
`payments -> rent_periods -> contracts -> properties`,
`charge_entries -> recurring_charges -> properties` y
`work_orders -> properties` -- mismo motivo documentado en
`modules/payments/repository.py` y `modules/maintenance/repository.py`:
evitar el ciclo de import real `properties <-> people` confirmado en esos
modulos. `settlements`/`settlement_line_items` SI usan el ORM (son las
tablas propias de este modulo, sin riesgo de ciclo). Filtro EXPLICITO de
`organization_id` en TODAS las tablas de cada JOIN (docs/skills/
tenant-isolation.md §"Queries con join/agregacion", RN-D01).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from fastapi import Depends
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from adminprop.db.session import get_tenant_db_session
from adminprop.modules.settlements.models import Settlement, SettlementLineItem


@dataclass(frozen=True)
class SettlementPaymentRow:
    """Fila cruda de un cobro del periodo, para landlord's properties --
    ambos `destination` (RF-02: la comision se calcula sobre los dos)."""

    payment_id: UUID
    property_id: UUID
    currency: str
    amount: Decimal
    charged_interest: Decimal
    destination: str


@dataclass(frozen=True)
class SettlementChargeEntryRow:
    charge_entry_id: UUID
    property_id: UUID
    amount: Decimal


@dataclass(frozen=True)
class SettlementRepairRow:
    work_order_id: UUID
    property_id: UUID
    final_cost: Decimal


@dataclass(frozen=True)
class UnpaidRentPeriodRow:
    """CA-05-03: periodo impago del landlord en el mes -- fuente de una
    advertencia (`with_errors`)."""

    rent_period_id: UUID
    property_id: UUID
    status: str


@dataclass(frozen=True)
class MissingChargeEntryRow:
    """CA-05-03: concepto activo sin cargo cargado en el mes -- fuente de
    una advertencia (`with_errors`)."""

    recurring_charge_id: UUID
    property_id: UUID
    label: str


@dataclass(frozen=True)
class GatheredSettlementData:
    """RF-02: universo de datos crudos de un `(landlord_id, period)` --
    ver `service.calculate_settlement` para la formula que los consume."""

    payments: list[SettlementPaymentRow]
    charge_entries: list[SettlementChargeEntryRow]
    repairs: list[SettlementRepairRow]


# RN-D01: filtro EXPLICITO de `organization_id` en las CUATRO tablas del
# join (docs/skills/tenant-isolation.md §"Queries con join/agregacion").
# `pay.voided_at IS NULL` -- CLAUDE.md §"Fuentes de verdad": "EXCLUÍ
# anulados". Ambos `destination` (agency_account/landlord_account): la
# comision (RF-02) se calcula sobre los dos, `service.py` separa por tipo.
_PAYMENTS_SQL = """
    SELECT
        pay.id AS payment_id,
        p.id AS property_id,
        pay.payment_currency AS currency,
        pay.amount AS amount,
        pay.charged_interest AS charged_interest,
        pay.destination AS destination
    FROM payments pay
    JOIN rent_periods rp ON rp.id = pay.rent_period_id AND rp.organization_id = :org_id
    JOIN contracts c ON c.id = rp.contract_id AND c.organization_id = :org_id
    JOIN properties p ON p.id = c.property_id AND p.organization_id = :org_id
    WHERE pay.organization_id = :org_id
      AND p.landlord_id = :landlord_id
      AND rp.period = :period
      AND pay.voided_at IS NULL
"""

# Cargos del mes (rentas/muni/otro, RF-05) de las propiedades del
# propietario en el periodo -- siempre ARS (spec_data_model.md
# "charge_entries.amount": "Siempre ARS en MVP").
_CHARGE_ENTRIES_SQL = """
    SELECT
        ce.id AS charge_entry_id,
        rc.property_id AS property_id,
        ce.amount AS amount
    FROM charge_entries ce
    JOIN recurring_charges rc ON rc.id = ce.recurring_charge_id AND rc.organization_id = :org_id
    JOIN properties p ON p.id = rc.property_id AND p.organization_id = :org_id
    WHERE ce.organization_id = :org_id
      AND p.landlord_id = :landlord_id
      AND ce.period = :period
"""

# RN-L04: reparaciones `closed`/`payer=agency` AUN NO liquidadas (sin
# restriccion de periodo -- "se descuentan en la proxima liquidacion",
# sdd_02 §2.13) de las propiedades del propietario.
_REPAIRS_SQL = """
    SELECT
        wo.id AS work_order_id,
        wo.property_id AS property_id,
        wo.final_cost AS final_cost
    FROM work_orders wo
    JOIN properties p ON p.id = wo.property_id AND p.organization_id = :org_id
    WHERE wo.organization_id = :org_id
      AND p.landlord_id = :landlord_id
      AND wo.status = 'closed'
      AND wo.payer = 'agency'
      AND wo.settled_in_settlement_id IS NULL
      AND wo.deleted_at IS NULL
      AND wo.final_cost IS NOT NULL
"""

# CA-05-03: periodos `pending`/`partial` del propietario en el mes --
# advertencia de "periodos impagos".
_UNPAID_RENT_PERIODS_SQL = """
    SELECT
        rp.id AS rent_period_id,
        c.property_id AS property_id,
        rp.status AS status
    FROM rent_periods rp
    JOIN contracts c ON c.id = rp.contract_id AND c.organization_id = :org_id
    JOIN properties p ON p.id = c.property_id AND p.organization_id = :org_id
    WHERE rp.organization_id = :org_id
      AND p.landlord_id = :landlord_id
      AND rp.period = :period
      AND rp.status IN ('pending', 'partial')
"""

# CA-05-03: conceptos `is_active` sin `charge_entry` en el mes --
# advertencia de "cargos faltantes" (mismo patron de LEFT JOIN que
# `modules/charges/repository.py.list_verification`).
_MISSING_CHARGE_ENTRIES_SQL = """
    SELECT
        rc.id AS recurring_charge_id,
        rc.property_id AS property_id,
        rc.label AS label
    FROM recurring_charges rc
    JOIN properties p ON p.id = rc.property_id AND p.organization_id = :org_id
    LEFT JOIN charge_entries ce
        ON ce.recurring_charge_id = rc.id
        AND ce.organization_id = :org_id
        AND ce.period = :period
    WHERE rc.organization_id = :org_id
      AND p.landlord_id = :landlord_id
      AND rc.is_active = true
      AND rc.deleted_at IS NULL
      AND ce.id IS NULL
"""


class SettlementRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    @property
    def session(self) -> AsyncSession:
        """Expuesto para que `service.py` reutilice la MISMA sesion --
        mismo criterio que `modules/charges/repository.py.session`."""
        return self._session

    # ─── Validaciones de existencia (RN-D01) ────────────────────────────

    async def landlord_exists(self, landlord_id: UUID, organization_id: UUID) -> bool:
        result = await self._session.execute(
            text(
                "SELECT 1 FROM landlords "
                "WHERE id = :landlord_id AND organization_id = :org_id AND deleted_at IS NULL"
            ),
            {"landlord_id": str(landlord_id), "org_id": str(organization_id)},
        )
        return result.first() is not None

    async def get_landlord_commission_pct(
        self, landlord_id: UUID, organization_id: UUID
    ) -> Decimal | None:
        """RN-L05: el % vigente al momento de generar se congela en
        `commission_pct_used` -- lectura liviana (sin descifrar
        `bank_info`), mismo criterio que
        `modules/people/repository.py.get_commission_pct`."""
        result = await self._session.execute(
            text(
                "SELECT commission_pct FROM landlords "
                "WHERE id = :landlord_id AND organization_id = :org_id AND deleted_at IS NULL"
            ),
            {"landlord_id": str(landlord_id), "org_id": str(organization_id)},
        )
        row = result.first()
        return row[0] if row is not None else None

    async def has_active_contract_for_landlord(
        self, landlord_id: UUID, organization_id: UUID
    ) -> bool:
        """RF-02 §Validaciones: "no se puede generar la liquidacion de un
        periodo si el propietario no tiene ninguna propiedad con contrato
        activo ni movimientos en ese mes"."""
        result = await self._session.execute(
            text(
                "SELECT 1 FROM contracts c "
                "JOIN properties p ON p.id = c.property_id AND p.organization_id = :org_id "
                "WHERE c.organization_id = :org_id AND p.landlord_id = :landlord_id "
                "AND c.status = 'active' LIMIT 1"
            ),
            {"org_id": str(organization_id), "landlord_id": str(landlord_id)},
        )
        return result.first() is not None

    # ─── Settlement CRUD (ORM) ───────────────────────────────────────────

    async def get_by_landlord_and_period(
        self, landlord_id: UUID, organization_id: UUID, period: date
    ) -> Settlement | None:
        """RF-01/RN-05: unicidad `(landlord_id, period)` -- red de UX
        antes del `409 SETTLEMENT_ALREADY_EXISTS` (el UNIQUE constraint de
        la migracion #27 es la red de seguridad real)."""
        stmt = select(Settlement).where(
            Settlement.landlord_id == landlord_id,
            Settlement.organization_id == organization_id,
            Settlement.period == period,
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_by_id(self, settlement_id: UUID, organization_id: UUID) -> Settlement | None:
        """RN-D01: filtro explicito de `organization_id`."""
        stmt = select(Settlement).where(
            Settlement.id == settlement_id,
            Settlement.organization_id == organization_id,
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def list(
        self,
        *,
        organization_id: UUID,
        period: date | None,
        landlord_id: UUID | None,
        status: str | None,
    ) -> list[Settlement]:
        """sdd_03 §11: `GET /settlements?period=&landlord_id=&status=`."""
        stmt = select(Settlement).where(Settlement.organization_id == organization_id)
        if period is not None:
            stmt = stmt.where(Settlement.period == period)
        if landlord_id is not None:
            stmt = stmt.where(Settlement.landlord_id == landlord_id)
        if status is not None:
            stmt = stmt.where(Settlement.status == status)
        stmt = stmt.order_by(Settlement.created_at.desc(), Settlement.id.desc())
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def create_placeholder(
        self,
        *,
        organization_id: UUID,
        landlord_id: UUID,
        period: date,
        exchange_rate: Decimal | None,
        commission_pct_used: Decimal,
        generated_by: UUID,
    ) -> Settlement:
        """RF-01: fila `draft` con totales en 0 -- placeholder que el
        worker (`documents_worker.generate_settlement`) completa. Existe
        ANTES del 202 para que `GET /settlements/:id` (polling) tenga un
        recurso real desde el primer momento (patron async-worker.md)."""
        row = Settlement(
            organization_id=organization_id,
            landlord_id=landlord_id,
            period=period,
            exchange_rate=exchange_rate,
            commission_pct_used=commission_pct_used,
            generated_by=generated_by,
        )
        self._session.add(row)
        await self._session.flush()
        await self._session.refresh(row)
        return row

    async def apply_calculation(
        self,
        settlement_id: UUID,
        organization_id: UUID,
        *,
        total_collected: Decimal,
        commission_total: Decimal,
        charges_total: Decimal,
        repairs_total: Decimal,
        already_settled_total: Decimal,
        net_amount: Decimal,
        line_items: list[dict],
        settled_work_order_ids: list[UUID],
    ) -> Settlement | None:
        """RF-02: persiste el resultado del calculo -- totales +
        line items + estampado de `settled_in_settlement_id` (RN-L04) en
        una sola transaccion (el `commit()` lo hace el caller, worker o
        service)."""
        row = await self.get_by_id(settlement_id, organization_id)
        if row is None:  # pragma: no cover -- defensivo, el worker ya valido existencia
            return None

        row.total_collected = total_collected
        row.commission_total = commission_total
        row.charges_total = charges_total
        row.repairs_total = repairs_total
        row.already_settled_total = already_settled_total
        row.net_amount = net_amount

        for item in line_items:
            self._session.add(
                SettlementLineItem(
                    organization_id=organization_id,
                    settlement_id=settlement_id,
                    **item,
                )
            )

        if settled_work_order_ids:
            # RN-L04: "se descuenta una unica vez" -- estampa el vinculo
            # para que la proxima generacion (RF-02, `_REPAIRS_SQL`) ya no
            # la vuelva a traer.
            await self._session.execute(
                text(
                    "UPDATE work_orders SET settled_in_settlement_id = :sid "
                    "WHERE id = ANY(:wo_ids) AND organization_id = :org_id"
                ),
                {
                    "sid": str(settlement_id),
                    "wo_ids": [str(wid) for wid in settled_work_order_ids],
                    "org_id": str(organization_id),
                },
            )

        await self._session.flush()
        await self._session.refresh(row)
        return row

    async def delete_placeholder(self, settlement_id: UUID, organization_id: UUID) -> None:
        """Decision de implementacion (documentada en el PR): si el job
        de calculo falla con un error real (RF-01: "failed: no se genero
        [...] el motivo queda en el job y en Sentry"), el placeholder
        `draft` creado antes del 202 NUNCA llego a ser una liquidacion de
        verdad (sin totales calculados, sin line items) -- se borra para
        que un reintento de `POST /settlements/generate` para el mismo
        `(landlord_id, period)` no choque con `409
        SETTLEMENT_ALREADY_EXISTS` contra una fila que nunca se genero.
        Distinto de RN-L03 (nunca se borra una liquidacion YA generada,
        que si tiene datos reales) -- ver `job_status.py` para el
        contexto completo de la decision."""
        await self._session.execute(
            text(
                "DELETE FROM settlement_line_items "
                "WHERE settlement_id = :sid AND organization_id = :org_id"
            ),
            {"sid": str(settlement_id), "org_id": str(organization_id)},
        )
        await self._session.execute(
            text("DELETE FROM settlements WHERE id = :sid AND organization_id = :org_id"),
            {"sid": str(settlement_id), "org_id": str(organization_id)},
        )

    async def list_line_items(
        self, settlement_id: UUID, organization_id: UUID
    ) -> list[SettlementLineItem]:
        stmt = (
            select(SettlementLineItem)
            .where(
                SettlementLineItem.settlement_id == settlement_id,
                SettlementLineItem.organization_id == organization_id,
            )
            .order_by(SettlementLineItem.created_at.asc())
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def commit(self) -> None:
        await self._session.commit()

    # ─── Datos crudos para la formula (RF-02) ───────────────────────────

    async def gather_generation_data(
        self, landlord_id: UUID, organization_id: UUID, period: date
    ) -> GatheredSettlementData:
        params = {
            "org_id": str(organization_id),
            "landlord_id": str(landlord_id),
            "period": period,
        }
        payments_result = await self._session.execute(text(_PAYMENTS_SQL), params)
        charges_result = await self._session.execute(text(_CHARGE_ENTRIES_SQL), params)
        repairs_result = await self._session.execute(
            text(_REPAIRS_SQL), {"org_id": params["org_id"], "landlord_id": params["landlord_id"]}
        )
        return GatheredSettlementData(
            payments=[SettlementPaymentRow(**dict(r)) for r in payments_result.mappings()],
            charge_entries=[
                SettlementChargeEntryRow(**dict(r)) for r in charges_result.mappings()
            ],
            repairs=[SettlementRepairRow(**dict(r)) for r in repairs_result.mappings()],
        )

    async def list_unpaid_rent_periods(
        self, landlord_id: UUID, organization_id: UUID, period: date
    ) -> list[UnpaidRentPeriodRow]:
        result = await self._session.execute(
            text(_UNPAID_RENT_PERIODS_SQL),
            {"org_id": str(organization_id), "landlord_id": str(landlord_id), "period": period},
        )
        return [UnpaidRentPeriodRow(**dict(r)) for r in result.mappings()]

    async def list_missing_charge_entries(
        self, landlord_id: UUID, organization_id: UUID, period: date
    ) -> list[MissingChargeEntryRow]:
        result = await self._session.execute(
            text(_MISSING_CHARGE_ENTRIES_SQL),
            {"org_id": str(organization_id), "landlord_id": str(landlord_id), "period": period},
        )
        return [MissingChargeEntryRow(**dict(r)) for r in result.mappings()]


def get_settlement_repository(
    session: AsyncSession = Depends(get_tenant_db_session),
) -> SettlementRepository:
    return SettlementRepository(session)
