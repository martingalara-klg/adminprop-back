"""Logica de negocio del modulo personas (issue #13).

SDD: docs/sdd/features/spec_module_02_personas.md RF-01..RF-03.
Implements: CA-02-01, 02, 03, 04, 06 (RN-D01, RN-D02, RN-D04, RN-L05).
"""

from __future__ import annotations

from decimal import Decimal
from uuid import UUID

from fastapi import Depends

from adminprop.modules.people.models import Renter
from adminprop.modules.people.repository import (
    LandlordFields,
    LandlordRepository,
    RenterRepository,
    get_landlord_repository,
    get_renter_repository,
)
from adminprop.shared.audit.service import audit
from adminprop.shared.errors.codes import (
    EntityHasActiveContractException,
    EntityHasDependenciesException,
    ForbiddenException,
    NotFoundException,
)

# sdd_03 v1.5 §"Catalogo de Permisos" (issue #51): permiso atomico
# dedicado -- reemplaza el chequeo previo por nombre de rol
# (`payload.role != "owner"`). El check se hace aca (no via
# `Depends(requires_permission(...))` en el router) porque solo aplica
# CONDICIONALMENTE, cuando `commission_pct` viene en el PATCH -- el resto
# de los campos del mismo endpoint solo requiere `landlord:manage`
# (ya exigido por la dependency del router).
_SET_COMMISSION_PERMISSION = "landlord:set-commission"


class LandlordService:
    """RF-01 (ABM propietarios) + RF-02 (ficha)."""

    def __init__(self, repo: LandlordRepository) -> None:
        self._repo = repo

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
        """RF-01 + CA-02-01: `commission_pct` obligatorio desde el alta
        (ya lo exige `LandlordCreate` de Pydantic, `Field(...)`). Sin
        restriccion de rol en la creacion -- CA-02-02 solo restringe el
        CAMBIO posterior via PATCH (el owner que da de alta ya fija el %
        que quiere; RF-01 no dice que el alta sea owner-only)."""
        landlord = await self._repo.create(
            organization_id=organization_id,
            name=name,
            tax_id=tax_id,
            phone=phone,
            email=email,
            bank_info=bank_info,
            commission_pct=commission_pct,
            notes=notes,
        )
        await self._repo.commit()
        return landlord

    async def get(self, landlord_id: UUID, organization_id: UUID) -> LandlordFields | None:
        return await self._repo.get_by_id(landlord_id, organization_id)

    async def list(
        self, *, organization_id: UUID, cursor: str | None, limit: int
    ) -> tuple[list, str | None]:
        """CA-02-04: el caller (router) serializa estas filas con
        `LandlordSummary` -- ningun campo `bank_info` viaja aca (nunca se
        descifra para el listado)."""
        return await self._repo.list(organization_id=organization_id, cursor=cursor, limit=limit)

    async def update(
        self,
        landlord_id: UUID,
        organization_id: UUID,
        *,
        name: str | None,
        tax_id: str | None,
        phone: str | None,
        email: str | None,
        bank_info: str | None,
        commission_pct: Decimal | None,
        notes: str | None,
        actor_user_id: UUID,
        actor_permissions: frozenset[str],
        fields_set: set[str],
    ) -> LandlordFields | None:
        """RF-01: PATCH parcial.

        CA-02-02: `commission_pct` (si vino en el request, `"commission_pct"
        in fields_set`) SOLO lo puede tocar quien tenga el permiso atomico
        `landlord:set-commission` (sdd_03 v1.5, issue #51 -- reemplaza el
        chequeo previo por nombre de rol, `CLAUDE.md` §6: "chequeo por
        permiso atomico, nunca por nombre de rol"). En el seed de roles
        (`superadmin/provisioning.py`) solo `owner` lo tiene; un `admin`
        que incluya el campo recibe 403 FORBIDDEN, sin importar el valor
        (mismo % o distinto: "recibe 403 FORBIDDEN al intentar cambiar",
        literal del CA). Todos los demas campos ("datos de contacto") son
        editables por cualquier actor con `landlord:manage` (ya exigido
        por la dependency del router).

        RN-D04/RN-L05: el cambio de `commission_pct` queda auditado con
        valor anterior/nuevo, en la MISMA transaccion que el UPDATE.
        """
        if "commission_pct" in fields_set and _SET_COMMISSION_PERMISSION not in actor_permissions:
            raise ForbiddenException(
                message="Solo el owner puede cambiar el porcentaje de comision.",
                field="commission_pct",
            )

        current_commission_pct: Decimal | None = None
        if "commission_pct" in fields_set:
            current_commission_pct = await self._repo.get_commission_pct(
                landlord_id, organization_id
            )
            if current_commission_pct is None:
                raise NotFoundException()

        update_fields: dict[str, object] = {}
        if "name" in fields_set:
            update_fields["name"] = name
        if "tax_id" in fields_set:
            update_fields["tax_id"] = tax_id
        if "phone" in fields_set:
            update_fields["phone"] = phone
        if "email" in fields_set:
            update_fields["email"] = email
        if "bank_info" in fields_set:
            update_fields["bank_info"] = (
                await self._repo.encrypt_bank_info(bank_info) if bank_info is not None else None
            )
        if "commission_pct" in fields_set:
            update_fields["commission_pct"] = commission_pct
        if "notes" in fields_set:
            update_fields["notes"] = notes

        updated = await self._repo.update(landlord_id, organization_id, fields=update_fields)
        if updated is None:
            raise NotFoundException()

        if (
            "commission_pct" in fields_set
            and current_commission_pct is not None
            and commission_pct != current_commission_pct
        ):
            # RN-D04/RN-L05: auditar el cambio, en la MISMA transaccion
            # que el UPDATE de arriba -- se persiste junto con el
            # `commit()` de abajo.
            await audit(
                self._repo.session,
                organization_id=organization_id,
                action="landlord.commission_pct_changed",
                entity_type="landlord",
                entity_id=landlord_id,
                before={"commission_pct": str(current_commission_pct)},
                after={"commission_pct": str(commission_pct)},
                user_id=actor_user_id,
            )

        await self._repo.commit()
        return updated

    async def delete(self, landlord_id: UUID, organization_id: UUID) -> None:
        """RF-01 + CA-02-06: soft delete; `409 ENTITY_HAS_DEPENDENCIES`
        si hay propiedades activas (ver
        `LandlordRepository.has_active_dependencies` para el alcance
        actual -- siempre `False` hasta que exista el modulo
        `properties`, issue #15)."""
        existing = await self._repo.get_by_id(landlord_id, organization_id)
        if existing is None:
            raise NotFoundException()

        if await self._repo.has_active_dependencies(landlord_id, organization_id):
            raise EntityHasDependenciesException(
                details={"entity_type": "landlord", "entity_id": str(landlord_id)}
            )

        await self._repo.soft_delete(landlord_id, organization_id)
        await self._repo.commit()


def get_landlord_service(
    repo: LandlordRepository = Depends(get_landlord_repository),
) -> LandlordService:
    return LandlordService(repo)


class RenterService:
    """RF-03 (ABM inquilinos)."""

    def __init__(self, repo: RenterRepository) -> None:
        self._repo = repo

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
        renter = await self._repo.create(
            organization_id=organization_id,
            name=name,
            tax_id=tax_id,
            phone=phone,
            email=email,
            notes=notes,
        )
        await self._repo.commit()
        return renter

    async def get(self, renter_id: UUID, organization_id: UUID) -> Renter | None:
        return await self._repo.get_by_id(renter_id, organization_id)

    async def list(
        self, *, organization_id: UUID, cursor: str | None, limit: int
    ) -> tuple[list[Renter], str | None]:
        return await self._repo.list(organization_id=organization_id, cursor=cursor, limit=limit)

    async def update(
        self,
        renter_id: UUID,
        organization_id: UUID,
        *,
        name: str | None,
        tax_id: str | None,
        phone: str | None,
        email: str | None,
        notes: str | None,
        fields_set: set[str],
    ) -> Renter | None:
        update_fields: dict[str, object] = {}
        if "name" in fields_set:
            update_fields["name"] = name
        if "tax_id" in fields_set:
            update_fields["tax_id"] = tax_id
        if "phone" in fields_set:
            update_fields["phone"] = phone
        if "email" in fields_set:
            update_fields["email"] = email
        if "notes" in fields_set:
            update_fields["notes"] = notes

        updated = await self._repo.update(renter_id, organization_id, fields=update_fields)
        if updated is None:
            raise NotFoundException()
        await self._repo.commit()
        return updated

    async def delete(self, renter_id: UUID, organization_id: UUID, *, actor_user_id: UUID) -> None:
        """RF-03 + CA-02-06/08/09 (issue #124, decision #130): soft delete
        (RN-D02/RN-D05). Con contrato `active` (no eliminado) -> `422
        ENTITY_HAS_ACTIVE_CONTRACT` con `details.active_contracts[]`
        (reemplaza el 409 ENTITY_HAS_DEPENDENCIES que este caso devolvia
        hasta sdd_03 v1.16); contratos `draft`/`expired`/`terminated` NO
        bloquean. Sin contrato activo: baja logica auditada
        (`renter.deleted`) -- la trazabilidad queda intacta: contratos
        historicos, cobros, liquidaciones y auditoria siguen
        referenciandolo (RN-12), y la deuda de sus contratos historicos
        no eliminados sigue computandose (RN-C05)."""
        existing = await self._repo.get_by_id(renter_id, organization_id)
        if existing is None:
            raise NotFoundException()

        # RN-D05: solo un contrato `active` bloquea la baja.
        active_contracts = await self._repo.list_active_contracts(renter_id, organization_id)
        if active_contracts:
            raise EntityHasActiveContractException(
                details={
                    "entity_type": "renter",
                    "entity_id": str(renter_id),
                    "active_contracts": [ref.to_details() for ref in active_contracts],
                }
            )

        await self._repo.soft_delete(renter_id, organization_id)
        # RN-D05: baja logica auditada, en la MISMA transaccion que el
        # UPDATE de `deleted_at` (mismo criterio que `property.deleted`).
        await audit(
            self._repo.session,
            organization_id=organization_id,
            action="renter.deleted",
            entity_type="renter",
            entity_id=renter_id,
            after={"deleted": True},
            user_id=actor_user_id,
        )
        await self._repo.commit()


def get_renter_service(
    repo: RenterRepository = Depends(get_renter_repository),
) -> RenterService:
    return RenterService(repo)
