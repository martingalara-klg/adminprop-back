"""RF-08 (issue #24, movido a `modules/contracts` en el issue #104):
certificado de libre deuda POR CONTRATO.

SDD: features/spec_module_04_cobranzas.md §RF-08 + core/sdd_03_api_
contracts.md §8 `POST /contracts/:id/debt-certificate`.
Implements: CA-04-11 (sin deuda -> PDF + auditoria `debt_certificate.
issued`), CA-04-12 (con deuda -> `422 CONTRACT_HAS_DEBT` con detalle en
`details`), RN-P08.

Decision del PO (issue #104, 2026-08-28): el libre deuda es
conceptualmente por CONTRATO -- un inquilino puede alquilar 2 propiedades
(ej: comercial) y deber en una si y en otra no. Se emite desde el
contrato y verifica SOLO los periodos de ESE contrato -- NUNCA los otros
contratos del mismo inquilino (a diferencia de la version original del
issue #24, que agregaba TODOS los contratos activos del inquilino).

Vive en `modules/contracts` (no en `modules/people`) porque el recurso
es `/contracts/:id/debt-certificate` -- mismo criterio de "dueno del
router" que ya aplica el resto de `contracts/router.py`.
Reutiliza `DebtService.contract_debt` (modulo `payments`) para decidir
si hay deuda -- NO duplica el calculo de saldo/dias de mora/interes
sugerido (mismo criterio del issue #24 original).

Este modulo se importa DIFERIDO desde `contracts/router.py` (dentro del
handler del endpoint, no a nivel de modulo): `payments.service` importa
`contracts.models`/`contracts.repository`/`contracts.rent_period_hook` a
nivel de modulo, y `contracts/__init__.py` importa `contracts.router` --
un import a nivel de modulo de `payments.service` (o de `people.
repository`) EN `contracts/router.py` cerraria un ciclo con el orden de
carga real de `main.py` (`contracts` se importa antes que `payments`).
Diferir el import hasta el request evita el ciclo porque para entonces
todos los modulos ya terminaron de cargar -- mismo patron que
documentaba `people/router.py.issue_debt_certificate` (issue #24,
ahora eliminado)."""

from __future__ import annotations

from datetime import date
from uuid import UUID

from adminprop.modules.administracion.repository import AdministracionRepository
from adminprop.modules.contracts.models import Contract
from adminprop.modules.contracts.repository import ContractRepository
from adminprop.modules.payments.service import DebtEntry, DebtService
from adminprop.modules.people.repository import RenterRepository
from adminprop.modules.properties.repository import PropertyRepository
from adminprop.shared.audit.service import audit
from adminprop.shared.errors.codes import ContractHasDebtException, NotFoundException
from adminprop.shared.pdf import document_html, render_pdf_from_html


def _debt_entry_to_details(entry: DebtEntry) -> dict:
    return {
        "contract_id": str(entry.contract_id),
        "property_id": str(entry.property_id),
        "periods_overdue": entry.periods_overdue,
        "balance": str(entry.balance),
        "days_late": entry.days_late,
        "suggested_interest": str(entry.suggested_interest),
    }


class DebtCertificateService:
    def __init__(
        self,
        *,
        contract_repo: ContractRepository,
        renter_repo: RenterRepository,
        property_repo: PropertyRepository,
        admin_repo: AdministracionRepository,
        debt_service: DebtService,
        actor_user_id: UUID,
    ) -> None:
        self._contract_repo = contract_repo
        self._renter_repo = renter_repo
        self._property_repo = property_repo
        self._admin_repo = admin_repo
        self._debt_service = debt_service
        self._actor_user_id = actor_user_id

    async def issue(self, contract_id: UUID, organization_id: UUID, *, today: date) -> bytes:
        contract: Contract | None = await self._contract_repo.get_by_id(
            contract_id, organization_id
        )
        if contract is None:  # pragma: no cover -- defensivo, el router ya valido existencia
            # RN-D01: 404, no 403 -- no distingue "no existe" de "otra org".
            raise NotFoundException()

        # RN-P08 (issue #104): verifica SOLO los periodos de ESTE contrato
        # -- nunca los de otros contratos del mismo inquilino.
        debt = await self._debt_service.contract_debt(contract_id, organization_id, today=today)
        if debt is not None:
            # CA-04-12: "con deuda -> 422 CONTRACT_HAS_DEBT con el detalle
            # de lo adeudado en details".
            raise ContractHasDebtException(details=_debt_entry_to_details(debt))

        renter = await self._renter_repo.get_by_id(contract.renter_id, organization_id)
        property_row = await self._property_repo.get_by_id(contract.property_id, organization_id)
        renter_name = renter.name if renter is not None else str(contract.renter_id)
        address = property_row.address if property_row is not None else str(contract.property_id)

        settings = await self._admin_repo.get_organization_settings(organization_id)
        billing_header = (settings or {}).get("billing_header") or {}

        rows: list[tuple[str, str]] = [
            ("Inquilino", renter_name),
            (
                f"Propiedad ({contract.currency} {contract.current_amount:.2f}/mes)",
                address,
            ),
            ("Fecha de emision", today.strftime("%d/%m/%Y")),
        ]

        html = document_html(
            title="Certificado de libre deuda",
            billing_header=billing_header,
            body_rows=rows,
            footer="El presente certificado acredita que el contrato no registra "
            "periodos impagos ni saldos parciales a la fecha de emision.",
        )
        pdf_bytes = render_pdf_from_html(html)

        # CA-04-11: "la emision queda auditada" -- misma transaccion que
        # el commit de abajo. `entity_type="contract"` (no "renter") --
        # el recurso del que cuelga el endpoint es el contrato (issue #104).
        await audit(
            self._contract_repo.session,
            organization_id=organization_id,
            action="debt_certificate.issued",
            entity_type="contract",
            entity_id=contract_id,
            after={"issued_at": today.isoformat()},
            user_id=self._actor_user_id,
        )

        await self._contract_repo.commit()
        return pdf_bytes
