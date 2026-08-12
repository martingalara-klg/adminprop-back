"""documents_worker — esqueleto (issue #4).

SDD: core/sdd_04_nonfunctional.md §1.3.
Skill: docs/skills/async-worker.md, docs/skills/tenant-isolation.md.

La generacion real de Excel (openpyxl) y PDF (WeasyPrint) de liquidaciones
llega con los issues #29/#30. Este modulo solo deja aplicado (y testeado)
el patron obligatorio de aislamiento multi-tenant en workers Celery
(`tenant_scoped_session` — RN-D01, docs/skills/tenant-isolation.md: "los
workers no tienen middleware que lo setee, deben llamar a
set_tenant_context explicitamente antes de cualquier query") para que
#29/#30 solo agreguen la logica de negocio sin tener que re-descubrir el
patron.
"""

import asyncio
import logging
from uuid import UUID

from celery import Task

from adminprop.db.session import tenant_scoped_session
from adminprop.workers.celery_app import celery_app

logger = logging.getLogger(__name__)


class DocumentsTask(Task):
    """Politica de reintentos — sdd_04 §1.3.

    Se completa (autoretry_for con los errores especificos de
    generacion de documentos) cuando la logica real llegue con #29/#30;
    por ahora el esqueleto no ejecuta ningun I/O que pueda fallar.
    """

    max_retries = 3


@celery_app.task(
    base=DocumentsTask,
    bind=True,
    name="adminprop.workers.documents_worker.generate_document_skeleton",
)
def generate_document_skeleton(
    self: Task, document_id: str, organization_id: str, request_id: str
) -> None:
    """Esqueleto de `documents_worker` (issue #4). No genera ningun archivo.

    Recibe IDs como string (no objetos ORM) — docs/skills/async-worker.md.
    La logica real (cargar la liquidacion, generar Excel/PDF, actualizar
    `status`) llega con los issues #29/#30.
    """
    logger.info(
        "generate_document_skeleton start (esqueleto, sin logica de negocio)",
        extra={
            "request_id": request_id,
            "organization_id": organization_id,
            "document_id": document_id,
            "attempt": self.request.retries + 1,
            "service": "documents_worker",
        },
    )
    asyncio.run(
        _generate_document_skeleton_async(UUID(document_id), UUID(organization_id), request_id)
    )


async def _generate_document_skeleton_async(
    document_id: UUID, organization_id: UUID, request_id: str
) -> None:
    async with tenant_scoped_session(organization_id):
        # RN-D01 / docs/skills/tenant-isolation.md: el contexto de tenant ya
        # esta seteado en la transaccion antes de cualquier query real.
        # #29/#30 reemplazan este cuerpo por: cargar la liquidacion
        # (filtrada por organization_id), generar Excel/PDF, actualizar
        # `settlement.status` en cada transicion. Todavia no hay tabla de
        # negocio que consultar (llega con los issues de Liquidaciones).
        logger.info(
            "generate_document_skeleton no-op",
            extra={"request_id": request_id, "document_id": str(document_id)},
        )
