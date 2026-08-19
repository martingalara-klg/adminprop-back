"""Logica de negocio de `ContractAdjustment` -- RF-04 (issue #18).

SDD: docs/sdd/features/spec_module_03_contratos.md §RF-04.
Implements: CA-03-04, CA-03-05 (RN-C03, RN-P01, RN-D01).

El flujo completo de 5 pasos (RF-04):
1. Deteccion diaria (`detect_due_adjustments`, llamado por el worker Beat
   por cada organizacion `active`) crea el ajuste `pending`.
2. Notificacion in-app + email a owner/admin (`adjustment_pending`).
3. Bandeja `GET /adjustments?status=pending` (`list_pending`).
4. Aplicacion manual del % (`apply`) -- recalcula `current_amount` del
   contrato y marca el ajuste `applied` (inmutable, sdd_02 §2.8).
5. Historial (`list_for_contract`).
"""

from __future__ import annotations

from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from uuid import UUID

from fastapi import Depends

from adminprop.modules.contracts.adjustment_repository import (
    ContractAdjustmentRepository,
    get_contract_adjustment_repository,
)
from adminprop.modules.contracts.models import ContractAdjustment
from adminprop.modules.contracts.rent_period_hook import (
    maybe_generate_rent_period_for_adjustment,
)
from adminprop.modules.contracts.repository import ContractRepository, get_contract_repository
from adminprop.shared.audit.service import audit
from adminprop.shared.errors.codes import (
    AdjustmentAlreadyAppliedException,
    AdjustmentPctRequiredException,
    NotFoundException,
)
from adminprop.shared.notifications import service as notifications_service

_TWO_DECIMALS = Decimal("0.01")


def _add_months(anchor: date, months: int) -> date:
    """Suma `months` a `anchor` y normaliza al dia 1 del mes resultante
    (`due_period` es siempre dia 1 -- CHECK `date_trunc('month', ...)` de
    la migracion #16)."""
    zero_based_month = anchor.month - 1 + months
    year = anchor.year + zero_based_month // 12
    month = zero_based_month % 12 + 1
    return date(year, month, 1)


class ContractAdjustmentService:
    def __init__(
        self,
        repo: ContractAdjustmentRepository,
        contract_repo: ContractRepository,
    ) -> None:
        self._repo = repo
        self._contract_repo = contract_repo

    # ─── RF-04 paso 1: deteccion diaria (job `detect_due_adjustments`) ─────

    async def detect_due_adjustments(self, *, organization_id: UUID, today: date) -> list[UUID]:
        """RN-C03: crea el ajuste `pending` de cada contrato ARS `active`
        cuyo proximo periodo de ajuste ya llego (contado desde
        `start_date` o desde el `due_period` del ultimo ajuste `applied`).
        Nunca calcula ni aplica un % (RN-C03: siempre manual).

        Idempotente: si el contrato ya tiene un ajuste `pending`, se
        salta (CA-16-03/ADJUSTMENT_PENDING_EXISTS ya lo impediria a nivel
        de indice parcial unico; el chequeo previo evita el round-trip
        fallido y hace la corrida diaria un no-op limpio para contratos ya
        detectados).

        Devuelve los IDs de notificacion `adjustment_pending` creadas --
        el worker las usa DESPUES de su commit para encolar el email
        (patron outbox, `shared/notifications/service.py`).
        """
        candidates = await self._repo.list_ars_contracts_due_for_adjustment_check(organization_id)
        notification_ids: list[UUID] = []
        for candidate in candidates:
            if await self._repo.has_pending_adjustment(candidate.id, organization_id):
                continue

            anchor = await self._repo.get_last_applied_adjustment_due_period(
                candidate.id, organization_id
            )
            if anchor is None:
                anchor = date(candidate.start_date.year, candidate.start_date.month, 1)
            next_due_period = _add_months(anchor, candidate.adjustment_frequency_months)
            if today < next_due_period:
                continue

            adjustment = await self._repo.create_pending(
                organization_id=organization_id,
                contract_id=candidate.id,
                due_period=next_due_period,
                previous_amount=candidate.current_amount,
            )

            # RF-04 paso 2: notificacion in-app (misma transaccion) a
            # owner+admin (RN-01 de shared/notifications, evento ya
            # declarado en EVENT_RECIPIENT_ROLES desde el issue #11).
            ids = await notifications_service.emit(
                self._repo.session,
                organization_id=organization_id,
                event_type="adjustment_pending",
                payload={
                    "contract_id": str(candidate.id),
                    "adjustment_id": str(adjustment.id),
                    "due_period": next_due_period.isoformat(),
                },
            )
            notification_ids.extend(ids)

        return notification_ids

    # ─── RF-04 paso 3/5: bandeja + historial ────────────────────────────────

    async def list_for_contract(
        self, contract_id: UUID, organization_id: UUID
    ) -> list[ContractAdjustment]:
        """`GET /contracts/:id/adjustments`. RN-D01: el caller (router)
        primero valida que el contrato exista en el tenant -- cross-tenant
        u otro contrato inexistente ya es 404 antes de llegar aca."""
        return await self._repo.list_by_contract(contract_id, organization_id)

    async def list_pending(
        self, *, organization_id: UUID, cursor: str | None, limit: int
    ) -> tuple[list[ContractAdjustment], str | None]:
        """`GET /adjustments?status=pending` -- bandeja de ajustes que tocan."""
        return await self._repo.list_pending(
            organization_id=organization_id, cursor=cursor, limit=limit
        )

    # ─── RF-04 paso 4: aplicacion manual ────────────────────────────────────

    async def apply(
        self,
        adjustment_id: UUID,
        organization_id: UUID,
        *,
        pct: Decimal | None,
        actor_user_id: UUID,
    ) -> ContractAdjustment:
        """RN-C03: `pending -> applied`. `new_amount = previous ×
        (1 + pct/100)`, redondeado a 2 decimales (NUMERIC(14,2)).
        Actualiza `current_amount` del contrato y deja historial completo
        (pct/monto anterior/monto nuevo/autor) en la MISMA transaccion
        que la auditoria -- si algo falla, nada de esto queda a medias."""
        adjustment = await self._repo.get_by_id(adjustment_id, organization_id)
        if adjustment is None:
            raise NotFoundException()

        if adjustment.status != "pending":
            raise AdjustmentAlreadyAppliedException(details={"adjustment_id": str(adjustment_id)})

        # sdd_03: 400 ADJUSTMENT_PCT_REQUIRED -- distinto del generico
        # VALIDATION_ERROR (mismo criterio que CA-03-06/current_amount).
        if pct is None:
            raise AdjustmentPctRequiredException(field="pct")

        previous_amount = adjustment.previous_amount
        new_amount = (previous_amount * (Decimal(1) + pct / Decimal(100))).quantize(
            _TWO_DECIMALS, rounding=ROUND_HALF_UP
        )

        updated = await self._repo.apply(
            adjustment_id,
            organization_id,
            pct=pct,
            new_amount=new_amount,
            actor_user_id=actor_user_id,
        )
        if updated is None:  # pragma: no cover -- defensivo, ya se valido existencia arriba
            raise NotFoundException()

        # RN-04/RN-C04: el monto vigente del contrato SOLO cambia via un
        # ajuste registrado -- este es el unico punto de mutacion. Se
        # captura la fila actualizada (antes se descartaba) porque
        # `maybe_generate_rent_period_for_adjustment` necesita `currency`
        # para el INSERT del rent_period (issue #21).
        contract = await self._contract_repo.update(
            adjustment.contract_id, organization_id, fields={"current_amount": new_amount}
        )
        if contract is None:  # pragma: no cover -- defensivo, el ajuste ya referencia un contrato existente
            raise NotFoundException()

        # Integraciones: "Log de Auditoria: Ajustes aplicados" (sdd_02 §2.8
        # + spec_module_03_contratos.md §"Integraciones").
        await audit(
            self._repo.session,
            organization_id=organization_id,
            action="adjustment.applied",
            entity_type="contract_adjustment",
            entity_id=adjustment_id,
            before={
                "status": "pending",
                "previous_amount": str(previous_amount),
            },
            after={
                "status": "applied",
                "pct_applied": str(pct),
                "new_amount": str(new_amount),
                "applied_by": str(actor_user_id),
            },
            user_id=actor_user_id,
        )

        # RF-04 paso 4/CA-04-02: "una vez aplicado, se genera con el monto
        # nuevo" -- ver rent_period_hook.py (issue #21).
        await maybe_generate_rent_period_for_adjustment(
            self._repo.session,
            contract_id=adjustment.contract_id,
            organization_id=organization_id,
            period=adjustment.due_period,
            amount_due=new_amount,
            currency=contract.currency,
        )

        await self._repo.commit()
        return updated


def get_contract_adjustment_service(
    repo: ContractAdjustmentRepository = Depends(get_contract_adjustment_repository),
    contract_repo: ContractRepository = Depends(get_contract_repository),
) -> ContractAdjustmentService:
    return ContractAdjustmentService(repo, contract_repo)
