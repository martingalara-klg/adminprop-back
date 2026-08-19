"""Punto de extension para RF-07 (issue #24): "El PDF generado queda
como Adjunto del cobro" (spec_module_04_cobranzas.md §RF-07).

La tabla `attachments` es de la Capa 5 (Mantenimiento, Fase 6, issue
todavia no numerado en este roadmap) y no existe hoy -- mismo patron que
`payments/settlement_hook.py` documenta para Modulo 5 (Liquidaciones):
este hook es deliberadamente no-op, con la firma final lista para que
`PaymentService.generate_receipt_pdf` la invoque sin cambios cuando la
tabla exista. En ese momento, reemplazar el cuerpo por el INSERT real en
`attachments` (entity_type='payment', entity_id=payment_id, mime_type=
'application/pdf', el binario del PDF ya generado)."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession


async def maybe_store_receipt_attachment(
    session: AsyncSession,
    *,
    organization_id: UUID,
    payment_id: UUID,
    pdf_bytes: bytes,
) -> None:
    """No-op (issue #24): `attachments` (Capa 5) todavia no existe -- no
    hay donde persistir el PDF. Cuando exista, reemplazar por el INSERT
    real. Parametros sin usar hoy a proposito -- la firma queda lista
    para ese reemplazo (mismo patron que `settlement_hook.py`)."""
    return
