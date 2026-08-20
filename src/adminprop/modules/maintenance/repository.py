"""Acceso a datos de `work_orders` y `work_order_quotes` (issue #26).

SDD: infrastructure/spec_data_model.md §Capa 5. docs/skills/tenant-isolation.md
("todo metodo del repository recibe organization_id y lo aplica en el
WHERE" -- defense in depth sobre RLS, RN-D01).

`get_with_address`/`list_with_address`/`history_by_property` hacen SQL
crudo (`text()`) uniendo `work_orders -> properties` -- mismo motivo
documentado en `modules/payments/repository.py` (evitar el ciclo de
import real `properties <-> people` confirmado en ese modulo: importar
`adminprop.modules.properties.models` a nivel de modulo dispara la cadena
`properties/__init__.py -> properties.router -> properties.repository ->
people.models -> people/__init__.py -> people.router -> properties.repository`).
Filtro EXPLICITO de `organization_id` en AMBAS tablas del join
(docs/skills/tenant-isolation.md §"Queries con join/agregacion").
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from uuid import UUID

from fastapi import Depends
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from adminprop.db.session import get_tenant_db_session
from adminprop.modules.maintenance.models import WorkOrder, WorkOrderQuote


@dataclass(frozen=True)
class WorkOrderWithAddress:
    """Fila cruda del join `work_orders -> properties` -- CA-06-01: "lo ve
    en su listado con la direccion de la propiedad"."""

    id: UUID
    organization_id: UUID
    property_id: UUID
    property_address: str
    title: str
    description: str | None
    payer: str
    status: str
    final_cost: Decimal | None
    approved_quote_id: UUID | None
    created_by: UUID
    closed_at: datetime | None
    created_at: datetime
    updated_at: datetime


_WORK_ORDER_WITH_ADDRESS_SQL = """
    SELECT
        wo.id AS id,
        wo.organization_id AS organization_id,
        wo.property_id AS property_id,
        p.address AS property_address,
        wo.title AS title,
        wo.description AS description,
        wo.payer AS payer,
        wo.status AS status,
        wo.final_cost AS final_cost,
        wo.approved_quote_id AS approved_quote_id,
        wo.created_by AS created_by,
        wo.closed_at AS closed_at,
        wo.created_at AS created_at,
        wo.updated_at AS updated_at
    FROM work_orders wo
    JOIN properties p ON p.id = wo.property_id AND p.organization_id = :org_id
    WHERE wo.organization_id = :org_id AND wo.deleted_at IS NULL
"""


class WorkOrderRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    @property
    def session(self) -> AsyncSession:
        """Expuesto para que `service.py` reutilice la MISMA sesion al
        auditar/notificar en la misma transaccion (CA-NT-02, mismo
        criterio que `modules/payments/repository.py.session`)."""
        return self._session

    async def property_exists(self, property_id: UUID, organization_id: UUID) -> bool:
        """RN-D01: usado para devolver 404 (no 500) si `property_id` no
        existe o es de otro tenant al crear un pedido, o al pedir el
        historial de una propiedad (`GET /properties/:id/work-orders`)."""
        result = await self._session.execute(
            text(
                "SELECT 1 FROM properties "
                "WHERE id = :property_id AND organization_id = :org_id AND deleted_at IS NULL"
            ),
            {"property_id": str(property_id), "org_id": str(organization_id)},
        )
        return result.first() is not None

    async def insert(
        self,
        *,
        organization_id: UUID,
        property_id: UUID,
        title: str,
        description: str | None,
        payer: str,
        created_by: UUID,
    ) -> WorkOrder:
        row = WorkOrder(
            organization_id=organization_id,
            property_id=property_id,
            title=title,
            description=description,
            payer=payer,
            created_by=created_by,
        )
        self._session.add(row)
        await self._session.flush()
        await self._session.refresh(row)
        return row

    async def get_by_id(self, work_order_id: UUID, organization_id: UUID) -> WorkOrder | None:
        """RN-D01: filtro explicito de `organization_id` -- usado por las
        transiciones de estado (quote/approve/close/cancel), que
        necesitan el ORM row para mutarlo."""
        stmt = select(WorkOrder).where(
            WorkOrder.id == work_order_id,
            WorkOrder.organization_id == organization_id,
            WorkOrder.deleted_at.is_(None),
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_with_address(
        self, work_order_id: UUID, organization_id: UUID
    ) -> WorkOrderWithAddress | None:
        """RF-06/CA-06-01: `GET /work-orders/:id` con la direccion ya
        resuelta -- misma forma que `list_with_address`."""
        sql = _WORK_ORDER_WITH_ADDRESS_SQL + " AND wo.id = :work_order_id"
        result = await self._session.execute(
            text(sql), {"org_id": str(organization_id), "work_order_id": str(work_order_id)}
        )
        row = result.mappings().first()
        return WorkOrderWithAddress(**dict(row)) if row is not None else None

    async def list_with_address(
        self,
        *,
        organization_id: UUID,
        status: str | None = None,
        property_id: UUID | None = None,
    ) -> list[WorkOrderWithAddress]:
        """sdd_03 §12: `GET /work-orders?status=&property_id=` --
        "maintenance ve todos los de la org" (RN-03/CA-06-01): el
        listado es el mismo para los tres roles, la diferenciacion la
        hace el permiso `work-order:read` (todos lo tienen) y el shape de
        respuesta (sin datos de contrato/inquilino/cobros/liquidacion,
        que este modulo nunca incluye)."""
        conditions: list[str] = []
        params: dict[str, object] = {"org_id": str(organization_id)}
        if status is not None:
            conditions.append("wo.status = :status")
            params["status"] = status
        if property_id is not None:
            conditions.append("wo.property_id = :property_id")
            params["property_id"] = str(property_id)

        sql = _WORK_ORDER_WITH_ADDRESS_SQL
        if conditions:
            sql += " AND " + " AND ".join(conditions)
        sql += " ORDER BY wo.created_at DESC, wo.id DESC"

        result = await self._session.execute(text(sql), params)
        return [WorkOrderWithAddress(**dict(row)) for row in result.mappings()]

    async def history_by_property(
        self, property_id: UUID, organization_id: UUID
    ) -> list[WorkOrderWithAddress]:
        """RF-06/CA-06-05: `GET /properties/:id/work-orders` -- todas las
        reparaciones de la propiedad (abiertas, cerradas o canceladas)."""
        sql = (
            _WORK_ORDER_WITH_ADDRESS_SQL
            + " AND wo.property_id = :property_id ORDER BY wo.created_at DESC"
        )
        result = await self._session.execute(
            text(sql), {"org_id": str(organization_id), "property_id": str(property_id)}
        )
        return [WorkOrderWithAddress(**dict(row)) for row in result.mappings()]

    async def mark_in_progress(
        self,
        work_order_id: UUID,
        organization_id: UUID,
        *,
        approved_quote_id: UUID,
        final_cost: Decimal,
    ) -> WorkOrder | None:
        """RF-03/CA-06-03: `open -> in_progress` al aprobar una
        cotizacion."""
        row = await self.get_by_id(work_order_id, organization_id)
        if row is None:  # pragma: no cover -- defensivo, el service ya valido existencia
            return None
        row.status = "in_progress"
        row.approved_quote_id = approved_quote_id
        row.final_cost = final_cost
        await self._session.flush()
        await self._session.refresh(row)
        return row

    async def mark_closed(
        self, work_order_id: UUID, organization_id: UUID, *, final_cost: Decimal, closed_at
    ) -> WorkOrder | None:
        """RF-04/CA-06-04: cierre con costo final (ajustable)."""
        row = await self.get_by_id(work_order_id, organization_id)
        if row is None:  # pragma: no cover -- defensivo, el service ya valido existencia
            return None
        row.status = "closed"
        row.final_cost = final_cost
        row.closed_at = closed_at
        await self._session.flush()
        await self._session.refresh(row)
        return row

    async def mark_cancelled(self, work_order_id: UUID, organization_id: UUID) -> WorkOrder | None:
        """RF-05: cancelacion -- el motivo se audita (`audit()`, no hay
        columna dedicada en `work_orders`, mismo criterio que
        `payments.void_payment`/`reason`)."""
        row = await self.get_by_id(work_order_id, organization_id)
        if row is None:  # pragma: no cover -- defensivo, el service ya valido existencia
            return None
        row.status = "cancelled"
        await self._session.flush()
        await self._session.refresh(row)
        return row

    async def commit(self) -> None:
        await self._session.commit()


class WorkOrderQuoteRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    @property
    def session(self) -> AsyncSession:
        return self._session

    async def insert(
        self,
        *,
        organization_id: UUID,
        work_order_id: UUID,
        amount: Decimal,
        description: str | None,
        submitted_by: UUID,
    ) -> WorkOrderQuote:
        row = WorkOrderQuote(
            organization_id=organization_id,
            work_order_id=work_order_id,
            amount=amount,
            description=description,
            submitted_by=submitted_by,
        )
        self._session.add(row)
        await self._session.flush()
        await self._session.refresh(row)
        return row

    async def get_by_id(self, quote_id: UUID, organization_id: UUID) -> WorkOrderQuote | None:
        """RN-D01: filtro explicito de `organization_id` -- usado por
        `POST /quotes/:id/approve` y `POST /quotes/:id/attachments`."""
        stmt = select(WorkOrderQuote).where(
            WorkOrderQuote.id == quote_id,
            WorkOrderQuote.organization_id == organization_id,
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def list_by_work_order(
        self, work_order_id: UUID, organization_id: UUID
    ) -> list[WorkOrderQuote]:
        """RF-02: "todas quedan visibles con autor y fecha" -- usado por
        `GET /work-orders/:id`."""
        stmt = (
            select(WorkOrderQuote)
            .where(
                WorkOrderQuote.work_order_id == work_order_id,
                WorkOrderQuote.organization_id == organization_id,
            )
            .order_by(WorkOrderQuote.created_at.asc())
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def has_approved(self, work_order_id: UUID, organization_id: UUID) -> bool:
        """RN-02/CA-06-03: chequeo app-level ANTES del INSERT/UPDATE --
        la red de seguridad real es el indice parcial unico
        `idx_work_order_quotes_one_approved_per_order` (migracion #25),
        mismo criterio que `ContractOverlapException` con el EXCLUDE de
        `contracts` (validacion de UX antes que la DB reviente)."""
        stmt = select(WorkOrderQuote.id).where(
            WorkOrderQuote.work_order_id == work_order_id,
            WorkOrderQuote.organization_id == organization_id,
            WorkOrderQuote.status == "approved",
        )
        result = await self._session.execute(stmt)
        return result.first() is not None

    async def approve(self, quote_id: UUID, organization_id: UUID) -> WorkOrderQuote | None:
        row = await self.get_by_id(quote_id, organization_id)
        if row is None:  # pragma: no cover -- defensivo, el service ya valido existencia
            return None
        row.status = "approved"
        await self._session.flush()
        await self._session.refresh(row)
        return row

    async def discard_others(
        self, work_order_id: UUID, organization_id: UUID, *, except_quote_id: UUID
    ) -> None:
        """RF-03: "las demas cotizaciones quedan `discarded`"."""
        stmt = select(WorkOrderQuote).where(
            WorkOrderQuote.work_order_id == work_order_id,
            WorkOrderQuote.organization_id == organization_id,
            WorkOrderQuote.id != except_quote_id,
            WorkOrderQuote.status == "submitted",
        )
        result = await self._session.execute(stmt)
        for quote in result.scalars().all():
            quote.status = "discarded"
        await self._session.flush()

    async def commit(self) -> None:
        await self._session.commit()


def get_work_order_repository(
    session: AsyncSession = Depends(get_tenant_db_session),
) -> WorkOrderRepository:
    return WorkOrderRepository(session)


def get_work_order_quote_repository(
    session: AsyncSession = Depends(get_tenant_db_session),
) -> WorkOrderQuoteRepository:
    return WorkOrderQuoteRepository(session)
