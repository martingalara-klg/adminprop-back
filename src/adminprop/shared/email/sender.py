"""Cliente de email transaccional via Resend (issue #4).

Unico servicio externo del MVP (core/sdd_04_nonfunctional.md §2.9).
SDD: infrastructure/spec_notificaciones.md §"Email".
Skill: docs/skills/external-integrations.md.

`send_email` no decide la politica de reintentos: se limita a clasificar
la respuesta de Resend en `RetryableNotificationError` /
`NonRetryableNotificationError` y dejar que el llamador (la tarea Celery
`notification_worker.send_transactional_email`) aplique el backoff
30s -> 90s -> 270s con jitter (sdd_04 §1.3).
"""

import httpx

from adminprop.config import get_settings
from adminprop.shared.errors.retryable import (
    NonRetryableNotificationError,
    RetryableNotificationError,
)

RESEND_API_URL = "https://api.resend.com/emails"
_TIMEOUT_SECONDS = 10.0


async def send_email(
    *,
    to: list[str],
    subject: str,
    html: str,
    text: str | None,
    organization_name: str,
    owner_reply_email: str | None,
    request_id: str,
) -> str:
    """Envia un email transaccional via Resend. Retorna el `id` del mensaje.

    spec_notificaciones.md §"Email":
    - From dinamico: "AdminProp · {organization.name} <noreply@{dominio}>".
    - Reply-To: el email del owner activo de la organizacion (si se conoce).
    - `X-Request-Id` propagado como header para trazabilidad cross-stack
      (sdd_04 §4.6).

    Levanta `RetryableNotificationError` en 429/5xx/timeout y
    `NonRetryableNotificationError` en 4xx estructural (ej: email invalido).
    RF-03 (spec_notificaciones.md): el envio de email nunca bloquea la
    operacion de negocio — el llamador decide si reintenta o deja el
    fallo registrado (dead-letter).
    """
    settings = get_settings()
    from_header = f"AdminProp · {organization_name} <noreply@{settings.resend_from_domain}>"
    payload: dict[str, object] = {
        "from": from_header,
        "to": to,
        "subject": subject,
        "html": html,
        "text": text or "",
        "headers": {"X-Request-Id": request_id},
    }
    if owner_reply_email:
        payload["reply_to"] = owner_reply_email

    async with httpx.AsyncClient(timeout=_TIMEOUT_SECONDS) as client:
        try:
            response = await client.post(
                RESEND_API_URL,
                json=payload,
                headers={"Authorization": f"Bearer {settings.resend_api_key}"},
            )
        except httpx.TimeoutException as exc:
            raise RetryableNotificationError("resend timeout") from exc

    if response.status_code in {429, 500, 502, 503, 504}:
        raise RetryableNotificationError(f"resend {response.status_code}: {response.text}")

    if 400 <= response.status_code < 500:
        raise NonRetryableNotificationError(f"resend {response.status_code}: {response.text}")

    response.raise_for_status()
    return response.json().get("id", "")
