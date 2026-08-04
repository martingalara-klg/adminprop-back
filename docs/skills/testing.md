# testing

## Cuándo leer este skill

Leer **antes de**:

- Escribir tests para una tarea (CA-XX o flujo alternativo).
- Configurar el harness de testing en un módulo nuevo.
- Revisar un PR que incluye tests.

Define la nomenclatura, cobertura mínima y la pirámide de testing que aplica a ambos repos.

## Stack relevante

| Capa | Backend (`adminprop-back`) | Frontend (`adminprop-front`) |
|---|---|---|
| Unit tests | `pytest`, `pytest-asyncio` | Vitest + React Testing Library |
| Integration tests | `pytest` + `httpx.AsyncClient` (cliente de test FastAPI) | Vitest con MSW (Mock Service Worker) si se necesita stub de API |
| E2E tests | (delegado al frontend) | **Playwright** |
| Component docs | — | Storybook (desde MVP) |
| Cobertura mínima | **95% global** (excl. DTOs, modelos declarativos sin método custom y boilerplate trivial) | No definida por SDD; default sugerido: 80% en lógica de hooks y utils, sin métrica obligatoria sobre componentes presentacionales |
| Mocks de servicios externos (backend) | Fixtures JSON del índice ICL (BCRA) y del webhook de Resend en `tests/fixtures/external/` | N/A |

Fuente: backend `CLAUDE.md` §3 "Tests" y §10; frontend `CLAUDE.md` §3 "Stack de frontend".

## SDDs de referencia

- `core/sdd_01_prd.md` §3 — define los UC-01..UC-N que dan nombre a los tests E2E principales.
- `core/sdd_02_domain_model.md` §3 — define las invariantes RN-C (contratos), RN-P (pagos), RN-L (liquidaciones), RN-A (accesos), RN-D que cada test debe cubrir.
- `core/sdd_03_api_contracts.md` — define los códigos de error custom (`VALIDATION_ERROR`, `PERIOD_LOCKED`, etc.) que los tests deben verificar literalmente.
- `core/sdd_04_nonfunctional.md` §2.5 — define los rate limits que los tests de rate-limit deben enforzar.
- Cada `spec_module_XX_*.md` — su sección "Criterios de Aceptación" lista los CA-XX a cubrir.

## El patrón

### Nomenclatura: tests nombrados con el ID del criterio del SDD

Cada test que verifica un criterio de aceptación del SDD se nombra con el ID exacto. Esto crea trazabilidad inequívoca entre el SDD y la suite.

#### Backend (pytest)

```python
# tests/integration/onboarding/test_organization_creation.py
# SDD: spec_module_00_superadmin.md §RF-02

import pytest
from httpx import AsyncClient


class TestRF02OrganizationCreation:
    """spec_module_00_superadmin.md §RF-02 — Creación de organización."""

    @pytest.mark.asyncio
    async def test_rf02_01_super_admin_creates_org_in_pending_owner(
        self,
        client: AsyncClient,
        super_admin_jwt: str,
    ):
        """
        CA: Al crear una organización, queda en estado `pending_owner`
        (naranja en el dashboard).
        """
        response = await client.post(
            "/v1/superadmin/organizations",
            json={
                "name": "Acme Propiedades",
                "tipo_organizacion": "inmobiliaria",
                "plan": "starter",
                "timezone": "America/Buenos_Aires",
            },
            headers={"Authorization": f"Bearer {super_admin_jwt}"},
        )

        assert response.status_code == 201
        data = response.json()["data"]
        assert data["status"] == "pending_owner"
        assert data["slug"] == "acme-propiedades"
```

Patrón del nombre:

```
test_<id-en-minusculas>_<descripcion-corta>

CA-RF02-01  →  test_rf02_01_<descripcion>
CA-04-09    →  test_04_09_<descripcion>
UC-05       →  test_uc05_<descripcion>
RN-P02      →  test_rn_p02_<descripcion>
```

El docstring contiene la descripción exacta del CA o RN. El nombre del archivo agrupa por módulo/feature.

#### Frontend (Vitest)

```typescript
// apps/main/src/modules/auth/__tests__/login-flow.spec.ts
// SDD: spec_module_00_superadmin.md §"Flujo de Activación de Cuenta"
// + sdd_04 §2.2 "Autenticación y sesiones"

import { describe, it, expect } from 'vitest'

describe('UC-01 — Activación de cuenta vía invitación', () => {
  it('CA-01-01: GET /auth/invitation/:token con token válido muestra formulario de activación', async () => {
    // arrange, act, assert
  })

  it('CA-01-02: GET /auth/invitation/:token con token expirado muestra error con CTA "contactá al admin"', async () => {
    // ...
  })
})
```

#### Frontend (Playwright)

```typescript
// tests/e2e/onboarding/accept-invitation.spec.ts

import { test, expect } from '@playwright/test'

test.describe('UC-01 — Onboarding de owner', () => {
  test('CA-01-01: owner activa cuenta y queda autenticado', async ({ page, request }) => {
    // 1. setup: crear org + invitación vía API
    // 2. abrir el link en el browser
    // 3. completar el formulario
    // 4. assert: redirect a / autenticado
  })
})
```

### Pirámide de testing

```
              ┌──────────────┐
              │     E2E      │   Playwright (frontend)
              │  flujos      │   - happy paths críticos: auth/MFA, alta de contrato,
              │  críticos    │     registro de cobro, orden de trabajo end-to-end
              └──────┬───────┘
                     │
              ┌──────┴───────────┐
              │   Integration    │   pytest + httpx (backend)
              │ un test por CA-XX│   - cada CA-XX del SDD = un test
              │ aislamiento      │   - aislamiento multi-tenant (obligatorio en cada módulo)
              │ multi-tenant     │   - contratos de API completos (status code, error code, shape)
              │ contratos API    │
              └──────┬───────────┘
                     │
              ┌──────┴───────────┐
              │      Unit        │   pytest (backend) + Vitest (frontend)
              │  lógica de       │   - reglas RN-XX aisladas (fórmulas: ajuste por índice, saldo de cobro)
              │  negocio pura    │   - transformaciones puras de datos
              │  RN-XX aisladas  │   - validators (Zod en frontend, Pydantic custom validators en backend)
              └──────────────────┘
```

### Test de aislamiento multi-tenant (obligatorio en backend)

Todo módulo backend que toca datos tenant-scoped debe incluir este test. Verifica que un usuario del Tenant A nunca acceda a recursos del Tenant B, y que la respuesta sea **404** (no 403 — RN-D01 + convención de no revelar existencia cross-tenant).

```python
# tests/integration/contracts/test_tenant_isolation.py
# Invariante: RN-D01 — Los datos de un tenant nunca son accesibles desde otro.

import pytest
from httpx import AsyncClient


class TestTenantIsolation:
    """RN-D01 enforcement: tenant A no accede a recursos del tenant B."""

    @pytest.mark.asyncio
    async def test_get_resource_from_other_tenant_returns_404_not_403(
        self,
        client: AsyncClient,
        tenant_a_jwt: str,
        tenant_b_resource_id: str,   # creado vía fixture en setup de tenant B
    ):
        """
        Un usuario autenticado en Tenant A pide GET /<recurso>/:id de un
        recurso de Tenant B. La API debe responder 404 (NOT_FOUND), no 403,
        para no revelar la existencia del recurso fuera del scope.
        """
        response = await client.get(
            f"/v1/contracts/{tenant_b_resource_id}",
            headers={"Authorization": f"Bearer {tenant_a_jwt}"},
        )

        assert response.status_code == 404
        body = response.json()
        assert body["error"]["code"] == "NOT_FOUND"
```

### Test de flujo alternativo (un test por escenario del SDD)

Cada SDD tiene una sección "Criterios de Aceptación" + flujos alternativos implícitos (token expirado, slug taken, monto excede saldo, etc.). Cada uno debe tener un test dedicado, no un genérico "happy path + error".

```python
# tests/integration/onboarding/test_invitation_expired.py
# SDD: spec_module_00_superadmin.md §RF-03 + sdd_03 §"Códigos de Error Globales"

class TestInvitationExpired:
    """RF-03 — Token de invitación expirado."""

    @pytest.mark.asyncio
    async def test_accept_invitation_with_expired_token_returns_422(
        self,
        client: AsyncClient,
        expired_invitation_token: str,
    ):
        response = await client.post(
            "/v1/auth/accept-invitation",
            json={
                "token": expired_invitation_token,
                "full_name": "Juan García",
                "password": "Password1234",
            },
        )

        assert response.status_code == 422
        body = response.json()
        assert body["error"]["code"] == "INVITATION_EXPIRED"
```

### Mocks de servicios externos (backend)

Las integraciones externas (índice ICL del BCRA, IPC de datos.gob.ar/INDEC, email vía Resend) **no se testean contra el servicio real en CI**. Usar fixtures JSON deterministas:

```
tests/fixtures/external/
├── bcra_icl_index_ok.json
├── bcra_icl_index_not_found.json
├── indec_ipc_index_ok.json
├── resend_send_ok.json
├── resend_webhook_delivered.json
└── ...
```

El cliente HTTP se reemplaza por un mock determinista que retorna fixtures según el endpoint llamado.

```python
# tests/conftest.py (fragmento)

@pytest.fixture
def mock_icl_client(monkeypatch):
    """Sustituye el cliente HTTP del índice ICL por uno que retorna fixtures."""
    from adminprop.shared.indices import bcra

    class FakeBcraClient:
        async def get_icl_index(self, reference_date):
            if reference_date.year < 2020:
                return load_fixture("bcra_icl_index_not_found.json")
            return load_fixture("bcra_icl_index_ok.json")

    monkeypatch.setattr(bcra, "get_client", lambda *_args, **_kwargs: FakeBcraClient())
```

### Tests de rate-limit

Para los endpoints con rate-limit declarado en `sdd_04` §2.5 (login, mfa challenge, creación de contratos, etc.), incluir un test que verifique el límite documentado:

```python
class TestRateLimit:
    """sdd_04 §2.5 — Rate limit en POST /auth/login (10/IP en 10min)."""

    @pytest.mark.asyncio
    async def test_login_returns_429_after_10_attempts_same_ip(
        self,
        client: AsyncClient,
        redis_clear,   # fixture que limpia Redis antes del test
    ):
        for _ in range(10):
            await client.post("/v1/auth/login", json={"email": "x@y.com", "password": "wrong"})

        response = await client.post(
            "/v1/auth/login",
            json={"email": "x@y.com", "password": "wrong"},
        )
        assert response.status_code == 429
        assert response.json()["error"]["code"] == "RATE_LIMIT_EXCEEDED"
        assert "Retry-After" in response.headers
```

### Frontend: tests por estado del flujo, no sólo happy path

Cada flujo del frontend tiene los siguientes estados que deben cubrirse (ver `flow-implementation.md` para detalle):

- `idle` — estado inicial sin acción
- `loading` — request en curso
- `success` — operación exitosa
- `error` — error del servidor (cada `error.code` relevante)
- `expired` — token o sesión expirada
- `empty` — operación OK sin datos

```typescript
// apps/main/src/modules/auth/__tests__/login.spec.ts

describe('Login flow', () => {
  it('UC-LOGIN-01: success → guarda sesión y redirige a /', async () => { ... })
  it('UC-LOGIN-02: 401 UNAUTHORIZED → muestra "Credenciales incorrectas." (anti-enumeration)', async () => { ... })
  it('UC-LOGIN-03: 403 ACCOUNT_LOCKED → muestra countdown de 30 min', async () => { ... })
  it('UC-LOGIN-04: 200 mfa_challenge_required → redirige a /login/mfa-challenge', async () => { ... })
  it('UC-LOGIN-05: 429 RATE_LIMIT_EXCEEDED → muestra Retry-After', async () => { ... })
})
```

## Template

### Template de test de integración backend (CA-XX)

```python
# tests/integration/<modulo>/test_<ca_xx>_<descripcion>.py
# SDD: <ruta-del-SDD>.md §<sección>

import pytest
from httpx import AsyncClient


class Test<NombreDelCriterio>:
    """<doc-del-SDD>.md §<sección> — <descripción del CA>."""

    @pytest.mark.asyncio
    async def test_<id_ca>_<descripcion_snake_case>(
        self,
        client: AsyncClient,
        # fixtures necesarias: jwt del rol que tiene permiso, datos sembrados
    ):
        """
        CA-<XX>: <descripción exacta del SDD, copiada del docstring del SDD>.
        """
        # arrange — sembrar datos relevantes (vía fixtures o factory)

        # act
        response = await client.<method>(
            "<path>",
            json={...},
            headers={"Authorization": f"Bearer {jwt}"},
        )

        # assert — status, error.code o data, side-effects
        assert response.status_code == <expected_code>
        body = response.json()
        if response.status_code >= 400:
            assert body["error"]["code"] == "<EXPECTED_CODE>"
            # opcional: assert sobre body["error"]["field"] o body["error"]["details"]
        else:
            assert body["data"] == <expected_shape>
```

### Template de test frontend con Vitest + RTL

```typescript
// apps/main/src/modules/<modulo>/__tests__/<feature>.spec.tsx
// SDD: <ruta-del-SDD>.md §<sección>

import { describe, it, expect, vi } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'

describe('<UC-XX> — <título del flujo>', () => {
  it('CA-XX-NN: <descripción exacta del SDD>', async () => {
    // arrange
    // ...

    // act
    const user = userEvent.setup()
    render(<Component />)
    await user.click(screen.getByRole('button', { name: /enviar/i }))

    // assert
    await waitFor(() => {
      expect(screen.getByText(/mensaje exacto del SDD/i)).toBeInTheDocument()
    })
  })
})
```

### Template de test E2E con Playwright

```typescript
// tests/e2e/<modulo>/<feature>.spec.ts

import { test, expect } from '@playwright/test'

test.describe('<UC-XX> — <título del flujo>', () => {
  test('CA-XX-NN: <descripción>', async ({ page, request }) => {
    // 1. setup vía API (más rápido que UI clicks)
    const { token } = await request.post('/test-utils/seed', { ... })

    // 2. ejecutar el flujo en UI
    await page.goto(`/accept-invitation?token=${token}`)
    await page.getByLabel(/nombre/i).fill('Juan García')
    await page.getByLabel(/contraseña/i).fill('Password1234')
    await page.getByRole('button', { name: /activar/i }).click()

    // 3. assert
    await expect(page).toHaveURL('/')
    await expect(page.getByText(/bienvenido/i)).toBeVisible()
  })
})
```

## Checklist pre-commit

- [ ] Cada CA-XX/UC-XX del SDD asignado a la tarea tiene su test.
- [ ] El nombre del test incluye el ID exacto del SDD (`test_rf02_01_...`, `CA-XX-NN`).
- [ ] El docstring/describe contiene la descripción exacta del SDD.
- [ ] Para backend: existe un test de aislamiento multi-tenant en el módulo.
- [ ] Para backend: los tests verifican el `error.code` exacto del SDD (no sólo el status code).
- [ ] Para frontend: cada estado del flujo (idle, loading, success, error, expired, empty) está cubierto.
- [ ] Los mensajes de error de seguridad (anti-enumeration, MFA, etc.) se verifican textualmente.
- [ ] Si hay integraciones externas (ICL, IPC, Resend): usa fixtures JSON, no llama al servicio real.
- [ ] Si hay rate-limit: existe un test que llega al límite y verifica 429.
- [ ] Backend: cobertura del módulo cubierto en el PR ≥ 95% (excluyendo DTOs y modelos sin lógica).

## Antipatrones

```python
# ❌ Test genérico que no se ata a un CA específico
def test_create_settlement():
    response = client.post("/v1/settlements/calculate", json={...})
    assert response.status_code == 202

# ✅ Test atado al CA + verifica el error code, no sólo el status
class TestRF03GenerateSettlementPerProperty:
    """spec_module_05_liquidaciones.md §RF-04 — scope=per_property."""

    def test_rf03_01_returns_202_with_settlement_id_and_status_draft(self, client, ...):
        response = client.post("/v1/settlements/calculate", json={
            "owner_id": "...",
            "scope": "per_property",
            "property_id": "...",
            "period_id": "...",
            "currency": "ARS",
        })
        assert response.status_code == 202
        data = response.json()["data"]
        assert data["status"] == "draft"
        assert data["scope"] == "per_property"
        assert data["settlement_id"] is not None
```

```python
# ❌ Test que sólo verifica el status code, no el error.code
async def test_create_settlement_without_fiscal_module():
    response = await client.post("/v1/settlements/calculate", ...)
    assert response.status_code == 403   # ¿FORBIDDEN, FEATURE_NOT_ACTIVATED, ROLE_REQUIRED?

# ✅ Verificar el error.code exacto del SDD (sdd_03 §"Códigos de Error Globales")
async def test_create_settlement_without_module_active_returns_feature_not_activated():
    response = await client.post("/v1/settlements/calculate", ...)
    assert response.status_code == 403
    body = response.json()
    assert body["error"]["code"] == "FEATURE_NOT_ACTIVATED"
    assert body["error"]["message"]   # mensaje legible
```

```python
# ❌ Test que retorna 403 para acceso cross-tenant
async def test_user_a_cannot_read_user_b_contract():
    response = await client.get(f"/v1/contracts/{user_b_contract_id}",
                                 headers={"Authorization": f"Bearer {user_a_jwt}"})
    assert response.status_code == 403   # ¡Mal! Esto revela que el recurso existe.

# ✅ Verifica 404 (no revela existencia cross-tenant)
async def test_user_a_gets_404_on_user_b_contract():
    response = await client.get(f"/v1/contracts/{user_b_contract_id}",
                                 headers={"Authorization": f"Bearer {user_a_jwt}"})
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "NOT_FOUND"
```

```typescript
// ❌ Frontend: usa un mensaje genérico en lugar del exacto del SDD
expect(screen.getByText(/error de credenciales/i)).toBeVisible()

// ✅ Frontend: usa el mensaje exacto del SDD (anti-enumeration)
// El SDD dice: "Credenciales incorrectas." sin diferenciar email/password.
expect(screen.getByText('Credenciales incorrectas.')).toBeVisible()
```

```python
# ❌ Llamar al servicio del índice ICL real en CI
def test_calculate_settlement_with_index():
    response = client.post(f"/v1/settlements/{settlement_id}/apply-index", ...)
    # ❌ Esto golpea la API pública del BCRA y rompe CI si el servicio está caído.

# ✅ Mockear con fixtures JSON
def test_calculate_settlement_with_index(mock_icl_client):
    # mock_icl_client retorna bcra_icl_index_ok.json
    response = client.post(f"/v1/settlements/{settlement_id}/apply-index", ...)
    assert response.status_code == 200
    assert response.json()["data"]["index_value"] == 123.45   # del fixture
```

## Referencias

- Backend `CLAUDE.md` §3 "Tests" y §10 — pytest, pytest-asyncio, httpx; cobertura 95% global con exclusiones documentadas.
- Frontend `CLAUDE.md` §3 — Vitest + RTL + Playwright + Storybook desde MVP.
- `core/sdd_02_domain_model.md` §3 "Reglas de Negocio Críticas" — catálogo completo de RN-XX que cada test de unit verifica.
- `core/sdd_03_api_contracts.md` §"Códigos de Error Globales" + §"Resumen de Autorización por Recurso" — la suite verifica `error.code` exacto y permiso esperado por endpoint.
- `core/sdd_04_nonfunctional.md` §2.5 (rate limits) y §2.2 (anti-enumeration) — tests dedicados a estas reglas.
- Cada `spec_module_XX_*.md` §"Criterios de Aceptación" — fuente de los IDs de test (`CA-XX-NN`).
