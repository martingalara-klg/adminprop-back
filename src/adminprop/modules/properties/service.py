"""Logica de negocio del modulo propiedades (issue #15).

SDD: docs/sdd/features/spec_module_01_propiedades.md RF-01..RF-03.
Implements: CA-01-01, 02, 03, 06 (RN-D01, RN-D02, RN-D04).

Fuera de alcance (ver PR "Decisiones de implementacion"):
- CA-01-04 (estado `rented` automatico) y CA-01-05 (ficha con contrato
  vigente REAL) dependen del modulo `contracts` (issue #17).
- Historial de reparaciones (issue #26) y conceptos recurrentes (issue
  #28) en la ficha consolidada: placeholders vacios, ver `router.py`.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import Depends

from adminprop.modules.properties.repository import (
    NeighborhoodRepository,
    PropertyFilters,
    PropertyRepository,
    PropertyServiceAccountRepository,
    get_neighborhood_repository,
    get_property_repository,
    get_property_service_account_repository,
)
from adminprop.shared.audit.service import audit
from adminprop.shared.errors.codes import (
    ConflictException,
    EntityHasDependenciesException,
    InvalidStatusTransitionException,
    NotFoundException,
)


class PropertyService:
    """RF-01 (ABM propiedades) + RF-03 (ficha consolidada, parcial)."""

    def __init__(self, repo: PropertyRepository) -> None:
        self._repo = repo

    async def create(
        self,
        *,
        organization_id: UUID,
        landlord_id: UUID,
        neighborhood_id: UUID,
        address: str,
        property_type: str,
        notes: str | None,
    ):
        """RF-01 + CA-01-01: `landlord_id` obligatorio, validado contra el
        mismo tenant y no borrado (RN-D01) -- un `landlord_id` invalido o
        de otra organizacion es 404, con el mismo criterio de no-revelar-
        existencia que el resto del sistema.

        Issue #99 + CA-01-08: `neighborhood_id` obligatorio (ya lo exige
        `PropertyCreate` a nivel de Pydantic) y validado con el mismo
        criterio RN-D01 que `landlord_id`."""
        if not await self._repo.landlord_exists(landlord_id, organization_id):
            raise NotFoundException(
                message="El propietario indicado no existe.", field="landlord_id"
            )
        if not await self._repo.neighborhood_exists(neighborhood_id, organization_id):
            raise NotFoundException(
                message="El barrio indicado no existe.", field="neighborhood_id"
            )

        row = await self._repo.create(
            organization_id=organization_id,
            landlord_id=landlord_id,
            neighborhood_id=neighborhood_id,
            address=address,
            property_type=property_type,
            notes=notes,
        )
        await self._repo.commit()
        return row

    async def get(self, property_id: UUID, organization_id: UUID):
        return await self._repo.get_by_id(property_id, organization_id)

    async def list(
        self,
        *,
        organization_id: UUID,
        cursor: str | None,
        limit: int,
        filters: PropertyFilters,
    ) -> tuple[list, str | None]:
        return await self._repo.list(
            organization_id=organization_id, cursor=cursor, limit=limit, filters=filters
        )

    async def update(
        self,
        property_id: UUID,
        organization_id: UUID,
        *,
        address: str | None,
        landlord_id: UUID | None,
        neighborhood_id: UUID | None,
        property_type: str | None,
        status: str | None,
        notes: str | None,
        actor_user_id: UUID,
        fields_set: set[str],
    ):
        """RF-01: PATCH parcial. "Edicion de todos los campos salvo el
        estado `rented`" -- `status` en `PropertyUpdate` solo acepta
        `available`/`unavailable` a nivel de Pydantic (RF-04), asi que
        aca no hace falta re-validar el valor, solo aplicarlo.

        RN "Cambiar el propietario de una propiedad ... es una operacion
        auditada": si `landlord_id` viene en el request, se valida
        (mismo tenant, no borrado -- 404 si no) y el cambio queda
        auditado con propietario anterior/nuevo, en la MISMA transaccion
        que el UPDATE. El efecto sobre "a quien se liquida" (RN: "el
        cambio rige desde la proxima liquidacion") lo enforzara el modulo
        de liquidaciones (issue #28) cuando exista -- este servicio solo
        persiste el nuevo `landlord_id` y dispara el evento de auditoria.
        """
        current = await self._repo.get_by_id(property_id, organization_id)
        if current is None:
            raise NotFoundException()

        # RF-04 + issue #109: `available`/`unavailable` son estados
        # manuales validos SOLO sin contrato activo (CA-01-04:
        # `rented` <=> contrato `active`). Defensa en profundidad --
        # `has_active_dependencies` consulta la tabla `contracts`
        # directamente (fuente de verdad del invariante), no solo el
        # `status` cacheado en `properties`. El front ya omite `status`
        # para propiedades rented (adminprop-front#58); esto bloquea el
        # mismo salto si algun cliente lo envia igual.
        if "status" in fields_set and await self._repo.has_active_dependencies(
            property_id, organization_id
        ):
            raise InvalidStatusTransitionException(
                message="No se puede cambiar el estado manualmente: la propiedad tiene un "
                "contrato activo.",
                field="status",
                details={"property_id": str(property_id)},
            )

        if "landlord_id" in fields_set and landlord_id is not None:
            landlord_still_valid = await self._repo.landlord_exists(landlord_id, organization_id)
            if not landlord_still_valid:
                raise NotFoundException(
                    message="El propietario indicado no existe.", field="landlord_id"
                )

        # Issue #99: `neighborhood_id` no puede vaciarse via PATCH -- si el
        # campo viene, `PropertyUpdate` ya garantiza que no es `None`
        # (rechazado por Pydantic antes de llegar aca); solo resta validar
        # existencia/tenant con el mismo criterio RN-D01 que `landlord_id`.
        if "neighborhood_id" in fields_set and neighborhood_id is not None:
            neighborhood_still_valid = await self._repo.neighborhood_exists(
                neighborhood_id, organization_id
            )
            if not neighborhood_still_valid:
                raise NotFoundException(
                    message="El barrio indicado no existe.", field="neighborhood_id"
                )

        update_fields: dict[str, object] = {}
        if "address" in fields_set:
            update_fields["address"] = address
        if "landlord_id" in fields_set:
            update_fields["landlord_id"] = landlord_id
        if "neighborhood_id" in fields_set:
            update_fields["neighborhood_id"] = neighborhood_id
        if "property_type" in fields_set:
            update_fields["property_type"] = property_type
        if "status" in fields_set:
            update_fields["status"] = status
        if "notes" in fields_set:
            update_fields["notes"] = notes

        previous_landlord_id = current.landlord_id
        updated = await self._repo.update(property_id, organization_id, fields=update_fields)
        if updated is None:  # pragma: no cover -- defensivo, ya se valido existencia arriba
            raise NotFoundException()

        if (
            "landlord_id" in fields_set
            and landlord_id is not None
            and landlord_id != previous_landlord_id
        ):
            # RN: cambio de propietario auditado (afecta a quien se liquida).
            await audit(
                self._repo.session,
                organization_id=organization_id,
                action="property.landlord_changed",
                entity_type="property",
                entity_id=property_id,
                before={"landlord_id": str(previous_landlord_id)},
                after={"landlord_id": str(landlord_id)},
                user_id=actor_user_id,
            )

        await self._repo.commit()
        return updated

    async def delete(self, property_id: UUID, organization_id: UUID) -> None:
        """RF-01 + CA-01-03: soft delete; `409 ENTITY_HAS_DEPENDENCIES` si
        hay contrato `active` (ver `PropertyRepository.has_active_dependencies`
        -- siempre `False` hasta que exista el modulo `contracts`, issue #17)."""
        existing = await self._repo.get_by_id(property_id, organization_id)
        if existing is None:
            raise NotFoundException()

        if await self._repo.has_active_dependencies(property_id, organization_id):
            raise EntityHasDependenciesException(
                details={"entity_type": "property", "entity_id": str(property_id)}
            )

        await self._repo.soft_delete(property_id, organization_id)
        await self._repo.commit()


def get_property_service(
    repo: PropertyRepository = Depends(get_property_repository),
) -> PropertyService:
    return PropertyService(repo)


class PropertyServiceAccountService:
    """RF-02: ABM de cuentas de servicio de una propiedad."""

    def __init__(self, repo: PropertyServiceAccountRepository) -> None:
        self._repo = repo

    async def create(
        self,
        *,
        property_id: UUID,
        organization_id: UUID,
        service_type: str,
        account_number: str,
        secondary_number: str | None,
        notes: str | None,
    ):
        if not await self._repo.property_exists(property_id, organization_id):
            raise NotFoundException()

        row = await self._repo.create(
            organization_id=organization_id,
            property_id=property_id,
            service_type=service_type,
            account_number=account_number,
            secondary_number=secondary_number,
            notes=notes,
        )
        await self._repo.commit()
        return row

    async def list_by_property(self, property_id: UUID, organization_id: UUID) -> list:
        """RF-02: "todas las cuentas visibles juntas en su ficha" -- 404
        si la propiedad no existe/es de otro tenant (RN-D01)."""
        if not await self._repo.property_exists(property_id, organization_id):
            raise NotFoundException()
        return await self._repo.list_by_property(property_id, organization_id)

    async def update(
        self,
        service_account_id: UUID,
        organization_id: UUID,
        *,
        account_number: str | None,
        secondary_number: str | None,
        notes: str | None,
        fields_set: set[str],
    ):
        update_fields: dict[str, object] = {}
        if "account_number" in fields_set:
            update_fields["account_number"] = account_number
        if "secondary_number" in fields_set:
            update_fields["secondary_number"] = secondary_number
        if "notes" in fields_set:
            update_fields["notes"] = notes

        updated = await self._repo.update(service_account_id, organization_id, fields=update_fields)
        if updated is None:
            raise NotFoundException()
        await self._repo.commit()
        return updated

    async def delete(self, service_account_id: UUID, organization_id: UUID) -> None:
        """RN-D02: baja logica -- la cuenta de servicio es solo informativa,
        sin dependencias que chequear (RF-02: "ninguna logica de negocio
        depende de estas cuentas")."""
        existing = await self._repo.get_by_id(service_account_id, organization_id)
        if existing is None:
            raise NotFoundException()

        await self._repo.soft_delete(service_account_id, organization_id)
        await self._repo.commit()


def get_property_service_account_service(
    repo: PropertyServiceAccountRepository = Depends(get_property_service_account_repository),
) -> PropertyServiceAccountService:
    return PropertyServiceAccountService(repo)


class NeighborhoodService:
    """RF-05 (issue #99): ABM del catalogo de barrios por organizacion."""

    def __init__(self, repo: NeighborhoodRepository) -> None:
        self._repo = repo

    async def create(self, *, organization_id: UUID, name: str):
        # RF-05 + CA-01-07: "name unico por organizacion, case-insensitive"
        if await self._repo.name_exists(organization_id, name):
            raise ConflictException(message="Ya existe un barrio con ese nombre.", field="name")
        row = await self._repo.create(organization_id=organization_id, name=name)
        await self._repo.commit()
        return row

    async def list(self, organization_id: UUID) -> list:
        return await self._repo.list(organization_id)

    async def update(self, neighborhood_id: UUID, organization_id: UUID, *, name: str):
        existing = await self._repo.get_by_id(neighborhood_id, organization_id)
        if existing is None:
            raise NotFoundException()

        if await self._repo.name_exists(organization_id, name, exclude_id=neighborhood_id):
            raise ConflictException(message="Ya existe un barrio con ese nombre.", field="name")

        updated = await self._repo.update(neighborhood_id, organization_id, name=name)
        if updated is None:  # pragma: no cover -- defensivo, ya se valido existencia arriba
            raise NotFoundException()
        await self._repo.commit()
        return updated

    async def delete(self, neighborhood_id: UUID, organization_id: UUID) -> None:
        """CA-01-07: `409 ENTITY_HAS_DEPENDENCIES` si el barrio tiene
        propiedades asociadas (no borradas)."""
        existing = await self._repo.get_by_id(neighborhood_id, organization_id)
        if existing is None:
            raise NotFoundException()

        if await self._repo.has_properties(neighborhood_id, organization_id):
            raise EntityHasDependenciesException(
                details={"entity_type": "neighborhood", "entity_id": str(neighborhood_id)}
            )

        await self._repo.soft_delete(neighborhood_id, organization_id)
        await self._repo.commit()


def get_neighborhood_service(
    repo: NeighborhoodRepository = Depends(get_neighborhood_repository),
) -> NeighborhoodService:
    return NeighborhoodService(repo)
