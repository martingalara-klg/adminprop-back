"""Punto de extension para RF-08 (issue #24): "Cada emision queda como
Adjunto del inquilino" (spec_module_04_cobranzas.md §RF-08).

Mismo criterio que `modules/payments/attachment_hook.py` documenta para
el recibo de cobro (RF-07): `attachments` (Capa 5) no existe todavia --
hook no-op con la firma final lista para `DebtCertificateService.issue`."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession


async def maybe_store_debt_certificate_attachment(
    session: AsyncSession,
    *,
    organization_id: UUID,
    renter_id: UUID,
    pdf_bytes: bytes,
) -> None:
    """No-op (issue #24): ver `payments/attachment_hook.py` para el
    criterio completo. Reemplazar por el INSERT real en `attachments`
    (entity_type='renter', entity_id=renter_id) cuando la tabla exista."""
    return
