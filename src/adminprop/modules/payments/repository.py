"""Acceso a datos de `rent_periods` (issue #21) y `payments` (issue #22).

SDD: infrastructure/spec_data_model.md §Capa 4. docs/skills/tenant-isolation.md
("todo metodo del repository recibe organization_id como parametro y lo
aplica en el WHERE" -- defense in depth sobre RLS, RN-D01).
docs/skills/async-worker.md ("jobs Beat idempotentes -- las UNIQUE
constraints los protegen"): `insert_pending` es el INSERT idempotente que
tanto el job mensual `generate_rent_periods` (issue #21,
`workers/notification_worker.py`) como los hooks de contratos
(`modules/contracts/rent_period_hook.py`, issues #17/#18) usan para
respetar RN-P01 (un solo `rent_period` por `contract_id`+`period`).

`get_by_id`/`update_after_payment`/`commit` de `RentPeriodRepository`, y
la clase `PaymentRepository` completa, son del issue #22 -- el #21
deliberadamente no los agrego (su unico consumidor era el job Beat, que
maneja su propio commit via `session.begin()` del caller).

`list_candidates`/`get_candidate` (issue #23, RF-02/RF-06) hacen el JOIN
`rent_periods -> contracts -> properties` para resolver `property_id`/
`landlord_id`/`renter_id`/`daily_late_fee_pct` de cada periodo -- filtro
EXPLICITO de `organization_id` en las TRES tablas (docs/skills/
tenant-isolation.md §"Queries con join/agregacion", RN-D01), no solo en
`rent_periods`. `days_late`/`suggested_interest` (campos calculados, no
columnas) se resuelven en `service.py` reutilizando
`compute_days_late`/`compute_suggested_interest` (RN-P02/P03) -- el
repository solo devuelve los datos crudos necesarios para ese calculo.

Ese JOIN usa SQL crudo (`text()`, mismo patron que
`modules/contracts/repository.py.get_expiry_notice_days`) en vez de los
modelos ORM `Contract`/`Property`: importar `adminprop.modules.properties.models`
a nivel de modulo aca dispara la carga de `properties/__init__.py`, que
importa `properties.router` -> `properties.repository` -> `people.models`
-> `people/__init__.py` -> `people.router` -> (de vuelta) `properties.repository`
-- un ciclo real, confirmado corriendo `python -c "import adminprop.main"`
dentro del contenedor (`ImportError: cannot import name 'PropertyRepository'
from partially initialized module`), porque este repository se alcanza
muy temprano en el arranque via `contracts.rent_period_hook` (issues
#17/#18), antes de que `properties`/`people` terminen de cargar por su
cuenta. SQL crudo rompe esa dependencia de import sin tocar el orden de
carga de ningun otro modulo.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import UUID

from fastapi import Depends
from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from adminprop.db.session import get_tenant_db_session
from adminprop.modules.payments.models import Payment, RentPeriod


@dataclass(frozen=True)
class PaymentReceiptContext:
    """RF-07 (issue #24): fila cruda del join `payments -> rent_periods ->
    contracts -> properties/renters` -- todos los datos de texto que
    `PaymentService.generate_receipt_pdf` necesita para el recibo, sin
    recalcular nada (el interes ya quedo congelado en `payments` al
    momento del cobro, RN-P04)."""

    id: UUID
    payment_date: date
    method: str
    payment_currency: str
    amount: Decimal
    exchange_rate: Decimal | None
    destination: str
    charged_interest: Decimal
    voided_at: datetime | None
    period: date
    contract_currency: str
    renter_name: str
    property_address: str


@dataclass(frozen=True)
class RentPeriodCandidate:
    """Fila cruda del JOIN `rent_periods -> contracts -> properties` --
    todavia sin `days_late`/`suggested_interest`/`balance` (esos son
    calculados por `service.py`, no datos de la fila)."""

    id: UUID
    contract_id: UUID
    property_id: UUID
    landlord_id: UUID
    renter_id: UUID
    period: date
    amount_due: Decimal
    currency: str
    status: str
    paid_total: Decimal
    daily_late_fee_pct: Decimal
    created_at: datetime


class RentPeriodRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    @property
    def session(self) -> AsyncSession:
        return self._session

    async def insert_pending(
        self,
        *,
        organization_id: UUID,
        contract_id: UUID,
        period: date,
        amount_due: Decimal,
        currency: str,
    ) -> UUID | None:
        """RN-P01 (UNIQUE `rent_periods_contract_period_unique` sobre
        `(contract_id, period)`, migracion #20): INSERT idempotente via
        `ON CONFLICT DO NOTHING` (sdd_04 §1.3 -- "Jobs Beat idempotentes
        [...] las UNIQUE constraints los protegen"). `status` nace
        `pending` (default de la columna, RF-01). Devuelve el `id` del
        `rent_period` recien creado, o `None` si ya existia (conflicto) --
        el caller no necesita diferenciar "ya existia" de "se creo ahora"
        salvo para loggear/contar en tests.
        """
        stmt = (
            pg_insert(RentPeriod)
            .values(
                organization_id=organization_id,
                contract_id=contract_id,
                period=period,
                amount_due=amount_due,
                currency=currency,
            )
            .on_conflict_do_nothing(
                index_elements=[RentPeriod.contract_id, RentPeriod.period],
            )
            .returning(RentPeriod.id)
        )
        result = await self._session.execute(stmt)
        row = result.first()
        return row.id if row is not None else None

    async def get_by_contract_and_period(
        self, contract_id: UUID, organization_id: UUID, period: date
    ) -> RentPeriod | None:
        """Usado por tests (y por el futuro RF-02 panel de cobranzas) para
        verificar el `rent_period` generado -- filtro explicito de
        `organization_id` (RN-D01) ademas del RLS."""
        stmt = select(RentPeriod).where(
            RentPeriod.contract_id == contract_id,
            RentPeriod.organization_id == organization_id,
            RentPeriod.period == period,
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_by_id(self, rent_period_id: UUID, organization_id: UUID) -> RentPeriod | None:
        """RN-D01: filtro explicito de `organization_id` (defense in depth
        sobre RLS) -- usado por `PaymentService` (issue #22) para resolver
        el periodo a cobrar/previsualizar."""
        stmt = select(RentPeriod).where(
            RentPeriod.id == rent_period_id,
            RentPeriod.organization_id == organization_id,
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def update_after_payment(
        self,
        rent_period_id: UUID,
        organization_id: UUID,
        *,
        paid_total: Decimal,
        status: str,
    ) -> RentPeriod | None:
        """RF-03, RN-P05: recalcula `paid_total`/`status` tras imputar un
        cobro (`pending`/`partial` -> `partial`/`paid`, CA-04-04/CA-04-05).
        Filtro explicito de `organization_id` (RN-D01)."""
        row = await self.get_by_id(rent_period_id, organization_id)
        if row is None:
            return None
        row.paid_total = paid_total
        row.status = status
        await self._session.flush()
        await self._session.refresh(row)
        return row

    async def commit(self) -> None:
        await self._session.commit()

    # ─── RF-02/RF-06 (issue #23): panel del mes + estado de deuda ──────────

    # RN-D01: filtro EXPLICITO de `organization_id` en las TRES tablas del
    # join (docs/skills/tenant-isolation.md §"Queries con join/agregacion")
    # -- no alcanza con filtrarlo solo en `rent_periods`. SQL crudo: ver
    # motivo (evitar el ciclo de import `properties`<->`people`) en el
    # docstring del modulo.
    _CANDIDATE_SQL = """
        SELECT
            rp.id AS id,
            rp.contract_id AS contract_id,
            c.property_id AS property_id,
            p.landlord_id AS landlord_id,
            c.renter_id AS renter_id,
            rp.period AS period,
            rp.amount_due AS amount_due,
            rp.currency AS currency,
            rp.status AS status,
            rp.paid_total AS paid_total,
            c.daily_late_fee_pct AS daily_late_fee_pct,
            rp.created_at AS created_at
        FROM rent_periods rp
        JOIN contracts c ON c.id = rp.contract_id AND c.organization_id = :org_id
        JOIN properties p ON p.id = c.property_id AND p.organization_id = :org_id
        WHERE rp.organization_id = :org_id
    """

    async def list_candidates(
        self,
        *,
        organization_id: UUID,
        period: date | None = None,
        status: str | None = None,
        unpaid_only: bool = False,
        property_id: UUID | None = None,
        landlord_id: UUID | None = None,
        renter_id: UUID | None = None,
    ) -> list[RentPeriodCandidate]:
        """RF-02 (panel del mes) + RF-06/CA-02-05 (deuda): candidatos crudos
        del join, ordenados `created_at desc, id desc` (mismo criterio de
        orden que el resto de los listados del repo) -- `in_arrears`/
        `days_late`/`suggested_interest` y la paginacion por cursor las
        resuelve `service.py` porque dependen de `today`/`grace_day`
        (RN-P02/P03), no de columnas de la fila. `unpaid_only` filtra
        `status IN ('pending', 'partial')` -- usado por RF-06/CA-02-05
        (solo interesan los periodos con deuda)."""
        conditions: list[str] = []
        params: dict[str, object] = {"org_id": str(organization_id)}
        if period is not None:
            conditions.append("rp.period = :period")
            params["period"] = period
        if status is not None:
            conditions.append("rp.status = :status")
            params["status"] = status
        if unpaid_only:
            conditions.append("rp.status IN ('pending', 'partial')")
        if property_id is not None:
            conditions.append("c.property_id = :property_id")
            params["property_id"] = str(property_id)
        if landlord_id is not None:
            conditions.append("p.landlord_id = :landlord_id")
            params["landlord_id"] = str(landlord_id)
        if renter_id is not None:
            conditions.append("c.renter_id = :renter_id")
            params["renter_id"] = str(renter_id)

        sql = self._CANDIDATE_SQL
        if conditions:
            sql += " AND " + " AND ".join(conditions)
        sql += " ORDER BY rp.created_at DESC, rp.id DESC"

        result = await self._session.execute(text(sql), params)
        return [RentPeriodCandidate(**dict(row._mapping)) for row in result]

    async def get_candidate(
        self, rent_period_id: UUID, organization_id: UUID
    ) -> RentPeriodCandidate | None:
        """RF-02: `GET /rent-periods/:id` -- misma forma que `list_candidates`
        pero para un solo periodo (RN-D01: filtro explicito + join en las
        tres tablas, 404 si es de otro tenant o no existe)."""
        sql = self._CANDIDATE_SQL + " AND rp.id = :rent_period_id"
        result = await self._session.execute(
            text(sql),
            {"org_id": str(organization_id), "rent_period_id": str(rent_period_id)},
        )
        row = result.first()
        return RentPeriodCandidate(**dict(row._mapping)) if row is not None else None


class PaymentRepository:
    """Acceso a datos de `payments` (issue #22, RF-03/RF-04)."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    @property
    def session(self) -> AsyncSession:
        """Expuesto para que `service.py` pase la MISMA sesion a
        `AuditService.audit()` -- mismo criterio que
        `modules/contracts/repository.py.ContractRepository.session`."""
        return self._session

    async def insert(
        self,
        *,
        organization_id: UUID,
        rent_period_id: UUID,
        payment_date: date,
        method: str,
        payment_currency: str,
        amount: Decimal,
        exchange_rate: Decimal | None,
        destination: str,
        suggested_interest: Decimal,
        charged_interest: Decimal,
        forgiven_interest: Decimal,
        days_late: int,
        notes: str | None,
        created_by: UUID,
    ) -> Payment:
        """RF-03/RF-04: persiste el cobro con los tres valores de interes
        (RN-P04) y el TC usado (RN-P06) -- inmutable una vez creado
        (RN-06/RN-D04, la correccion es anular + recargar, issue #23)."""
        row = Payment(
            organization_id=organization_id,
            rent_period_id=rent_period_id,
            payment_date=payment_date,
            method=method,
            payment_currency=payment_currency,
            amount=amount,
            exchange_rate=exchange_rate,
            destination=destination,
            suggested_interest=suggested_interest,
            charged_interest=charged_interest,
            forgiven_interest=forgiven_interest,
            days_late=days_late,
            notes=notes,
            created_by=created_by,
        )
        self._session.add(row)
        await self._session.flush()
        await self._session.refresh(row)
        return row

    async def get_by_id(self, payment_id: UUID, organization_id: UUID) -> Payment | None:
        """RN-D01: filtro explicito de `organization_id` -- usado por
        RF-05 (anulacion, issue #23) y por tests."""
        stmt = select(Payment).where(
            Payment.id == payment_id,
            Payment.organization_id == organization_id,
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def void(
        self, payment_id: UUID, organization_id: UUID, *, voided_by: UUID
    ) -> Payment | None:
        """RF-05/RN-D04: anulacion logica -- setea `voided_at`/`voided_by`.
        El caller (`service.py.PaymentService.void_payment`) ya valido
        existencia y que no estuviera anulado (`409
        PAYMENT_ALREADY_VOIDED`); filtro explicito de `organization_id`
        (RN-D01) por defense in depth."""
        row = await self.get_by_id(payment_id, organization_id)
        if row is None:  # pragma: no cover -- defensivo, el service ya valido existencia
            return None
        row.voided_at = datetime.now(UTC)
        row.voided_by = voided_by
        await self._session.flush()
        await self._session.refresh(row)
        return row

    async def commit(self) -> None:
        await self._session.commit()

    # ─── RF-07 (issue #24): recibo de cobro ─────────────────────────────

    async def get_receipt_context(
        self, payment_id: UUID, organization_id: UUID
    ) -> PaymentReceiptContext | None:
        """RF-07: datos crudos para el recibo PDF -- SQL crudo (mismo
        motivo que `RentPeriodRepository._CANDIDATE_SQL`, evitar el ciclo
        de import `properties`<->`people` documentado en el docstring del
        modulo). Filtro EXPLICITO de `organization_id` en las CUATRO
        tablas del join (RN-D01, tenant-isolation.md)."""
        sql = """
            SELECT
                pay.id AS id,
                pay.payment_date AS payment_date,
                pay.method AS method,
                pay.payment_currency AS payment_currency,
                pay.amount AS amount,
                pay.exchange_rate AS exchange_rate,
                pay.destination AS destination,
                pay.charged_interest AS charged_interest,
                pay.voided_at AS voided_at,
                rp.period AS period,
                c.currency AS contract_currency,
                r.name AS renter_name,
                p.address AS property_address
            FROM payments pay
            JOIN rent_periods rp ON rp.id = pay.rent_period_id AND rp.organization_id = :org_id
            JOIN contracts c ON c.id = rp.contract_id AND c.organization_id = :org_id
            JOIN properties p ON p.id = c.property_id AND p.organization_id = :org_id
            JOIN renters r ON r.id = c.renter_id AND r.organization_id = :org_id
            WHERE pay.id = :payment_id AND pay.organization_id = :org_id
        """
        result = await self._session.execute(
            text(sql), {"org_id": str(organization_id), "payment_id": str(payment_id)}
        )
        row = result.mappings().first()
        return PaymentReceiptContext(**dict(row)) if row is not None else None


def get_rent_period_repository(
    session: AsyncSession = Depends(get_tenant_db_session),
) -> RentPeriodRepository:
    return RentPeriodRepository(session)


def get_payment_repository(
    session: AsyncSession = Depends(get_tenant_db_session),
) -> PaymentRepository:
    return PaymentRepository(session)
