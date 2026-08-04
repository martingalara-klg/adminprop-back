# external-integrations

## Cuándo leer este skill

Leer **antes de**:

- Integrar el índice **ICL** (API pública del BCRA) para ajuste de contratos.
- Integrar el índice **IPC** (datos.gob.ar / INDEC).
- Integrar **Resend** (email transaccional).
- Configurar el storage local de archivos (comprobantes, PDFs de recibos y liquidaciones).
- Configurar el manejo de secretos locales (`.env`).

## Stack relevante

| Servicio | Librería | Configuración | Fuente |
|---|---|---|---|
| Índice ICL | `httpx` contra la API pública del BCRA | Endpoint estadísticas monetarias del BCRA; sin autenticación | backend `CLAUDE.md` §3 |
| Índice IPC | `httpx` contra la API de datos.gob.ar / series de tiempo de INDEC | Endpoint de series públicas; sin autenticación | backend `CLAUDE.md` §3 |
| Email transaccional | Resend Python SDK | API key en variable de entorno local (`.env`, no commiteado); migrar a un gestor de secretos cuando exista infra cloud | backend `CLAUDE.md` §3 |
| Storage | Filesystem local vía volumen Docker | Rutas `/data/adminprop-storage/{org_slug}/{purpose}/`; migrar a storage cloud post-infra | backend `CLAUDE.md` §3 |

## SDDs de referencia

- `features/spec_module_05_liquidaciones.md` §RF-02 — pipeline de obtención y aplicación de índices.
- `core/sdd_04_nonfunctional.md` §2.9 — política de reintento e indisponibilidad de servicios de índices.
- `infrastructure/spec_notificaciones.md` §"Apéndice" — Email: rate limits, retry, dead-letter, branding `From` dinámico.

## El patrón

### Índice ICL (API pública del BCRA)

```python
# src/adminprop/shared/indices/bcra.py
# SDD: features/spec_module_05_liquidaciones.md §RF-02.

from datetime import date
import httpx

from adminprop.shared.errors.retryable import RetryableIndexError, NonRetryableIndexError


BCRA_ICL_ENDPOINT = "https://api.bcra.gob.ar/estadisticas/v3.0/monetarias/40"   # SDD §RF-02
_TIMEOUT_SECONDS = 10.0


class IclIndexValue:
    def __init__(self, reference_date: date, value: float, variation_percent: float) -> None:
        self.reference_date = reference_date
        self.value = value
        self.variation_percent = variation_percent


async def get_icl_index(reference_date: date) -> IclIndexValue:
    """
    Obtiene el valor del índice ICL para una fecha de referencia.
    SDD: spec_module_05_liquidaciones §RF-02.
    """
    async with httpx.AsyncClient(timeout=_TIMEOUT_SECONDS, verify=True) as http:
        try:
            response = await http.get(
                BCRA_ICL_ENDPOINT,
                params={"desde": reference_date.isoformat(), "hasta": reference_date.isoformat()},
            )
        except httpx.TimeoutException as exc:
            raise RetryableIndexError("timeout") from exc

    if response.status_code in {429, 500, 502, 503, 504}:
        raise RetryableIndexError(f"BCRA ICL {response.status_code}")

    if response.status_code == 404:
        raise NonRetryableIndexError(f"No hay valor de ICL para {reference_date.isoformat()}")

    if 400 <= response.status_code < 500:
        raise NonRetryableIndexError(f"BCRA ICL {response.status_code}: {response.text}")

    response.raise_for_status()
    payload = response.json()
    if not payload.get("results"):
        raise NonRetryableIndexError(f"No hay valor de ICL para {reference_date.isoformat()}")

    row = payload["results"][0]
    return IclIndexValue(
        reference_date=reference_date,
        value=row["valor"],
        variation_percent=row.get("variacion_porcentual", 0.0),
    )
```

### Índice IPC (datos.gob.ar / INDEC)

```python
# src/adminprop/shared/indices/indec.py
# SDD: features/spec_module_05_liquidaciones.md §RF-02 (índice alternativo para
# contratos que no ajustan por ICL).

from datetime import date
import httpx

from adminprop.shared.errors.retryable import RetryableIndexError, NonRetryableIndexError


IPC_SERIES_ENDPOINT = "https://apis.datos.gob.ar/series/api/series"
IPC_SERIES_ID = "148.3_INIVELNAL_DICI_M_26"   # serie IPC nivel general, INDEC
_TIMEOUT_SECONDS = 10.0


async def get_ipc_index(reference_month: date) -> float:
    """Obtiene el valor del IPC (nivel general) para el mes de referencia."""
    async with httpx.AsyncClient(timeout=_TIMEOUT_SECONDS) as http:
        try:
            response = await http.get(
                IPC_SERIES_ENDPOINT,
                params={
                    "ids": IPC_SERIES_ID,
                    "start_date": reference_month.strftime("%Y-%m"),
                    "limit": 1,
                },
            )
        except httpx.TimeoutException as exc:
            raise RetryableIndexError("timeout") from exc

    if response.status_code in {429, 500, 502, 503, 504}:
        raise RetryableIndexError(f"IPC datos.gob.ar {response.status_code}")

    if 400 <= response.status_code < 500:
        raise NonRetryableIndexError(f"IPC datos.gob.ar {response.status_code}: {response.text}")

    response.raise_for_status()
    payload = response.json()
    rows = payload.get("data", [])
    if not rows:
        raise NonRetryableIndexError(f"No hay valor de IPC para {reference_month.strftime('%Y-%m')}")

    return float(rows[0][1])
```

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
- [ ] El timeout está configurado (10s típico para índices/email).
- [ ] Los errores se clasifican explícitamente en Retryable* vs NonRetryable* antes de propagarse al worker.
- [ ] El `request_id` se incluye como header (`X-Request-Id`) para trazabilidad en el proveedor, cuando el proveedor lo soporta.
- [ ] Las respuestas del proveedor con datos sensibles no se loguean en texto plano.
- [ ] El cliente del índice (ICL/IPC) se mockea con fixtures JSON en tests (`tests/fixtures/external/`).
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
def get_icl_index(reference_date):
    ...
# 400/404 (fecha sin valor publicado, formato inválido) no cambia con retry.
# El job se reintenta 10 veces, consume cuota y falla igual.

# ✅ Diferenciar reintentables vs no-reintentables explícitamente
try:
    return await _call_bcra(...)
except httpx.HTTPStatusError as exc:
    if exc.response.status_code in {429, 500, 502, 503, 504}:
        raise RetryableIndexError(str(exc)) from exc
    raise NonRetryableIndexError(str(exc)) from exc
```

```python
# ❌ Llamar al servicio del índice real en cada request de un endpoint
async def get_contract_projected_amount(...):
    index = await get_icl_index(today())   # ¡HTTP roundtrip cada vez!
    ...
# Si el índice se publica una vez al mes, no hace falta consultarlo en
# cada request; cachear el último valor conocido por período.

# ✅ Cachear el valor del período en Redis o en la propia tabla de índices
async def get_icl_index_cached(reference_date):
    cached = await redis.get(f"icl_index:{reference_date.isoformat()}")
    if cached:
        return cached
    value = await get_icl_index(reference_date)
    await redis.set(f"icl_index:{reference_date.isoformat()}", value, ex=60 * 60 * 24)
    return value
```

```python
# ❌ Llamar al proveedor real (BCRA/INDEC/Resend) en CI
def test_apply_index_adjustment():
    response = client.post(f"/v1/contracts/{contract_id}/apply-index", ...)
    # ❌ Esto golpea la API pública del BCRA y rompe CI si el servicio está caído.

# ✅ Mockear con fixtures JSON
def test_apply_index_adjustment(mock_icl_client):
    response = client.post(f"/v1/contracts/{contract_id}/apply-index", ...)
    assert response.status_code == 200
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
    raise RetryableIndexError(f"rate limit, retry-after {retry_after}s")
```

## Referencias

- `features/spec_module_05_liquidaciones.md` §RF-02 — pipeline de obtención y aplicación de índices ICL/IPC.
- `core/sdd_04_nonfunctional.md` §2.9 — política de retry e indisponibilidad de servicios externos.
- `infrastructure/spec_notificaciones.md` §"Apéndice" — Email: rate limits, retry/backoff, dead letter, branding From dinámico.
- Backend `CLAUDE.md` §3 y §10 — librerías concretas (`httpx`, SDK de Resend).
- `_index.md` §4 — decisiones sobre manejo de secretos locales en MVP.
