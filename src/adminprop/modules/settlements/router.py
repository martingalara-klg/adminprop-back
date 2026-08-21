"""Endpoints de `settlements`: generacion asincrona + lectura (issue #29)
+ emision, regeneracion auditada y exports (issue #30).

SDD: core/sdd_03_api_contracts.md §11 "Liquidaciones" +
docs/sdd/features/spec_module_05_liquidaciones.md §RF-01/RF-02/RF-03/RF-04.

Permiso `settlement:generate` para el POST /generate, `settlement:issue`
para POST /issue, `settlement:read` para los GET y `POST /regenerate`
(regenerar es una correccion sobre datos ya leidos, no una emision nueva
-- catalogo real de sdd_03 §"Catalogo de Permisos": no existe un
`settlement:regenerate` atomico separado, y `settlement:generate` queda
reservado a la generacion inicial por simetria con el resto del catalogo,
que no distingue "crear" de "recalcular" para otros recursos regenerables
como `charge-entries`)."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, Response, status

from adminprop.modules.settlements.schemas import (
    SettlementAttachmentSummary,
    SettlementDetail,
    SettlementGenerateAccepted,
    SettlementGenerateAcceptedData,
    SettlementGenerateRequest,
    SettlementLineItemDetail,
    SettlementListResponse,
    SettlementPropertyGroup,
    SettlementRegenerateAccepted,
    SettlementRegenerateRequest,
    SettlementResponse,
    SettlementSummary,
    parse_period,
)
from adminprop.modules.settlements.service import (
    SettlementService,
    get_settlement_service,
)
from adminprop.shared.attachments.repository import (
    AttachmentRepository,
    get_attachment_repository,
)
from adminprop.shared.auth.jwt import JWTPayload
from adminprop.shared.errors.codes import NotFoundException
from adminprop.shared.rbac import requires_permission
from adminprop.shared.storage.local import read_attachment
from adminprop.shared.tenant import get_current_tenant

router = APIRouter(prefix="/v1/settlements", tags=["settlements"])

# RF-01: "el cliente polea GET /settlements/:id" -- estimacion informativa
# del tiempo de procesamiento (mismo orden de magnitud que
# `SettlementCalculationAccepted` del skill api-endpoint.md).
_ESTIMATED_COMPLETION_SECONDS = 15

# RF-03: `GET /settlements/:id/export?format=xlsx|pdf` -- mapeo cerrado
# formato <-> mime_type del adjunto guardado por `documents_worker`.
_EXPORT_MIME_TYPES: dict[str, str] = {
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "pdf": "application/pdf",
}


def _attachment_format(mime_type: str) -> str:
    for fmt, mime in _EXPORT_MIME_TYPES.items():
        if mime == mime_type:
            return fmt
    return mime_type  # pragma: no cover -- defensivo, solo guardamos xlsx/pdf


def _request_id(request: Request) -> str:
    return request.headers.get("X-Request-Id") or str(getattr(request.state, "request_id", ""))


@router.get(
    "",
    response_model=SettlementListResponse,
    dependencies=[Depends(requires_permission("settlement:read"))],
)
async def list_settlements(
    period: str | None = Query(default=None, description="YYYY-MM"),
    landlord_id: UUID | None = Query(default=None),
    status: str | None = Query(default=None, description="draft|issued"),
    organization_id: UUID = Depends(get_current_tenant),
    service: SettlementService = Depends(get_settlement_service),
) -> SettlementListResponse:
    """sdd_03 §11: `GET /settlements?period=&landlord_id=&status=`.
    CA-05-06: cada fila incluye `needs_regeneration` (liquidacion `issued`
    con un cobro anulado desde la ultima regeneracion)."""
    period_date = parse_period(period) if period is not None else None
    settlements, flags = await service.list(
        organization_id=organization_id,
        period=period_date,
        landlord_id=landlord_id,
        status=status,
    )
    return SettlementListResponse(
        data=[
            SettlementSummary.model_validate(s).model_copy(
                update={"needs_regeneration": flags.get(s.id, False)}
            )
            for s in settlements
        ]
    )


@router.post(
    "/generate",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=SettlementGenerateAccepted,
    dependencies=[Depends(requires_permission("settlement:generate"))],
)
async def generate_settlement(
    dto: SettlementGenerateRequest,
    request: Request,
    organization_id: UUID = Depends(get_current_tenant),
    payload: JWTPayload = Depends(requires_permission("settlement:generate")),
    service: SettlementService = Depends(get_settlement_service),
) -> SettlementGenerateAccepted:
    """SDD: spec_module_05_liquidaciones.md §RF-01 + sdd_03 §11
    "POST /settlements/generate". Implements: CA-05-01..04, RN-L01/L02/
    L04/L05/L06. Validaciones sincronicas ANTES del 202 (existencia,
    duplicado, TC requerido, mes no futuro, regla de "sin propiedad/
    movimientos") -- el calculo real corre en `documents_worker`."""
    settlement = await service.generate(
        organization_id=organization_id,
        landlord_id=dto.landlord_id,
        period=dto.period_date,
        exchange_rate=dto.exchange_rate,
        actor_user_id=payload.sub,
        request_id=_request_id(request),
    )
    return SettlementGenerateAccepted(
        data=SettlementGenerateAcceptedData(
            settlement_id=settlement.id,
            status="pending",
            estimated_completion_seconds=_ESTIMATED_COMPLETION_SECONDS,
        )
    )


@router.get(
    "/{settlement_id}",
    response_model=SettlementResponse,
    dependencies=[Depends(requires_permission("settlement:read"))],
)
async def get_settlement(
    settlement_id: UUID,
    scope: str = Query(default="consolidated", description="consolidated|per_property"),
    organization_id: UUID = Depends(get_current_tenant),
    service: SettlementService = Depends(get_settlement_service),
    attachment_repo: AttachmentRepository = Depends(get_attachment_repository),
) -> SettlementResponse:
    """sdd_03 §11: `GET /settlements/:id?scope=` (totales + line items +
    adjuntos Excel/PDF). RF-04: `scope=per_property` agrupa por propiedad
    con subtotal. RN-D01: 404 si es de otro tenant o no existe -- no
    distingue ambos casos."""
    detail = await service.get_detail(settlement_id, organization_id, scope=scope)
    settlement = detail.settlement

    attachments = await attachment_repo.list_by_entity(
        entity_type="settlement", entity_id=settlement_id, organization_id=organization_id
    )

    property_groups = None
    if detail.property_groups is not None:
        property_groups = [
            SettlementPropertyGroup(
                property_id=group.property_id,
                property_label=group.property_label,
                line_items=[SettlementLineItemDetail.model_validate(li) for li in group.line_items],
                subtotal_ars=group.subtotal_ars,
            )
            for group in detail.property_groups
        ]

    return SettlementResponse(
        data=SettlementDetail(
            id=settlement.id,
            landlord_id=settlement.landlord_id,
            period=settlement.period,
            status=settlement.status,
            job_status=detail.job_status,
            warnings=detail.warnings,
            needs_regeneration=detail.needs_regeneration,
            exchange_rate=settlement.exchange_rate,
            total_collected=settlement.total_collected,
            commission_total=settlement.commission_total,
            charges_total=settlement.charges_total,
            repairs_total=settlement.repairs_total,
            already_settled_total=settlement.already_settled_total,
            net_amount=settlement.net_amount,
            commission_pct_used=settlement.commission_pct_used,
            regenerated_count=settlement.regenerated_count,
            generated_by=settlement.generated_by,
            issued_at=settlement.issued_at,
            created_at=settlement.created_at,
            updated_at=settlement.updated_at,
            line_items=[SettlementLineItemDetail.model_validate(li) for li in detail.line_items],
            property_groups=property_groups,
            attachments=[
                SettlementAttachmentSummary(
                    id=a.id,
                    file_name=a.file_name,
                    mime_type=a.mime_type,
                    format=_attachment_format(a.mime_type),
                    created_at=a.created_at,
                )
                for a in attachments
            ],
        )
    )


@router.post(
    "/{settlement_id}/issue",
    response_model=SettlementResponse,
    dependencies=[Depends(requires_permission("settlement:issue"))],
)
async def issue_settlement(
    settlement_id: UUID,
    request: Request,
    organization_id: UUID = Depends(get_current_tenant),
    payload: JWTPayload = Depends(requires_permission("settlement:issue")),
    service: SettlementService = Depends(get_settlement_service),
) -> SettlementResponse:
    """RF-03 + sdd_03 §11 "POST /settlements/:id/issue": `draft -> issued`.
    `422 INVALID_STATUS_TRANSITION` si ya esta `issued` (RF-03: la unica
    transicion es `draft -> issued`; una liquidacion emitida se corrige
    via `POST /regenerate`, RN-L03)."""
    updated = await service.issue(
        settlement_id,
        organization_id,
        actor_user_id=payload.sub,
        request_id=_request_id(request),
    )
    detail = await service.get_detail(settlement_id, organization_id)
    return SettlementResponse(
        data=SettlementDetail(
            id=updated.id,
            landlord_id=updated.landlord_id,
            period=updated.period,
            status=updated.status,
            job_status=detail.job_status,
            warnings=detail.warnings,
            needs_regeneration=detail.needs_regeneration,
            exchange_rate=updated.exchange_rate,
            total_collected=updated.total_collected,
            commission_total=updated.commission_total,
            charges_total=updated.charges_total,
            repairs_total=updated.repairs_total,
            already_settled_total=updated.already_settled_total,
            net_amount=updated.net_amount,
            commission_pct_used=updated.commission_pct_used,
            regenerated_count=updated.regenerated_count,
            generated_by=updated.generated_by,
            issued_at=updated.issued_at,
            created_at=updated.created_at,
            updated_at=updated.updated_at,
            line_items=[SettlementLineItemDetail.model_validate(li) for li in detail.line_items],
            attachments=[],
        )
    )


@router.post(
    "/{settlement_id}/regenerate",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=SettlementRegenerateAccepted,
    dependencies=[Depends(requires_permission("settlement:read"))],
)
async def regenerate_settlement(
    settlement_id: UUID,
    dto: SettlementRegenerateRequest,
    request: Request,
    organization_id: UUID = Depends(get_current_tenant),
    payload: JWTPayload = Depends(requires_permission("settlement:read")),
    service: SettlementService = Depends(get_settlement_service),
) -> SettlementRegenerateAccepted:
    """RF-03/RN-L03 + sdd_03 §11 "POST /settlements/:id/regenerate": 202,
    recalcula con los datos corregidos (cobros anulados/agregados, cargos
    corregidos, TC nuevo opcional). CA-05-06: `regenerated_count++` y
    queda auditado -- el calculo real corre en `documents_worker`, mismo
    patron async que `POST /generate`."""
    settlement = await service.regenerate(
        settlement_id=settlement_id,
        organization_id=organization_id,
        exchange_rate=dto.exchange_rate,
        actor_user_id=payload.sub,
        request_id=_request_id(request),
    )
    return SettlementRegenerateAccepted(
        data=SettlementGenerateAcceptedData(
            settlement_id=settlement.id,
            status="pending",
            estimated_completion_seconds=_ESTIMATED_COMPLETION_SECONDS,
        )
    )


@router.get(
    "/{settlement_id}/export",
    dependencies=[Depends(requires_permission("settlement:read"))],
)
async def export_settlement(
    settlement_id: UUID,
    format: str = Query(..., pattern="^(xlsx|pdf)$"),
    organization_id: UUID = Depends(get_current_tenant),
    service: SettlementService = Depends(get_settlement_service),
    attachment_repo: AttachmentRepository = Depends(get_attachment_repository),
) -> Response:
    """RF-03 + sdd_03 §11 "GET /settlements/:id/export?format=xlsx|pdf":
    descarga el adjunto ya generado por `documents_worker` al terminar el
    calculo (generacion o regeneracion, CA-05-07). `404 NOT_FOUND` si la
    liquidacion no existe/es de otro tenant (RN-D01) o si todavia no tiene
    un export de ese formato (job en curso o fallido)."""
    # RN-D01: valida existencia/tenant de la liquidacion antes de buscar
    # el adjunto (evita filtrar por enumeracion de `settlement_id`).
    detail = await service.get_detail(settlement_id, organization_id)
    settlement_id = detail.settlement.id

    mime_type = _EXPORT_MIME_TYPES[format]
    attachments = await attachment_repo.list_by_entity(
        entity_type="settlement", entity_id=settlement_id, organization_id=organization_id
    )
    matching = [a for a in attachments if a.mime_type == mime_type]
    if not matching:
        raise NotFoundException()
    # RF-03: si hubo regeneraciones, el export mas reciente es el vigente
    # (`list_by_entity` ordena por `created_at asc`).
    attachment = matching[-1]

    content = read_attachment(attachment.file_path)
    return Response(
        content=content,
        media_type=mime_type,
        headers={"Content-Disposition": f'attachment; filename="{attachment.file_name}"'},
    )
