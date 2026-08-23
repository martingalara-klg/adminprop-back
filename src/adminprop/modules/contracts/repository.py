"""Acceso a datos de `contracts` (issue #17).

SDD: infrastructure/spec_data_model.md §Capa 3. docs/skills/tenant-isolation.md
("todo metodo del repository recibe organization_id como parametro y lo
aplica en el WHERE" -- defense in depth sobre RLS, RN-D01).

`property_exists`/`renter_exists` importan modelos de otros modulos --
mismo patron que `modules/properties/repository.py.landlord_exists`
importa `Landlord` de `modules/people`.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from uuid import UUID

from fastapi import Depends
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from adminprop.db.session import get_tenant_db_session
from adminprop.modules.contracts.models import Contract
from adminprop.modules.people.models import Renter
from adminprop.modules.properties.models import Property

# spec_data_model.md §"Estrategia de Seed Data" (modules/superadmin/provisioning.py.
# DEFAULT_ORGANIZATION_SETTINGS): piso de seguridad si `organizations.settings`
# no trae la clave todavia (defensivo -- toda organizacion nueva la trae desde
# el provisioning del issue #7).
_DEFAULT_CONTRACT_EXPIRY_NOTICE_DAYS = 60


def _encode_cursor(created_at: datetime, row_id: UUID) -> str:
    raw = f"{created_at.isoformat()}|{row_id}"
    return base64.urlsafe_b64encode(raw.encode("utf-8")).decode("ascii")


def _decode_cursor(cursor: str) -> tuple[datetime, UUID]:
    raw = base64.urlsafe_b64decode(cursor.encode("ascii")).decode("utf-8")
    created_at_raw, row_id_raw = raw.split("|", 1)
    return datetime.fromisoformat(created_at_raw), UUID(row_id_raw)


@dataclass(frozen=True)
class ContractFilters:
    """RF-01: "Listado con filtros: estado, propiedad, inquilino, moneda,
    expiring_in_days". `propietario` (via propiedad) queda fuera de
    alcance de este PR -- requeriria un join con `properties`/`landlords`
    que ningun CA de este issue ejercita; se declara la limitacion aca en
    vez de construirlo especulativamente (YAGNI)."""

    status: str | None = None
    property_id: UUID | None = None
    renter_id: UUID | None = None
    currency: str | None = None
    expiring_in_days: int | None = None


class ContractRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    @property
    def session(self) -> AsyncSession:
        """Expuesto para que `service.py` pase la MISMA sesion a
        `AuditService.audit()` -- mismo criterio que
        `modules/properties/repository.py.PropertyRepository.session`."""
        return self._session

    async def property_exists(self, property_id: UUID, organization_id: UUID) -> bool:
        """RN-06: la propiedad referenciada debe existir, pertenecer al
        mismo tenant y no estar borrada -- cross-tenant se trata igual
        que "no existe" (RN-D01)."""
        stmt = select(Property.id).where(
            Property.id == property_id,
            Property.organization_id == organization_id,
            Property.deleted_at.is_(None),
        )
        result = await self._session.execute(stmt)
        return result.first() is not None

    async def renter_exists(self, renter_id: UUID, organization_id: UUID) -> bool:
        """RN-06: el inquilino referenciado debe existir, pertenecer al
        mismo tenant y no estar borrado (RN-D01)."""
        stmt = select(Renter.id).where(
            Renter.id == renter_id,
            Renter.organization_id == organization_id,
            Renter.deleted_at.is_(None),
        )
        result = await self._session.execute(stmt)
        return result.first() is not None

    async def find_overlapping_active_contract(
        self,
        *,
        property_id: UUID,
        organization_id: UUID,
        start_date: date,
        end_date: date,
        exclude_contract_id: UUID | None = None,
    ) -> Contract | None:
        """RN-01/RN-C01, CA-03-02: validacion app-level de no-solapamiento
        ANTES del EXCLUDE de DB (que es la red de seguridad, no la UX).
        Mismo criterio de solapamiento inclusivo ('[]') que el constraint
        `contracts_no_overlap` de la migracion #16: dos rangos se
        solapan si `start1 <= end2 AND start2 <= end1`."""
        stmt = select(Contract).where(
            Contract.organization_id == organization_id,
            Contract.property_id == property_id,
            Contract.status == "active",
            Contract.deleted_at.is_(None),
            Contract.start_date <= end_date,
            Contract.end_date >= start_date,
        )
        if exclude_contract_id is not None:
            stmt = stmt.where(Contract.id != exclude_contract_id)
        result = await self._session.execute(stmt)
        return result.scalars().first()

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
    ) -> Contract:
        # RN-02: todo contrato nace `draft`; `current_amount` arranca
        # igual a `initial_amount` (RF-02, sdd_02 §2.7).
        row = Contract(
            organization_id=organization_id,
            property_id=property_id,
            renter_id=renter_id,
            currency=currency,
            initial_amount=initial_amount,
            current_amount=initial_amount,
            start_date=start_date,
            end_date=end_date,
            daily_late_fee_pct=daily_late_fee_pct,
            adjustment_frequency_months=adjustment_frequency_months,
            adjustment_index=adjustment_index,
            adjustment_index_notes=adjustment_index_notes,
            status="draft",
            notes=notes,
        )
        self._session.add(row)
        await self._session.flush()
        await self._session.refresh(row)
        return row

    async def get_by_id(self, contract_id: UUID, organization_id: UUID) -> Contract | None:
        return await self._get_row(contract_id, organization_id)

    async def list(
        self,
        *,
        organization_id: UUID,
        cursor: str | None,
        limit: int,
        filters: ContractFilters,
    ) -> tuple[list[Contract], str | None]:
        stmt = select(Contract).where(
            Contract.organization_id == organization_id, Contract.deleted_at.is_(None)
        )
        if filters.status is not None:
            stmt = stmt.where(Contract.status == filters.status)
        if filters.property_id is not None:
            stmt = stmt.where(Contract.property_id == filters.property_id)
        if filters.renter_id is not None:
            stmt = stmt.where(Contract.renter_id == filters.renter_id)
        if filters.currency is not None:
            stmt = stmt.where(Contract.currency == filters.currency)
        if filters.expiring_in_days is not None:
            # RF-01/RF-05: "vence dentro de N dias" -- contratos activos
            # cuyo end_date cae en [hoy, hoy + N].
            today = datetime.now(UTC).date()
            threshold = today + timedelta(days=filters.expiring_in_days)
            stmt = stmt.where(
                Contract.status == "active",
                Contract.end_date >= today,
                Contract.end_date <= threshold,
            )
        if cursor:
            cursor_created_at, cursor_id = _decode_cursor(cursor)
            stmt = stmt.where((Contract.created_at, Contract.id) < (cursor_created_at, cursor_id))
        stmt = stmt.order_by(Contract.created_at.desc(), Contract.id.desc()).limit(limit + 1)

        result = await self._session.execute(stmt)
        rows = list(result.scalars().all())

        has_more = len(rows) > limit
        page = rows[:limit]
        next_cursor = (
            _encode_cursor(page[-1].created_at, page[-1].id) if has_more and page else None
        )
        return page, next_cursor

    async def update(
        self, contract_id: UUID, organization_id: UUID, *, fields: dict[str, object]
    ) -> Contract | None:
        row = await self._get_row(contract_id, organization_id)
        if row is None:  # pragma: no cover -- defensivo, service ya valido existencia
            return None
        for key, value in fields.items():
            setattr(row, key, value)
        await self._session.flush()
        await self._session.refresh(row)
        return row

    async def has_active_contract_for_property(
        self, property_id: UUID, organization_id: UUID
    ) -> bool:
        """CA-01-03: usado por `PropertyRepository.has_active_dependencies`
        para bloquear el borrado de una propiedad con contrato `active`
        (409 ENTITY_HAS_DEPENDENCIES). Reemplaza el placeholder
        `siempre False` que dejo el issue #15."""
        stmt = select(Contract.id).where(
            Contract.property_id == property_id,
            Contract.organization_id == organization_id,
            Contract.status == "active",
            Contract.deleted_at.is_(None),
        )
        result = await self._session.execute(stmt)
        return result.first() is not None

    # ─── RF-03/RF-05 (issue #19): job diario `detect_expiring_contracts` ──

    async def get_expiry_notice_days(self, organization_id: UUID) -> int:
        """RF-05: lee `contract_expiry_notice_days` de `organizations.settings`
        (JSONB, issue #9) -- default 60 si la clave no esta presente
        (defensivo, ver comentario del modulo)."""
        stmt = text(
            "SELECT settings ->> 'contract_expiry_notice_days' FROM organizations "
            "WHERE id = :organization_id AND deleted_at IS NULL"
        )
        result = await self._session.execute(stmt, {"organization_id": str(organization_id)})
        raw_value = result.scalar_one_or_none()
        if raw_value is None:
            return _DEFAULT_CONTRACT_EXPIRY_NOTICE_DAYS
        return int(raw_value)

    # ─── RF-01 (issue #21): job mensual `generate_rent_periods` ────────────

    async def list_active(self, organization_id: UUID) -> list[Contract]:
        """CA-04-01: todo contrato `active` de la organizacion es candidato
        al `rent_period` del mes en curso (RN-C05/RN-07: `expired`/
        `terminated` no generan nuevos periodos, por eso no entran aca).
        Sin distincion de moneda -- USD tambien genera `rent_period`
        (RN-C: solo el AJUSTE automatico no aplica a USD, RN-C02)."""
        stmt = select(Contract).where(
            Contract.organization_id == organization_id,
            Contract.status == "active",
            Contract.deleted_at.is_(None),
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def list_active_past_end_date(
        self, organization_id: UUID, *, today: date
    ) -> list[Contract]:
        """RF-03: contratos `active` cuyo `end_date` ya paso -- candidatos
        a la transicion automatica `active -> expired` (RN-C05/RN-07)."""
        stmt = select(Contract).where(
            Contract.organization_id == organization_id,
            Contract.status == "active",
            Contract.end_date < today,
            Contract.deleted_at.is_(None),
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def list_active_due_for_expiry_notice(
        self, organization_id: UUID, *, today: date, notice_days: int
    ) -> list[Contract]:
        """RF-05/CA-03-07: contratos `active` que vencen dentro de
        `notice_days` (inclusive) y todavia no fueron notificados
        (`expiring_notified_at IS NULL` -- idempotencia, ver migracion
        20260819_123059). Contratos ya vencidos (candidatos a `expired`)
        no entran aca: el service corre esta consulta DESPUES de aplicar
        `list_active_past_end_date`, asi que a esta altura `end_date >=
        today` ya implica que siguen `active`."""
        threshold = today + timedelta(days=notice_days)
        stmt = select(Contract).where(
            Contract.organization_id == organization_id,
            Contract.status == "active",
            Contract.end_date >= today,
            Contract.end_date <= threshold,
            Contract.expiring_notified_at.is_(None),
            Contract.deleted_at.is_(None),
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def mark_expiring_notified(
        self, contract_id: UUID, organization_id: UUID, *, notified_at: datetime
    ) -> None:
        """CA-03-07: marca el aviso como enviado -- una sola vez por
        contrato (la ventana de umbral no reinicia la marca; si el umbral
        de la organizacion cambia y el contrato vuelve a caer dentro del
        rango, no se reenvia -- "una sola notificacion por contrato y
        umbral" se interpreta con el umbral vigente al momento del primer
        aviso, ver decision en el PR)."""
        row = await self._get_row(contract_id, organization_id)
        if row is None:  # pragma: no cover -- defensivo, el service ya listo el candidato
            return
        row.expiring_notified_at = notified_at
        await self._session.flush()

    async def _get_row(self, contract_id: UUID, organization_id: UUID) -> Contract | None:
        stmt = select(Contract).where(
            Contract.id == contract_id,
            Contract.organization_id == organization_id,
            Contract.deleted_at.is_(None),
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def commit(self) -> None:
        await self._session.commit()


def get_contract_repository(
    session: AsyncSession = Depends(get_tenant_db_session),
) -> ContractRepository:
    return ContractRepository(session)
