# code-review

## Cuándo leer este skill

Leer **antes de**:

- Revisar un PR (propio o ajeno).
- Aprobar un cambio en `develop` o `main`.
- Detectar una divergencia con el SDD.

El skill define la pregunta central, los checklists por tipo de artefacto y el formato de reporte de divergencia.

## Stack relevante

| Item | Valor |
|---|---|
| Plataforma de review | GitHub PR comments + reviews (`gh pr review`, `gh pr comment`) |
| Foco principal | ¿El código implementa exactamente lo que el SDD especifica? |
| Cobertura mínima exigida (backend) | 95% global, exclusiones documentadas (ver `testing.md`) |
| Convención de divergencia | Label `sdd:divergence` + label `status:blocked` (ver `github-project-workflow.md`) |

## SDDs de referencia

- `docs/sdd/_index.md` §6 — versionado de SDDs (`1.x` aditivo, `2.x` breaking) que el review debe respetar.
- Todos los SDDs en `core/`, `features/` e `infrastructure/` — el review verifica el alineamiento del código contra el SDD que la tarea declara como referencia.

## El patrón

### La pregunta central del code review

> ¿El código implementa **exactamente** lo que el SDD especifica?
>
> No más. No menos. No diferente.

Todo hallazgo del review se reduce a una de tres categorías:

1. **Diverge del SDD** → el código hace algo que el SDD no especifica o lo hace de forma distinta. **Bloquea el merge.** Se reporta con formato `[SDD-DIVERGENCE]`.
2. **No cumple el SDD** → el código no cubre un CA-XX, una RN-XX o un FA del SDD. **Bloquea el merge.**
3. **Cumple el SDD pero tiene mejoras** → estilo, claridad, performance. **No bloquea.** Se reporta como `[NIT]` o `[SUGGESTION]`.

### Convenciones de etiquetado en comentarios

Para hacer el comentario procesable:

```
[BLOCKING] <comentario que bloquea el merge>
[SDD-DIVERGENCE] <comentario que reporta una divergencia con el SDD>
[QUESTION] <comentario que pide aclaración>
[NIT] <mejora menor de estilo>
[SUGGESTION] <sugerencia opcional>
[PRAISE] <reconocimiento>
```

`[BLOCKING]` y `[SDD-DIVERGENCE]` requieren resolución antes del merge. El resto es opcional.

### Checklist por tipo de artefacto

#### Migraciones de base de datos (backend)

- [ ] El archivo está bajo `src/adminprop/db/migrations/versions/` con nombre `YYYYMMDD_HHMMSS_<slug>.py` (convención Alembic del proyecto).
- [ ] La operación SQL está dentro de una transacción Alembic (default), o si usa `op.execute()` raw, está envuelta en `BEGIN; ... COMMIT;`.
- [ ] Los nombres y tipos de columnas coinciden **exactamente** con la tabla del `spec_data_model.md` (mismo nombre, mismo tipo, mismas restricciones).
- [ ] Si la tabla tiene `organization_id`: `ENABLE ROW LEVEL SECURITY` está activado.
- [ ] La política RLS usa `current_setting('app.current_tenant_id')::uuid`.
- [ ] Los índices declarados en el SDD están creados (incluido `idx_<tabla>_org_*` y los compuestos para queries comunes).
- [ ] Las extensiones requeridas (`pgcrypto`) ya están enabled (no se duplica el `CREATE EXTENSION`).
- [ ] La migración tiene `downgrade()` implementada o al menos comentada explícitamente por qué no la tiene (ej: drop de audit log es destructivo y se omite a propósito).
- [ ] Hay un commit `migrate(módulo): ...` separado del commit `feat(módulo): ...`.

#### Endpoints REST (backend)

- [ ] Path y método HTTP coinciden con `sdd_03`. Prefijo `/v1`. Path en kebab-case plural (`/work-orders`, no `/workOrders`).
- [ ] Status code de respuesta exitosa coincide con el SDD (típicamente 200, 201, 202, 204).
- [ ] El **request body** acepta exactamente los campos del SDD, ni más ni menos.
- [ ] Las validaciones (Pydantic) cubren las reglas del SDD: longitudes mínimas/máximas, regex, tipos, valores enum.
- [ ] **Formato de error CUSTOM** (no RFC 7807): `{ "error": { "code", "message", "field", "details" } }`.
- [ ] Cada FA del SDD tiene un `error.code` específico (`CONTRACT_OVERLAP`, `LAST_OWNER_REQUIRED`, `INVITATION_EXPIRED`, etc.), no un `INTERNAL_ERROR` genérico.
- [ ] El rate-limit declarado en `sdd_04 §2.5` está aplicado al endpoint.
- [ ] `organization_id` se extrae del JWT, **nunca** del body/path/query (salvo en `/superadmin/*` donde es opcional).
- [ ] El endpoint declara el permiso requerido (decorator `@requires_permission("contract:read")` o equivalente).
- [ ] El access cross-tenant retorna **404** (NOT_FOUND), no 403.
- [ ] Endpoints async retornan `202 Accepted` con `{ data: { <job_id>, status, estimated_completion_seconds } }`.

#### Lógica de negocio (service + repository)

- [ ] Cada RN-XX del SDD relevante está implementada.
- [ ] Las invariantes críticas tienen un comentario `# RN-XX` o `# Enforces RN-XX` en la línea correspondiente.
- [ ] No hay lógica de negocio en el router; el router sólo orquesta.
- [ ] No hay SQL directo en el service; el service usa repository.
- [ ] El repository filtra por `organization_id` **explícitamente** en cada query (defense in depth; RLS es la segunda capa).
- [ ] Las transiciones de estado siguen la máquina especificada en el SDD (ej: `draft → active → terminated`).
- [ ] Los enums de DB usan CHECK constraints + valores `snake_case` (no PG ENUM types).
- [ ] Money fields usan `NUMERIC(14,2)` o `NUMERIC(14,4)`; nunca `FLOAT` ni `REAL`.
- [ ] Timestamps mutables tienen `created_at` + `updated_at` con `TIMESTAMPTZ`.

#### Workers async (Celery — backend)

- [ ] El worker actualiza `status` del job en cada transición (`pending → processing → completed | failed`).
- [ ] El procesamiento en batch respeta el tamaño máximo del SDD.
- [ ] Diferencia errores reintentables (429, 500, 502, 503, 504, timeouts) de no-reintentables (400, 404).
- [ ] El backoff sigue la política del SDD (típicamente 30s → 90s → 270s; con jitter).
- [ ] El campo `last_error` se puebla con información útil para debugging.
- [ ] El worker no bloquea el thread principal (usa `asyncio` o `gevent` según la config del proyecto).
- [ ] `request_id` se propaga desde el request HTTP que encoló el job hasta el log del worker.

#### Frontend: hooks y API clients

- [ ] El hook usa **TanStack Query** para fetch (server state), no Zustand.
- [ ] El client HTTP es Axios con `withCredentials: true` (para HttpOnly cookies).
- [ ] El interceptor de Axios maneja 401 con refresh automático + retry de la request original; si el refresh falla, redirige a `/login`.
- [ ] El client lee el formato de error **CUSTOM** (`error.code`, `error.message`, `error.field`, `error.details`), no RFC 7807.
- [ ] Los tipos del request/response vienen de `src/api/generated/` (generados desde OpenAPI), no redeclarados.
- [ ] No hay lógica de negocio en el frontend que pertenezca al backend (cálculos de ajuste por índice, validaciones de invariantes complejas).
- [ ] Descargas de archivos usan **Fetch + Blob**, no `window.open(download_url)`.

#### Frontend: pages y components

- [ ] Pages componen + rutean; **no** llaman a la API directamente.
- [ ] Components son presentacionales; reciben props y emiten eventos.
- [ ] Los 6 estados del flujo están manejados: `idle`, `loading`, `success`, `error`, `expired`, `empty`.
- [ ] Los mensajes de error de seguridad usan el texto **exacto** del SDD (anti-enumeration, login fallido, recovery codes).
- [ ] Las opciones de UI restringidas por rol se ocultan según `permissions[]` del JWT (no según `role_name`). En particular: `maintenance` nunca ve UI de contratos, cobranzas ni liquidaciones.
- [ ] Validaciones Zod alineadas con `sdd_02`: el frontend valida un subset, el backend valida el total.

#### Tests

- [ ] Cada CA-XX del SDD asignado a la tarea tiene un test con el ID exacto en el nombre.
- [ ] El docstring/describe contiene la descripción exacta del SDD.
- [ ] Test de aislamiento multi-tenant existe y pasa (backend).
- [ ] Tests de FA-XX están cubiertos (no sólo happy path).
- [ ] Tests de rate-limit existen para endpoints con límite declarado en `sdd_04`.
- [ ] Tests de integraciones externas (ICL, IPC, Resend) usan fixtures JSON, no llaman al servicio real.

### Cómo reportar una divergencia con el SDD

Usar el formato `[SDD-DIVERGENCE]` en el comentario del PR. Estructura:

```
[SDD-DIVERGENCE] <doc-del-SDD>.md §<sección> — <título corto>

El SDD especifica: <cita textual + ruta>
El código hace: <descripción concreta>

Opciones:
A) Corregir el código para alinearlo al SDD.
B) Actualizar el SDD si la implementación es más correcta técnicamente.
C) Reabrir la decisión y discutir en un nuevo issue [sdd:divergence].

Bloqueante: requiere resolución antes de aprobar.
```

Ejemplo real:

```
[SDD-DIVERGENCE] core/sdd_03_api_contracts.md §"Convenciones Generales" — Formato de error

El SDD especifica: el formato de error es CUSTOM, `{ "error": { "code", "message", "field", "details" } }`,
no RFC 7807.

El código retorna:
{
  "type": "https://adminprop.local/errors/period-locked",
  "title": "Conflict",
  "status": 409,
  "detail": "Period is locked"
}

Esto rompe el contrato con el frontend, que lee `error.code` para discriminar (ver
adminprop-front `CLAUDE.md` §5 "Formato de error esperado").

Opciones:
A) Corregir el ExceptionHandler para retornar el formato custom.
B) Actualizar el SDD si RFC 7807 es preferido — requiere también actualizar
   el frontend.

Bloqueante.
```

Si la divergencia se acepta como cambio del SDD: abrir un issue con label `sdd:divergence` (ver `github-project-workflow.md` §"Manejo de divergencias"), agregar también el label `status:blocked` al issue original, pausar el PR, esperar instrucción.

### Comportamiento esperado del autor del PR

Si el reviewer marca `[SDD-DIVERGENCE]`:

1. **No mergear** hasta resolver.
2. Si la divergencia es un bug del código: corregirlo y referenciar el comentario en el commit de fix.
3. Si la divergencia es del SDD: abrir issue `sdd:divergence` con label `status:blocked`, pausar el PR, esperar resolución del owner.

## Template

Template de review checklist (post-fetch del PR):

```bash
PR_NUMBER=<numero>
gh pr view "$PR_NUMBER" --json files,additions,deletions,title,body

# Listar archivos modificados para ubicar el alcance del review
gh pr diff "$PR_NUMBER" --name-only

# Ver el diff completo
gh pr diff "$PR_NUMBER"
```

Template de comentario inline en una línea específica:

```bash
gh pr comment "$PR_NUMBER" --body "$(cat <<'EOF'
[BLOCKING] Falta el filtro explícito por `organization_id` en la query.

El repository depende solo de RLS, lo cual es defensa única.
Ver backend `CLAUDE.md` §8 "Filtrar por organization_id explícitamente
en toda query con join entre tablas tenant-scoped" y el skill
`tenant-isolation.md`.

Fix sugerido: agregar `.filter(WorkOrderQuote.organization_id == tenant_id)`
antes del join con `work_orders`.
EOF
)"
```

Template de aprobación o rechazo:

```bash
# Aprobar
gh pr review "$PR_NUMBER" --approve --body "LGTM. Verificados CA-RF02-01 al -04, RN-01 al -06, test de tenant isolation, e2e de invitación."

# Pedir cambios
gh pr review "$PR_NUMBER" --request-changes --body "Ver comentarios [BLOCKING] inline. Tres divergencias con el SDD requieren resolución antes del merge."

# Comentario sin bloqueo
gh pr review "$PR_NUMBER" --comment --body "Algunos NIT inline + una sugerencia opcional sobre performance. Aprobado a discreción del autor."
```

## Checklist pre-commit

Para el reviewer:

- [ ] Leí el SDD referenciado en el PR antes de revisar el código.
- [ ] Verifiqué que cada CA-XX y RN-XX listado en el PR efectivamente está cubierto.
- [ ] Revisé los archivos en orden: migraciones → modelos → repository → service → router → tests.
- [ ] Confirmé que el formato de error sigue el contrato custom (no RFC 7807).
- [ ] Confirmé que `organization_id` se extrae del JWT, no del body/path.
- [ ] Confirmé que el test de aislamiento multi-tenant existe (backend) o que los estados del flujo están cubiertos (frontend).
- [ ] Marcé las divergencias con `[SDD-DIVERGENCE]` y no aprobé hasta resolverlas.
- [ ] Mi review final usa `gh pr review --approve | --request-changes | --comment`, no sólo comentarios sueltos.

Para el autor del PR:

- [ ] El PR linkea el issue (`Closes #N`) y declara el SDD de referencia.
- [ ] La sección "Divergencias del SDD detectadas" del template del PR está explícitamente con "Ninguna" o con detalle.
- [ ] Respondí a todos los `[BLOCKING]` y `[SDD-DIVERGENCE]` antes de pedir re-review.
- [ ] Si la divergencia se aceptó, abrí el issue `sdd:divergence` (con label `status:blocked`) y lo linkeé en el PR.

## Antipatrones

```python
# ❌ Review: aprobar sin verificar formato de error
"LGTM, status code 409 está correcto."
# Pero el body retorna { "detail": "Contract overlap" } sin error.code.

# ✅ Verificar que el shape exacto del error coincide con el SDD
[BLOCKING] El body del 409 no sigue el formato del SDD §"Convenciones
Generales". Debe ser:
{ "error": { "code": "CONTRACT_OVERLAP", "message": "...", "field": "start_date", "details": { "conflicting_contract_id": "..." } } }
```

```python
# ❌ Review: aprobar porque "todos los tests pasan"
"Tests verdes, aprobado."
# Pero no se verificó si los tests cubren los CA-XX correctos.

# ✅ Verificar la cobertura conceptual, no sólo numérica
[BLOCKING] La cobertura es 96% pero falta el test de CA-04-09
(pago que excede el saldo pendiente del contrato → PAYMENT_EXCEEDS_CONTRACT_BALANCE).
Es un FA crítico que el SDD §RF-04 documenta y el código sí maneja, pero sin test.
```

```python
# ❌ Review: aceptar "decisión de diseño" sin respaldo en el SDD
# Autor: "Agregué un campo cached_at en la respuesta para que el frontend sepa si los datos son frescos."

# ✅ Pedir respaldo del SDD
[SDD-DIVERGENCE] core/sdd_03_api_contracts.md §"Convenciones Generales" — Shape de respuesta

El SDD especifica:
{ "data": {...}, "meta": { "next_cursor": "...", "limit": 20 } }

El PR agrega un campo `cached_at` en `data`. No está en el SDD.

Opciones:
A) Quitar el campo del response.
B) Actualizar `sdd_03` y consensuar con frontend si el campo debe existir
   en data o en meta.
```

```bash
# ❌ Reviewer aprueba con only comments, sin pedir explícitamente changes
gh pr comment 42 --body "[BLOCKING] Falta filtro de tenant en la query"
gh pr comment 42 --body "[SDD-DIVERGENCE] Formato de error incorrecto"
# Pero nunca corre `gh pr review --request-changes`, así el autor cree que
# puede mergear.

# ✅ Marcar el review explícitamente
gh pr review 42 --request-changes --body "Dos blockers inline."
```

```bash
# ❌ Autor merge a develop con comentarios [BLOCKING] sin responder
git checkout develop
git merge feature/12-...
git push origin develop
# Causa: rompe el flujo de review; deja blockers sin resolución.

# ✅ Esperar resolución, responder cada [BLOCKING] con un fix o argumento,
# y dejar que el reviewer ejecute `gh pr review --approve` antes del merge.
```

## Referencias

- `core/sdd_03_api_contracts.md` §"Regla de oro" — ningún contrato API se modifica sin actualizar el SDD primero. Esto es el ancla del checklist de endpoints.
- `core/sdd_02_domain_model.md` §3 — catálogo de RN-XX que cada review verifica.
- `core/sdd_04_nonfunctional.md` §2.5 (rate limits) y §2.2 (auth/MFA) — checklist transversal.
- Backend `CLAUDE.md` §7 "Reglas de negocio globales" — invariantes que **siempre** deben verificarse en el review.
- Frontend `CLAUDE.md` §7 "Reglas de negocio relevantes para el frontend" — invariantes que se verifican en el review de frontend.
- `docs/sdd/_index.md` §4 — registro de decisiones tomadas que el reviewer puede citar al rechazar una divergencia.
- `github-project-workflow.md` — modelo de 3 estados del Project y labels `status:ready` / `status:blocked` / `sdd:divergence`.
