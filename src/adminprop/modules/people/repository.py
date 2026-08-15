"""Acceso a datos de `landlords` y `renters` (issue #13).

SDD: infrastructure/spec_data_model.md §Capa 1. docs/skills/tenant-isolation.md
("todo metodo del repository recibe organization_id como parametro y lo
aplica en el WHERE" -- defense in depth sobre RLS, RN-D01).

`bank_info` nunca se cifra/descifra aca a mano: delega en
`shared/encryption/pgcrypto.py` (`encrypt_value`/`decrypt_value`, pgcrypto
AES-256, sdd_04 §2.4). El repository pasa la MISMA sesion que usa para el
resto de la query -- ninguna transaccion propia.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID

from fastapi import Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from adminprop.db.session import get_tenant_db_session
from adminprop.modules.people.models import Landlord, Renter
from adminprop.shared.encryption.pgcrypto import decrypt_value, encrypt_value


def _encode_cursor(created_at: datetime, row_id: UUID) -> str:
    raw = f"{created_at.isoformat()}|{row_id}"
    return base64.urlsafe_b64encode(raw.encode("utf-8")).decode("ascii")


def _decode_cursor(cursor: str) -> tuple[datetime, UUID]:
    raw = base64.urlsafe_b64decode(cursor.encode("ascii")).decode("utf-8")
    created_at_raw, row_id_raw = raw.split("|", 1)
    return datetime.fromisoformat(created_at_raw), UUID(row_id_raw)


@dataclass(frozen=True)
class LandlordFields:
    """Campos de negocio de un `Landlord` en texto plano -- `bank_info`
    aca SIEMPRE es plaintext (nunca el BYTEA cifrado); el repository es
    el unico punto que ve el ciphertext."""

    id: UUID
    name: str
    tax_id: str | None
    phone: str | None
    email: str | None
    bank_info: str | None
    commission_pct: Decimal
    notes: str | None
    created_at: datetime
    updated_at: datetime


async def _decrypt_bank_info(session: AsyncSession, ciphertext: bytes | None) -> str | None:
    if ciphertext is None:
        return None
    return await decrypt_value(session, ciphertext)


async def _to_landlord_fields(session: AsyncSession, row: Landlord) -> LandlordFields:
    return LandlordFields(
        id=row.id,
        name=row.name,
        tax_id=row.tax_id,
        phone=row.phone,
        email=row.email,
        bank_info=await _decrypt_bank_info(session, row.bank_info),
        commission_pct=row.commission_pct,
        notes=row.notes,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


class LandlordRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    @property
    def session(self) -> AsyncSession:
        """Expuesto para que `service.py` pase la MISMA sesion a
        `AuditService.audit()` (mismo criterio que
        `modules/administracion/repository.py.session`) -- el evento de
        auditoria del cambio de `commission_pct` (CA-02-02) debe
        persistirse en la misma transaccion que el UPDATE."""
        return self._session

    async def create(
        self,
        *,
        organization_id: UUID,
        name: str,
        tax_id: str | None,
        phone: str | None,
        email: str | None,
        bank_info: str | None,
        commission_pct: Decimal,
        notes: str | None,
    ) -> LandlordFields:
        """RF-01: alta. `bank_info` se cifra ANTES de armar el INSERT --
        el ciphertext (BYTEA) es lo unico que toca la fila, nunca el
        texto plano (CA-02-04)."""
        encrypted_bank_info = (
            await encrypt_value(self._session, bank_info) if bank_info is not None else None
        )
        row = Landlord(
            organization_id=organization_id,
            name=name,
            tax_id=tax_id,
            phone=phone,
            email=email,
            bank_info=encrypted_bank_info,
            commission_pct=commission_pct,
            notes=notes,
        )
        self._session.add(row)
        await self._session.flush()
        await self._session.refresh(row)
        return await _to_landlord_fields(self._session, row)

    async def get_by_id(self, landlord_id: UUID, organization_id: UUID) -> LandlordFields | None:
        """RN-D01: filtro explicito de `organization_id` + `deleted_at
        IS NULL` -- un landlord de otra organizacion o ya dado de baja no
        se distingue de "no existe" (404)."""
        row = await self._get_row(landlord_id, organization_id)
        if row is None:
            return None
        return await _to_landlord_fields(self._session, row)

    async def get_commission_pct(self, landlord_id: UUID, organization_id: UUID) -> Decimal | None:
        """Lectura liviana (sin descifrar `bank_info`) usada por el
        service para decidir si el PATCH efectivamente cambia el %
        vigente (CA-02-02: auditar solo si hay cambio real)."""
        stmt = select(Landlord.commission_pct).where(
            Landlord.id == landlord_id,
            Landlord.organization_id == organization_id,
            Landlord.deleted_at.is_(None),
        )
        result = await self._session.execute(stmt)
        row = result.first()
        return row[0] if row is not None else None

    async def list(
        self, *, organization_id: UUID, cursor: str | None, limit: int
    ) -> tuple[list[Landlord], str | None]:
        """RF-02: listado paginado cursor-based. CA-02-04: NUNCA descifra
        `bank_info` aca -- el caller (service/router) serializa con
        `LandlordSummary`, que ni siquiera declara el campo."""
        stmt = select(Landlord).where(
            Landlord.organization_id == organization_id, Landlord.deleted_at.is_(None)
        )
        if cursor:
            cursor_created_at, cursor_id = _decode_cursor(cursor)
            stmt = stmt.where((Landlord.created_at, Landlord.id) < (cursor_created_at, cursor_id))
        stmt = stmt.order_by(Landlord.created_at.desc(), Landlord.id.desc()).limit(limit + 1)

        result = await self._session.execute(stmt)
        rows = list(result.scalars().all())

        has_more = len(rows) > limit
        page = rows[:limit]
        next_cursor = (
            _encode_cursor(page[-1].created_at, page[-1].id) if has_more and page else None
        )
        return page, next_cursor

    async def update(
        self,
        landlord_id: UUID,
        organization_id: UUID,
        *,
        fields: dict[str, object],
    ) -> LandlordFields | None:
        """RF-01/RF-02: PATCH parcial. `fields` ya viene resuelto por el
        service (incluye `bank_info` cifrado si vino en el request, o
        ausente si no)."""
        row = await self._get_row(landlord_id, organization_id)
        if row is None:
            return None
        for key, value in fields.items():
            setattr(row, key, value)
        await self._session.flush()
        await self._session.refresh(row)
        return await _to_landlord_fields(self._session, row)

    async def encrypt_bank_info(self, plaintext: str) -> bytes:
        return await encrypt_value(self._session, plaintext)

    async def soft_delete(self, landlord_id: UUID, organization_id: UUID) -> bool:
        """RF-01: baja logica (`deleted_at`). Devuelve `False` si no
        existe/ya esta borrado/es de otra organizacion (404, RN-D01)."""
        row = await self._get_row(landlord_id, organization_id)
        if row is None:
            return False
        row.deleted_at = datetime.now(UTC)
        await self._session.flush()
        return True

    async def has_active_dependencies(self, landlord_id: UUID, organization_id: UUID) -> bool:
        """CA-02-06: `409 ENTITY_HAS_DEPENDENCIES` si el propietario tiene
        propiedades activas.

        Implementacion deliberadamente extensible (issue #13, "Decisiones
        de implementacion" del PR): el modulo `properties` (issue #15,
        "Bloquea a" de este issue) todavia no existe -- hoy NO puede
        haber dependencias, asi que este metodo siempre retorna `False`.
        Cuando `properties` exista, reemplazar el cuerpo por un
        `SELECT EXISTS(... FROM properties WHERE landlord_id = :id AND
        organization_id = :org_id AND status = 'active' AND deleted_at IS
        NULL)` -- la firma (recibe `landlord_id` + `organization_id`,
        devuelve `bool`) ya queda lista para ese reemplazo sin tocar el
        caller (`service.delete`).
        """
        return False

    async def _get_row(self, landlord_id: UUID, organization_id: UUID) -> Landlord | None:
        stmt = select(Landlord).where(
            Landlord.id == landlord_id,
            Landlord.organization_id == organization_id,
            Landlord.deleted_at.is_(None),
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def commit(self) -> None:
        await self._session.commit()


def get_landlord_repository(
    session: AsyncSession = Depends(get_tenant_db_session),
) -> LandlordRepository:
    return LandlordRepository(session)


class RenterRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    @property
    def session(self) -> AsyncSession:
        return self._session

    async def create(
        self,
        *,
        organization_id: UUID,
        name: str,
        tax_id: str | None,
        phone: str | None,
        email: str | None,
        notes: str | None,
    ) -> Renter:
        row = Renter(
            organization_id=organization_id,
            name=name,
            tax_id=tax_id,
            phone=phone,
            email=email,
            notes=notes,
        )
        self._session.add(row)
        await self._session.flush()
        await self._session.refresh(row)
        return row

    async def get_by_id(self, renter_id: UUID, organization_id: UUID) -> Renter | None:
        return await self._get_row(renter_id, organization_id)

    async def list(
        self, *, organization_id: UUID, cursor: str | None, limit: int
    ) -> tuple[list[Renter], str | None]:
        stmt = select(Renter).where(
            Renter.organization_id == organization_id, Renter.deleted_at.is_(None)
        )
        if cursor:
            cursor_created_at, cursor_id = _decode_cursor(cursor)
            stmt = stmt.where((Renter.created_at, Renter.id) < (cursor_created_at, cursor_id))
        stmt = stmt.order_by(Renter.created_at.desc(), Renter.id.desc()).limit(limit + 1)

        result = await self._session.execute(stmt)
        rows = list(result.scalars().all())

        has_more = len(rows) > limit
        page = rows[:limit]
        next_cursor = (
            _encode_cursor(page[-1].created_at, page[-1].id) if has_more and page else None
        )
        return page, next_cursor

    async def update(
        self, renter_id: UUID, organization_id: UUID, *, fields: dict[str, object]
    ) -> Renter | None:
        row = await self._get_row(renter_id, organization_id)
        if row is None:
            return None
        for key, value in fields.items():
            setattr(row, key, value)
        await self._session.flush()
        await self._session.refresh(row)
        return row

    async def soft_delete(self, renter_id: UUID, organization_id: UUID) -> bool:
        row = await self._get_row(renter_id, organization_id)
        if row is None:
            return False
        row.deleted_at = datetime.now(UTC)
        await self._session.flush()
        return True

    async def has_active_dependencies(self, renter_id: UUID, organization_id: UUID) -> bool:
        """CA-02-06: `409 ENTITY_HAS_DEPENDENCIES` si el inquilino tiene
        contrato vigente. Mismo criterio de extensibilidad que
        `LandlordRepository.has_active_dependencies`: el modulo
        `contracts` no existe todavia -- siempre `False` hasta entonces.
        """
        return False

    async def _get_row(self, renter_id: UUID, organization_id: UUID) -> Renter | None:
        stmt = select(Renter).where(
            Renter.id == renter_id,
            Renter.organization_id == organization_id,
            Renter.deleted_at.is_(None),
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def commit(self) -> None:
        await self._session.commit()


def get_renter_repository(
    session: AsyncSession = Depends(get_tenant_db_session),
) -> RenterRepository:
    return RenterRepository(session)
