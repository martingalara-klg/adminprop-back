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
from decimal import Decimal
from uuid import UUID

from fastapi import Depends
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from adminprop.db.session import get_tenant_db_session
from adminprop.modules.contracts.models import Contract
from adminprop.modules.people.models import Renter
from adminprop.modules.properties.models import Neighborhood, Property

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
class ContractDisplayFields:
    """RN-12 (issue #123, `sdd_03` v1.16 §8): campos denormalizados de
    SOLO LECTURA de `ContractSummary`, resueltos por JOIN en el mismo
    query del repository (sin N+1 -- mismo criterio que la decision
    #127/issue #118). `property_neighborhood` es `None` si la propiedad
    no tiene barrio asignado (`neighborhood_id` nullable, issue #99)."""

    property_address: str
    property_neighborhood: str | None
    renter_name: str


@dataclass(frozen=True)
class ActiveContractRef:
    """RN-D05 (issue #124, decision #130): referencia de un contrato
    `active` que bloquea la baja de una propiedad o un inquilino -- va
    serializada en `details.active_contracts[]` del 422
    ENTITY_HAS_ACTIVE_CONTRACT (sdd_03 v1.17 §"Codigos de Error
    Globales") para que el front arme un mensaje legible (precedente
    CONTRACT_HAS_DEBT/issue #104)."""

    contract_id: UUID
    property_id: UUID
    property_address: str
    renter_id: UUID
    renter_name: str
    start_date: date
    end_date: date

    def to_details(self) -> dict[str, str]:
        return {
            "contract_id": str(self.contract_id),
            "property_id": str(self.property_id),
            "property_address": self.property_address,
            "renter_id": str(self.renter_id),
            "renter_name": self.renter_name,
            "start_date": self.start_date.isoformat(),
            "end_date": self.end_date.isoformat(),
        }


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
        current_amount: Decimal | None = None,
    ) -> Contract:
        # RN-02: todo contrato nace `draft`; `current_amount` arranca
        # igual a `initial_amount` (RF-02, sdd_02 §2.7) -- salvo alta de
        # contrato en curso (RN-08/RN-C06, issue #100), donde el caller
        # (`ContractService.create`) ya resolvio el monto vigente
        # declarado y lo pasa explicito aca.
        row = Contract(
            organization_id=organization_id,
            property_id=property_id,
            renter_id=renter_id,
            currency=currency,
            initial_amount=initial_amount,
            current_amount=current_amount if current_amount is not None else initial_amount,
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
    ) -> tuple[list[tuple[Contract, ContractDisplayFields]], str | None]:
        """RN-12 (issue #123): cada fila del listado viene enriquecida con
        `ContractDisplayFields` resuelto por JOIN en ESTE query -- un solo
        round-trip por pagina, sin N+1 (mismo criterio que la decision
        #127/issue #118). `properties`/`renters` se unen con INNER JOIN
        (FKs NOT NULL de la migracion #16 -- siempre existen) y
        `neighborhoods` con LEFT JOIN (`neighborhood_id` nullable, issue
        #99). Cada tabla unida repite el filtro explicito de
        `organization_id` en el ON (defense in depth sobre RLS, RN-D01).
        Sin filtro de `deleted_at` en las tablas unidas: el contrato
        sigue existiendo y muestra su referencia aunque el registro
        referenciado este soft-deleted (RN-12)."""
        stmt = (
            select(Contract, Property.address, Neighborhood.name, Renter.name)
            .join(
                Property,
                (Property.id == Contract.property_id)
                & (Property.organization_id == organization_id),
            )
            .outerjoin(
                Neighborhood,
                (Neighborhood.id == Property.neighborhood_id)
                & (Neighborhood.organization_id == organization_id),
            )
            .join(
                Renter,
                (Renter.id == Contract.renter_id) & (Renter.organization_id == organization_id),
            )
            .where(Contract.organization_id == organization_id, Contract.deleted_at.is_(None))
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
        rows = [
            (
                contract,
                ContractDisplayFields(
                    property_address=property_address,
                    property_neighborhood=neighborhood_name,
                    renter_name=renter_name,
                ),
            )
            for contract, property_address, neighborhood_name, renter_name in result.all()
        ]

        has_more = len(rows) > limit
        page = rows[:limit]
        next_cursor = (
            _encode_cursor(page[-1][0].created_at, page[-1][0].id) if has_more and page else None
        )
        return page, next_cursor

    async def get_display_fields(
        self, contract_id: UUID, organization_id: UUID
    ) -> ContractDisplayFields | None:
        """RN-12 (issue #123): resuelve los campos denormalizados de UN
        contrato -- usado por los endpoints de a un contrato (`POST`/
        `PATCH`/`activate`/`terminate`/`GET /contracts/:id`), que
        devuelven el mismo `ContractSummary` que el listado. Mismos JOIN
        y criterios que `list()` (ver docstring de arriba). `None` si el
        contrato no existe en el tenant (RN-D01)."""
        stmt = (
            select(Property.address, Neighborhood.name, Renter.name)
            .select_from(Contract)
            .join(
                Property,
                (Property.id == Contract.property_id)
                & (Property.organization_id == organization_id),
            )
            .outerjoin(
                Neighborhood,
                (Neighborhood.id == Property.neighborhood_id)
                & (Neighborhood.organization_id == organization_id),
            )
            .join(
                Renter,
                (Renter.id == Contract.renter_id) & (Renter.organization_id == organization_id),
            )
            .where(
                Contract.id == contract_id,
                Contract.organization_id == organization_id,
                Contract.deleted_at.is_(None),
            )
        )
        result = await self._session.execute(stmt)
        row = result.first()
        if row is None:
            return None
        property_address, neighborhood_name, renter_name = row
        return ContractDisplayFields(
            property_address=property_address,
            property_neighborhood=neighborhood_name,
            renter_name=renter_name,
        )

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

    async def soft_delete(self, contract_id: UUID, organization_id: UUID) -> Contract | None:
        """RN-C08/RN-13 (issue #124): baja logica (`deleted_at`, RN-D02) --
        nunca DELETE fisico. Devuelve `None` si no existe/ya esta
        eliminado/es de otra organizacion (404, RN-D01). A partir de aca
        TODA query operativa de este repository lo excluye via el filtro
        `deleted_at IS NULL` que ya aplican (`_get_row`, `list`,
        `list_active*`, `find_overlapping_active_contract`, etc.)."""
        row = await self._get_row(contract_id, organization_id)
        if row is None:
            return None
        row.deleted_at = datetime.now(UTC)
        await self._session.flush()
        return row

    async def list_active_contract_refs(
        self,
        organization_id: UUID,
        *,
        property_id: UUID | None = None,
        renter_id: UUID | None = None,
    ) -> list[ActiveContractRef]:
        """RN-D05 (issue #124): contratos `active` (no eliminados) que
        bloquean la baja de una propiedad o un inquilino, con las
        referencias de display resueltas por JOIN en el MISMO query
        (sin N+1, mismo criterio que `list()`/RN-12). Cada tabla unida
        repite el filtro explicito de `organization_id` (defense in
        depth sobre RLS, RN-D01)."""
        stmt = (
            select(
                Contract.id,
                Contract.property_id,
                Property.address,
                Contract.renter_id,
                Renter.name,
                Contract.start_date,
                Contract.end_date,
            )
            .join(
                Property,
                (Property.id == Contract.property_id)
                & (Property.organization_id == organization_id),
            )
            .join(
                Renter,
                (Renter.id == Contract.renter_id) & (Renter.organization_id == organization_id),
            )
            .where(
                Contract.organization_id == organization_id,
                Contract.status == "active",
                Contract.deleted_at.is_(None),
            )
            .order_by(Contract.start_date.asc(), Contract.id.asc())
        )
        if property_id is not None:
            stmt = stmt.where(Contract.property_id == property_id)
        if renter_id is not None:
            stmt = stmt.where(Contract.renter_id == renter_id)
        result = await self._session.execute(stmt)
        return [
            ActiveContractRef(
                contract_id=row[0],
                property_id=row[1],
                property_address=row[2],
                renter_id=row[3],
                renter_name=row[4],
                start_date=row[5],
                end_date=row[6],
            )
            for row in result.all()
        ]

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

    # ─── RF-06 (issue #106): serie mensual de valores locativos ────────────

    async def get_terminated_at(self, contract_id: UUID, organization_id: UUID) -> date | None:
        """RN-09: fecha efectiva de terminacion anticipada de un contrato
        `terminated`. `contracts` no tiene columna propia para esto
        (`service.py.terminate` solo persiste el motivo en `audit_logs`,
        no en la tabla) -- se deriva del evento `contract.terminated` MAS
        RECIENTE de ESE contrato, filtrado explicitamente por
        `organization_id` (defense in depth, mismo criterio que el resto
        de este repository). `None` si no existe (defensivo -- no deberia
        pasar: `terminate()` audita en la MISMA transaccion que el cambio
        de estado; el caller -- `monthly_amounts.compute_monthly_amounts`
        -- cae a `end_date` en ese caso)."""
        stmt = text(
            "SELECT created_at FROM audit_logs "
            "WHERE organization_id = :organization_id "
            "AND entity_type = 'contract' AND entity_id = :contract_id "
            "AND action = 'contract.terminated' "
            "ORDER BY created_at DESC LIMIT 1"
        )
        result = await self._session.execute(
            stmt, {"organization_id": str(organization_id), "contract_id": str(contract_id)}
        )
        row = result.scalar_one_or_none()
        if row is None:
            return None
        return row.date()

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
