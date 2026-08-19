"""RF-08 (issue #24): certificado de libre deuda del inquilino.

SDD: features/spec_module_04_cobranzas.md §RF-08 + core/sdd_03_api_
contracts.md §9 `POST /renters/:id/debt-certificate`.
Implements: CA-04-11 (sin deuda -> PDF + auditoria `debt_certificate.
issued`), CA-04-12 (con deuda -> `422 RENTER_HAS_DEBT` con detalle en
`details`), RN-P08.

Vive en `modules/people` (no en `modules/payments`) porque el recurso es
`/renters/:id/debt-certificate` -- mismo criterio de "dueno del router"
que ya aplica `GET /renters/:id/debt` (issue #23, en `people/router.py`).
Reutiliza `DebtService.renter_debt` (issue #23) para decidir si hay
deuda -- NO duplica el calculo de saldo/dias de mora/interes sugerido
(pedido explicito del issue #24).

Este modulo importa `payments.service`/`payments.repository` a nivel de
modulo (no diferido) -- eso es seguro ACA porque `people/router.py` solo
importa ESTE archivo en DIFERIDO, dentro del handler del endpoint (mismo
ciclo de import que documenta `people/router.py.get_renter_debt`: si
`people/router.py` importara `payments.service` a nivel de modulo,
cerraria el ciclo `properties -> people -> payments -> contracts ->
properties`; al diferir el import de este archivo hasta el request, para
entonces todos los modulos ya terminaron de cargar)."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from uuid import UUID

from adminprop.modules.administracion.repository import AdministracionRepository
from adminprop.modules.payments.service import DebtEntry, DebtService
from adminprop.modules.people.attachment_hook import maybe_store_debt_certificate_attachment
from adminprop.modules.people.repository import RenterRepository
from adminprop.shared.audit.service import audit
from adminprop.shared.errors.codes import NotFoundException, RenterHasDebtException
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
        renter_repo: RenterRepository,
        admin_repo: AdministracionRepository,
        debt_service: DebtService,
        contract_rows: list[tuple[str, Decimal, str]],
        actor_user_id: UUID,
    ) -> None:
        """`contract_rows`: `(property_address, current_amount, currency)`
        de los contratos activos del inquilino -- ya resueltos por el
        caller (evita que este servicio dependa de `ContractRepository`/
        `PropertyRepository` directamente, ambos con el mismo problema de
        import diferido)."""
        self._renter_repo = renter_repo
        self._admin_repo = admin_repo
        self._debt_service = debt_service
        self._contract_rows = contract_rows
        self._actor_user_id = actor_user_id

    async def issue(self, renter_id: UUID, organization_id: UUID, *, today: date) -> bytes:
        renter = await self._renter_repo.get_by_id(renter_id, organization_id)
        if renter is None:  # pragma: no cover -- defensivo, el router ya valido existencia
            # RN-D01: 404, no 403 -- no distingue "no existe" de "otra org".
            raise NotFoundException()

        debts = await self._debt_service.renter_debt(renter_id, organization_id, today=today)
        if debts:
            # CA-04-12: "con deuda -> 422 RENTER_HAS_DEBT con el detalle
            # de lo adeudado en details".
            raise RenterHasDebtException(
                details={"debts": [_debt_entry_to_details(d) for d in debts]}
            )

        settings = await self._admin_repo.get_organization_settings(organization_id)
        billing_header = (settings or {}).get("billing_header") or {}

        rows: list[tuple[str, str]] = [
            ("Inquilino", renter.name),
            ("Fecha de emision", today.strftime("%d/%m/%Y")),
        ]
        for address, amount, currency in self._contract_rows:
            rows.append((f"Propiedad ({currency} {amount:.2f}/mes)", address))

        html = document_html(
            title="Certificado de libre deuda",
            billing_header=billing_header,
            body_rows=rows,
            footer="El presente certificado acredita que el inquilino no registra "
            "periodos impagos ni saldos parciales a la fecha de emision.",
        )
        pdf_bytes = render_pdf_from_html(html)

        # CA-04-11: "la emision queda auditada" -- misma transaccion que
        # el commit de abajo.
        await audit(
            self._renter_repo.session,
            organization_id=organization_id,
            action="debt_certificate.issued",
            entity_type="renter",
            entity_id=renter_id,
            after={"issued_at": today.isoformat()},
            user_id=self._actor_user_id,
        )

        # RF-08: "cada emision queda como Adjunto del inquilino" -- no-op
        # hasta que exista `attachments` (Capa 5, ver attachment_hook.py).
        await maybe_store_debt_certificate_attachment(
            self._renter_repo.session,
            organization_id=organization_id,
            renter_id=renter_id,
            pdf_bytes=pdf_bytes,
        )

        await self._renter_repo.commit()
        return pdf_bytes
