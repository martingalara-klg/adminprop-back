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
from datetime import UTC, date, datetime
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
# sdd_02 §2.13) de las propiedades del propietario, MAS las que ya estan
# vinculadas a ESTA MISMA liquidacion (`:current_settlement_id`) -- issue
# #30/CA-05-05: una regeneracion recalcula desde cero (`clear_line_items`)
# pero una reparacion que YA se descuenta en esta liquidacion no debe
# "desaparecer" del total solo porque ya quedo estampada por una
# generacion/regeneracion anterior. En la generacion inicial (issue #29)
# `:current_settlement_id` viaja como NULL -- `wo.settled_in_settlement_id
# = NULL` nunca es verdadero en SQL, asi que el comportamiento original
# (solo reparaciones sin liquidar) queda intacto.
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
      AND (
          wo.settled_in_settlement_id IS NULL
          OR wo.settled_in_settlement_id = :current_settlement_id
      )
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

    async def issue(self, settlement_id: UUID, organization_id: UUID) -> Settlement | None:
        """RF-03: `draft -> issued` (`POST /settlements/:id/issue`). El
        caller (`service.py`) ya valido que la transicion es valida (solo
        `draft`) y que el job de calculo termino."""
        row = await self.get_by_id(settlement_id, organization_id)
        if row is None:
            return None
        row.status = "issued"
        row.issued_at = datetime.now(UTC)
        row.updated_at = datetime.now(UTC)
        await self._session.flush()
        await self._session.refresh(row)
        return row

    async def clear_line_items(self, settlement_id: UUID, organization_id: UUID) -> None:
        """RN-L03/RF-03: la regeneracion recalcula desde cero -- borra las
        lineas de la version anterior ANTES de que `apply_regeneration`
        inserte las nuevas (los TOTALES quedan siempre auditados en
        `audit_logs`, ver `service.py.regenerate`; las lineas en si no son
        append-only en el spec, a diferencia de la liquidacion misma --
        `spec_data_model.md` Apendice B solo declara "sin delete" para la
        FILA de `settlements`/`settlement_line_items` como conjunto de
        columnas persistentes, no exige historizar cada linea individual).
        No borra la liquidacion (`RN-L03`: nunca se borra una liquidacion
        ya generada)."""
        await self._session.execute(
            text(
                "DELETE FROM settlement_line_items "
                "WHERE settlement_id = :sid AND organization_id = :org_id"
            ),
            {"sid": str(settlement_id), "org_id": str(organization_id)},
        )

    async def apply_regeneration(
        self,
        settlement_id: UUID,
        organization_id: UUID,
        *,
        exchange_rate: Decimal | None,
        total_collected: Decimal,
        commission_total: Decimal,
        charges_total: Decimal,
        repairs_total: Decimal,
        already_settled_total: Decimal,
        net_amount: Decimal,
        line_items: list[dict],
        settled_work_order_ids: list[UUID],
    ) -> Settlement | None:
        """RF-03/RN-L03: aplica el resultado de la REGENERACION -- mismos
        pasos que `apply_calculation` (totales + lineas nuevas + estampado
        RN-L04) mas `regenerated_count++` y `exchange_rate` (si vino uno
        nuevo en el body de `POST /settlements/:id/regenerate`).
        `clear_line_items` ya debe haberse llamado antes en la misma
        transaccion."""
        row = await self.get_by_id(settlement_id, organization_id)
        if row is None:  # pragma: no cover -- defensivo, el worker ya valido existencia
            return None

        if exchange_rate is not None:
            row.exchange_rate = exchange_rate
        row.total_collected = total_collected
        row.commission_total = commission_total
        row.charges_total = charges_total
        row.repairs_total = repairs_total
        row.already_settled_total = already_settled_total
        row.net_amount = net_amount
        row.regenerated_count = row.regenerated_count + 1
        row.updated_at = datetime.now(UTC)

        for item in line_items:
            self._session.add(
                SettlementLineItem(
                    organization_id=organization_id,
                    settlement_id=settlement_id,
                    **item,
                )
            )

        if settled_work_order_ids:
            # RN-L04: mismo estampado que `apply_calculation` -- solo
            # afecta reparaciones NUEVAS (recien cerradas desde la ultima
            # generacion/regeneracion); las ya estampadas ni siquiera las
            # trae `_REPAIRS_SQL` (filtra `settled_in_settlement_id IS NULL`).
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

    async def find_issued_settlement_ids_by_payment(
        self, payment_id: UUID, organization_id: UUID
    ) -> list[UUID]:
        """RF-03 parrafo 3 (spec_module_05_liquidaciones.md): "si se anula
        un cobro incluido en una liquidacion, esta queda marcada 'requiere
        regeneracion'" -- solo aplica a liquidaciones YA `issued` (una
        `draft` con un cobro anulado simplemente se regenera libremente
        via `POST /settlements/:id/regenerate`, sin necesidad de la
        senializacion de "requiere regeneracion" -- esa senal es para que
        el listado alerte sobre una liquidacion YA entregada al
        propietario). Filtro EXPLICITO de `organization_id` en ambas
        tablas del join (RN-D01)."""
        result = await self._session.execute(
            text(
                """
                SELECT DISTINCT s.id
                FROM settlement_line_items sli
                JOIN settlements s
                    ON s.id = sli.settlement_id AND s.organization_id = :org_id
                WHERE sli.organization_id = :org_id
                  AND sli.source_entity_type = 'payment'
                  AND sli.source_entity_id = :payment_id
                  AND s.status = 'issued'
                """
            ),
            {"org_id": str(organization_id), "payment_id": str(payment_id)},
        )
        return [row[0] for row in result.all()]

    async def list_needs_regeneration_flags(
        self, settlement_ids: list[UUID], organization_id: UUID
    ) -> dict[UUID, bool]:
        """Decision de implementacion (documentada en el PR): no hay
        columna `needs_regeneration` en `settlements` (la migracion #27 es
        fiel al spec, que no la declara -- este issue no agrega
        migraciones). La senal se deriva de `audit_logs`: si existe un
        evento `settlement.needs_regeneration` (estampado por
        `payments.settlement_hook` al anular un cobro de una liquidacion
        `issued`) mas reciente que `settlements.updated_at`, la
        liquidacion todavia no se regenero desde esa anulacion. Una
        regeneracion posterior actualiza `updated_at` (ver
        `apply_regeneration`), "limpiando" la bandera sin necesidad de un
        UPDATE explicito ni de una columna nueva."""
        if not settlement_ids:
            return {}
        result = await self._session.execute(
            text(
                """
                SELECT s.id AS id, s.updated_at AS updated_at, MAX(al.created_at) AS flagged_at
                FROM settlements s
                LEFT JOIN audit_logs al
                    ON al.entity_id = s.id
                    AND al.organization_id = s.organization_id
                    AND al.entity_type = 'settlement'
                    AND al.action = 'settlement.needs_regeneration'
                WHERE s.organization_id = :org_id AND s.id = ANY(:ids)
                GROUP BY s.id, s.updated_at
                """
            ),
            {"org_id": str(organization_id), "ids": [str(i) for i in settlement_ids]},
        )
        flags: dict[UUID, bool] = {}
        for row in result.mappings():
            flagged_at = row["flagged_at"]
            flags[row["id"]] = flagged_at is not None and flagged_at > row["updated_at"]
        return flags

    async def get_landlord_name(self, landlord_id: UUID, organization_id: UUID) -> str | None:
        """RF-04/exports: nombre del propietario para el encabezado del
        Excel/PDF -- lectura liviana (sin descifrar `bank_info`), mismo
        criterio que `get_landlord_commission_pct`."""
        result = await self._session.execute(
            text("SELECT name FROM landlords WHERE id = :id AND organization_id = :org_id"),
            {"id": str(landlord_id), "org_id": str(organization_id)},
        )
        row = result.first()
        return row[0] if row is not None else None

    async def list_property_labels(
        self, property_ids: list[UUID], organization_id: UUID
    ) -> dict[UUID, str]:
        """RF-04: direccion de cada propiedad para agrupar los exports/el
        detalle `scope=per_property` -- SQL crudo (mismo motivo de ciclo de
        import documentado en `modules/payments/repository.py`)."""
        if not property_ids:
            return {}
        result = await self._session.execute(
            text(
                "SELECT id, address FROM properties "
                "WHERE organization_id = :org_id AND id = ANY(:ids)"
            ),
            {"org_id": str(organization_id), "ids": [str(i) for i in property_ids]},
        )
        return {row[0]: row[1] for row in result.all()}

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
        self,
        landlord_id: UUID,
        organization_id: UUID,
        period: date,
        *,
        current_settlement_id: UUID | None = None,
    ) -> GatheredSettlementData:
        """`current_settlement_id`: solo se pasa en una REGENERACION
        (issue #30, RN-L04/CA-05-05) -- ver docstring de `_REPAIRS_SQL`
        para por que una reparacion ya vinculada a ESTA liquidacion debe
        seguir contando en el recalculo."""
        params = {
            "org_id": str(organization_id),
            "landlord_id": str(landlord_id),
            "period": period,
        }
        payments_result = await self._session.execute(text(_PAYMENTS_SQL), params)
        charges_result = await self._session.execute(text(_CHARGE_ENTRIES_SQL), params)
        repairs_result = await self._session.execute(
            text(_REPAIRS_SQL),
            {
                "org_id": params["org_id"],
                "landlord_id": params["landlord_id"],
                "current_settlement_id": (
                    str(current_settlement_id) if current_settlement_id is not None else None
                ),
            },
        )
        return GatheredSettlementData(
            payments=[SettlementPaymentRow(**dict(r)) for r in payments_result.mappings()],
            charge_entries=[SettlementChargeEntryRow(**dict(r)) for r in charges_result.mappings()],
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
