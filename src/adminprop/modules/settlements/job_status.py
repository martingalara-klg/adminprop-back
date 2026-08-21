"""Estado del job de generacion asincrona de una liquidacion (issue #29).

SDD: docs/sdd/features/spec_module_05_liquidaciones.md §RF-01: "Estados
del job de generacion: `pending` -> `processing` -> `completed` |
`with_errors` | `failed`."

Decision de implementacion (documentada en el PR): `settlements.status`
(migracion #27, capa 6) solo admite `draft`/`issued` (CHECK) -- es el
estado DE NEGOCIO de la liquidacion (RF-03, issue #30), no el estado del
JOB de calculo asincrono. Este issue NO agrega migraciones (fuera de
alcance explicito), asi que el estado del job y sus advertencias (RF-01:
"con periodos impagos o cargos faltantes termina `with_errors` y las
advertencias se listan en el detalle") se trackean en Redis -- mismo
backend que ya usa el result backend de Celery para `task_id` operacional
(`docs/skills/async-worker.md`: "el result backend solo trackea task_id
operacional, nunca datos de negocio"); esto extiende ese mismo criterio a
un dato de progreso igualmente operacional (no persistente por diseno: si
Redis se reinicia, `get_job_status` degrada a "completed" -- ver
`service.py.get_detail`, consistente con que la liquidacion YA existe en
Postgres con sus totales/line items reales).

TTL de 24h: tiempo mas que suficiente para que el cliente complete el
polling, sin acumular claves indefinidamente.
"""

from __future__ import annotations

import json
from typing import Literal
from uuid import UUID

from adminprop.shared.cache.redis import get_redis_client

JobStatus = Literal["pending", "processing", "completed", "with_errors", "failed"]

_KEY_PREFIX = "settlement_job:"
_TTL_SECONDS = 60 * 60 * 24


def _key(settlement_id: UUID) -> str:
    return f"{_KEY_PREFIX}{settlement_id}"


async def set_job_status(
    settlement_id: UUID, status: JobStatus, *, warnings: list[str] | None = None
) -> None:
    """Escribe el estado del job -- llamado por `service.py` (al encolar,
    `pending`) y por `workers/documents_worker.py` (`processing` al
    empezar, `completed`/`with_errors`/`failed` al terminar)."""
    redis = get_redis_client()
    payload = json.dumps({"status": status, "warnings": warnings or []})
    await redis.set(_key(settlement_id), payload, ex=_TTL_SECONDS)


async def get_job_status(settlement_id: UUID) -> dict | None:
    """Lee el estado del job -- `None` si la clave nunca se seteo o ya
    expiro (TTL); `service.py.get_detail` interpreta `None` como
    "completed sin advertencias" (la liquidacion existe en Postgres, el
    job ya termino hace tiempo)."""
    redis = get_redis_client()
    raw = await redis.get(_key(settlement_id))
    if raw is None:
        return None
    return json.loads(raw)
