# external-integrations

## Cuándo leer este skill

Leer **antes de**:

- Integrar **Resend** (email transaccional) — único servicio externo del MVP.
- Configurar el storage local de archivos (comprobantes, PDFs de recibos y liquidaciones).
- Configurar el manejo de secretos locales (`.env`).

> Nota: los índices de ajuste son ingreso manual del porcentaje (`sdd_03` §8); si post-MVP se automatizan, este skill aplica al fetch de índices.

## Stack relevante

| Servicio | Librería | Configuración | Fuente |
|---|---|---|---|
| Email transaccional | Resend Python SDK | API key en variable de entorno local (`.env`, no commiteado); migrar a un gestor de secretos cuando exista infra cloud | backend `CLAUDE.md` §3 |
| Storage | Filesystem local vía volumen Docker | Rutas `/data/adminprop-storage/{org_slug}/{purpose}/`; migrar a storage cloud post-infra | backend `CLAUDE.md` §3 |

## SDDs de referencia

- `core/sdd_03_api_contracts.md` §8 — índices de ajuste como ingreso manual del porcentaje (no fetch automático).
- `core/sdd_04_nonfunctional.md` §2.9 — política de reintento e indisponibilidad de servicios externos (único servicio externo del MVP: Resend).
- `infrastructure/spec_notificaciones.md` §"Apéndice" — Email: rate limits, retry, dead-letter, branding `From` dinámico.

## El patrón

### Email (Resend)

```python
# src/adminprop/shared/email/sender.py
# SDD: infrastructure/spec_notificaciones.md §"Email".

import httpx

from adminprop.config import settings
from adminprop.shared.errors.retryable import RetryableNotificationError


RESEND_API = "https://api.resend.com/emails"


async def send_email(
    *,
    to: list[str],
    subject: str,
    html: str,
    text: str | None = None,
    organization_name: str,
    owner_reply_email: str | None = None,
    request_id: str,
) -> str:
    """
    Envía un email via Resend. Retorna el message_id (para tracking en delivery log).
    SDD: spec_notificaciones §"Email" §"Header From dinámico"
      - From: "AdminProp · {organization_name} <noreply@adminprop.local>"  <!-- dominio provisorio hasta definir infra -->
      - Reply-To: owner_reply_email (primer owner activo)
    """
    from_header = f"AdminProp · {organization_name} <noreply@adminprop.local>"
    payload = {
        "from": from_header,
        "to": to,
        "subject": subject,
        "html": html,
        "text": text or "",
        "headers": {"X-Request-Id": request_id},
    }
    if owner_reply_email:
        payload["reply_to"] = owner_reply_email

    async with httpx.AsyncClient(timeout=10.0) as http:
        response = await http.post(
            RESEND_API,
            json=payload,
            headers={"Authorization": f"Bearer {settings.RESEND_API_KEY}"},
        )

    if response.status_code in {429, 500, 502, 503, 504}:
        # SDD §"Email" §"Retry y dead-letter": 30s, 5min, 30min (manejado por Celery)
        raise RetryableNotificationError(f"Resend {response.status_code}: {response.text}")

    response.raise_for_status()
    return response.json().get("id", "")
```

### Storage de archivos (filesystem local en MVP)

```python
# src/adminprop/shared/storage/local.py
# SDD: backend CLAUDE.md §3.

from pathlib import Path

STORAGE_ROOT = Path("/data/adminprop-storage")   # volumen Docker montado
DOWNLOAD_TTL_SECONDS = 7 * 24 * 60 * 60   # 7 días — equivalente al TTL de una signed URL cloud


def upload_local(organization_slug: str, purpose: str, object_key: str, content: bytes) -> Path:
    """
    Persiste un archivo en el volumen Docker local, con el mismo nivel de
    aislamiento per-tenant que tendría un bucket cloud (ver tenant-isolation.md).
    Migrar a storage cloud post-infra sin cambiar esta convención de rutas.
    """
    target_dir = STORAGE_ROOT / organization_slug / purpose
    target_dir.mkdir(parents=True, exist_ok=True)
    target_path = target_dir / object_key
    target_path.write_bytes(content)
    return target_path
```

### Carga de secretos locales

```python
# src/adminprop/config.py (fragmento)
# SDD: backend CLAUDE.md §3.
# Variables de entorno locales (.env, no commiteado). Migrar a un secret
# manager cuando exista infra cloud.

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    RESEND_API_KEY: str
    DATABASE_URL: str
    REDIS_BROKER_URL: str
    REDIS_RESULT_BACKEND_URL: str

    class Config:
        env_file = ".env"
```

## Template

Skeleton para integrar un proveedor externo:

```python
# src/adminprop/shared/<service>/<client>.py
# SDD: <ruta-del-SDD>.md §<sección>

import httpx   # o el SDK oficial del proveedor

from adminprop.config import settings
from adminprop.shared.errors.retryable import Retryable<X>Error, NonRetryable<X>Error


_BASE_URL = "<url-del-proveedor>"
_TIMEOUT_SECONDS = 10.0


async def call_<service>(
    *,
    request_id: str,
    organization_id: str,
    payload: dict,
) -> dict:
    """SDD: <ruta>.md §<sección>."""
    async with httpx.AsyncClient(timeout=_TIMEOUT_SECONDS) as http:
        try:
            response = await http.post(
                f"{_BASE_URL}/...",
                json=payload,
                headers={
                    "Authorization": f"Bearer {settings.<SERVICE>_API_KEY}",
                    "X-Request-Id": request_id,
                },
            )
        except httpx.TimeoutException as exc:
            raise Retryable<X>Error("timeout") from exc

    if response.status_code in {429, 500, 502, 503, 504}:
        raise Retryable<X>Error(f"<service> {response.status_code}")

    if 400 <= response.status_code < 500:
        raise NonRetryable<X>Error(f"<service> {response.status_code}: {response.text}")

    response.raise_for_status()
    return response.json()
```

## Checklist pre-commit

- [ ] El cliente HTTP/SDK se inicializa una vez o vía factory async con context manager, no re-creado por request cuando no es necesario.
- [ ] La API key se carga de una **variable de entorno local** (`.env`, no commiteado), nunca hardcodeada en el código.
- [ ] El timeout está configurado (10s típico para email).
- [ ] Los errores se clasifican explícitamente en Retryable* vs NonRetryable* antes de propagarse al worker.
- [ ] El `request_id` se incluye como header (`X-Request-Id`) para trazabilidad en el proveedor, cuando el proveedor lo soporta.
- [ ] Las respuestas del proveedor con datos sensibles no se loguean en texto plano.
- [ ] El cliente de Resend se mockea con fixtures JSON en tests (`tests/fixtures/external/`); nunca se llama al servicio real desde CI.
- [ ] Para email: el header `From` es dinámico `"AdminProp · {org.name} <noreply@adminprop.local>"`, `Reply-To` apunta al owner activo.
- [ ] Para storage local: la ruta sigue la convención `/data/adminprop-storage/{org_slug}/{purpose}/` (aislamiento per-tenant).
- [ ] Nunca se llama al servicio externo real desde CI: siempre fixtures.

## Antipatrones

```python
# ❌ Cargar API key hardcodeada en el código
RESEND_API_KEY = "re_abc123def..."   # en el código, o en .env commiteado a git
# Exposición directa de credenciales.

# ✅ Cargar de variable de entorno local
class Settings(BaseSettings):
    RESEND_API_KEY: str
    class Config:
        env_file = ".env"   # .env NUNCA se commitea
```

```python
# ❌ Tratar 400/404 como reintentable
@celery_task(autoretry_for=(Exception,), max_retries=10)
def send_transactional_email(payload):
    ...
# 400 (email inválido), 404 (destinatario eliminado) no cambian con retry.
# El job se reintenta 10 veces, consume cuota y falla igual.

# ✅ Diferenciar reintentables vs no-reintentables explícitamente
try:
    return await send_email(...)
except httpx.HTTPStatusError as exc:
    if exc.response.status_code in {429, 500, 502, 503, 504}:
        raise RetryableNotificationError(str(exc)) from exc
    raise NonRetryableNotificationError(str(exc)) from exc
```

```python
# ❌ Llamar al proveedor real (Resend) en CI
def test_send_invitation_email():
    response = await send_email(to=["x@y.com"], subject="...", html="...", ...)
    # ❌ Esto golpea la API real de Resend y rompe CI si el servicio está caído
    # (o consume cuota de la cuenta real).

# ✅ Mockear con fixtures JSON
def test_send_invitation_email(mock_resend_client):
    response = await send_email(to=["x@y.com"], subject="...", html="...", ...)
    assert response == "re_mocked_id"   # del fixture resend_send_ok.json
```

```python
# ❌ Buckets/paths compartidos entre tenants
"/data/adminprop-storage/all-orgs-shared/reports"   # una sola carpeta para todas las orgs
# El blast radius de un error de permisos o de un bug de path es total.

# ✅ Aislamiento per-tenant per-purpose en la ruta local
f"/data/adminprop-storage/{org.slug}/receipts"
f"/data/adminprop-storage/{org.slug}/settlements-pdf"
```

```python
# ❌ Sin Retry-After ni backoff ante 429 del proveedor
# El worker hace backoff fijo sin escuchar lo que el proveedor dice.

# ✅ Honrar el Retry-After si el proveedor lo manda
if response.status_code == 429:
    retry_after = int(response.headers.get("Retry-After", 30))
    await asyncio.sleep(retry_after)
    raise RetryableNotificationError(f"rate limit, retry-after {retry_after}s")
```

## Referencias

- `core/sdd_03_api_contracts.md` §8 — los índices de ajuste son ingreso manual del porcentaje (no hay fetch automático en el MVP).
- `core/sdd_04_nonfunctional.md` §2.9 — política de retry e indisponibilidad de servicios externos (Resend es el único servicio externo del MVP).
- `infrastructure/spec_notificaciones.md` §"Apéndice" — Email: rate limits, retry/backoff, dead letter, branding From dinámico.
- Backend `CLAUDE.md` §3 y §10 — librerías concretas (`httpx`, SDK de Resend).
- `_index.md` §4 — decisiones sobre manejo de secretos locales en MVP.
