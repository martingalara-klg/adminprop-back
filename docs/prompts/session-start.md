# PROMPT DE SESIÓN — ADMINPROP BACKEND

Usar al inicio de cada sesión de implementación en `adminprop-back`.
Claude Code lee este archivo y opera autónomamente hasta tener un PR
listo en `In Progress` (el Project solo tiene Todo / In Progress / Done).

### Reglas de merge del proyecto

- **Todos los PRs van a `develop`** — siempre `--base develop`, sin excepciones.
- **El usuario mergea los PRs uno a uno** y marca cada tarea como done en el Project.
- **Cada sesión empieza desde `develop`** con `git pull origin develop` para incorporar los últimos merges del usuario antes de crear el branch de la nueva tarea.
- **No crear el branch desde otro feature branch**, aunque la tarea dependa de un PR aún sin mergear. El usuario maneja el orden de merge.

---

## VARIABLES DE SESIÓN (hardcoded — no editar)

```bash
REPO="adminprop-back"
ORG="martingalara-klg"
PROJECT_NUMBER=1   # AdminProp Backend — confirmado en el bootstrap (2026-08-06)

# Cachear IDs del Project una sola vez por sesión (gh project item-edit
# los requiere; calcularlos cada vez es ruido):
PROJECT_ID=$(gh project view "$PROJECT_NUMBER" --owner "$ORG" --format json | jq -r .id)
STATUS_FIELD_ID=$(gh project field-list "$PROJECT_NUMBER" --owner "$ORG" --format json \
  | jq -r '.fields[] | select(.name == "Status") | .id')
status_option() {
  gh project field-list "$PROJECT_NUMBER" --owner "$ORG" --format json \
    | jq -r ".fields[] | select(.name == \"Status\") | .options[] | select(.name == \"$1\") | .id"
}
# El Project tiene tres estados: "Todo" / "In Progress" / "Done"
# (no existe "In Review" ni "Blocked" en el Project de este repo)
STATUS_IN_PROGRESS_ID=$(status_option "In Progress")
```

---

## LECTURA OBLIGATORIA AL INICIO (en este orden)

1. `CLAUDE.md`
2. `docs/skills/git-workflow.md`
3. `docs/skills/github-project-workflow.md`
4. `docs/skills/code-review.md` (sólo §"Checklist por tipo de artefacto" para tener presente lo que el PR debe satisfacer)
5. **El SDD que la tarea declara** (lo extrae el comando de Fase 1.1)
6. **El skill backend específico** según el tipo de tarea:
   - `MIGRATION` → `docs/skills/database-migration.md` + `docs/skills/tenant-isolation.md` + `docs/runbooks/RUNBOOK-LOCAL-001-backend.md` §"cómo correr la migración localmente"
   - `ENDPOINT` → `docs/skills/api-endpoint.md` + `docs/skills/module-structure.md` + `docs/skills/error-handling.md` + `docs/skills/tenant-isolation.md`
   - `WORKER` → `docs/skills/async-worker.md` + `docs/skills/external-integrations.md` (si llama servicios externos) + `docs/skills/tenant-isolation.md`
   - `INFRA / EXTERNAL` → `docs/skills/external-integrations.md`
   - `TEST` (módulo nuevo) → `docs/skills/testing.md`

`docs/skills/testing.md` se aplica siempre en la Capa 4.

> Sin CD todavía: el merge a develop solo corre CI. El deploy se
> incorpora cuando exista infra.

---

## FASE 1 — SELECCIÓN DE TAREA

### 1.1 Estado actual del Project

```bash
# Issues disponibles para tomar
gh issue list --repo "$ORG/$REPO" --label "status:ready" \
  --json number,title,labels,body \
  | jq '.[] | {number, title, labels: [.labels[].name]}'

# Snapshot completo del Project agrupado por Status
gh project item-list "$PROJECT_NUMBER" --owner "$ORG" --format json \
  | jq '
    .items
    | group_by(.status)
    | map({status: .[0].status, items: map({number: .content.number, title: .content.title})})
  '
```

### 1.2 Reglas de selección (en orden)

1. Sólo issues con `status:ready` (los `status:blocked` no se tocan).
2. Priorizar por **fase del roadmap** (Fase 0 > Fase 1 > Fase 2 > …). El orden canónico de capas está en `spec_data_model.md §"Orden de Migración"` (Fundación → Personas → Propiedades → Contratos → Cobranzas → Mantenimiento → Liquidaciones → Notificaciones/Auditoría).
3. Dentro de la misma fase: **el que desbloquea más issues** (revisar `## Bloquea a` / `## Depende de` del body del issue).
4. Desempate: complejidad **Baja > Media > Alta**.

### 1.3 Presentar plan y esperar confirmación

Única pausa planificada. Salida exacta:

```
TAREA SELECCIONADA — BACKEND
──────────────────────────────────────────────
Issue:       #<N> — <título>
Fase:        <fase>
Tipo:        <MIGRATION | MODEL | SERVICE | ENDPOINT | WORKER | INFRA | TEST>
SDD:         docs/sdd/<ruta>.md §<sección, sección>
Skills:      docs/skills/<skill-1>.md, docs/skills/<skill-2>.md
Complejidad: <Baja | Media | Alta>

DESBLOQUEA AL CERRAR
──────────────────────
- #<N> <título>

PLAN POR CAPAS
───────────────
Capa 1: <migración Alembic / extensión / seed>
Capa 2: <models + repository + service>
Capa 3: <router + schemas Pydantic | worker Celery>
Capa 4: <tests integration + tenant isolation>

ARCHIVOS A CREAR / MODIFICAR
──────────────────────────────
- src/adminprop/db/migrations/versions/<YYYYMMDD_HHMMSS>_<slug>.py
- src/adminprop/modules/<módulo>/{router,service,repository,schemas,models,exceptions}.py
- tests/integration/<módulo>/test_<feature>.py
- tests/integration/<módulo>/test_tenant_isolation.py

CRITERIOS DE DONE (extraídos del issue)
────────────────────────────────────────
- [ ] CA-XX-01: <descripción>
- [ ] CA-XX-02: <descripción>

¿Algo a revisar antes de comenzar? Silencio = proceder.
```

⏸ **ÚNICA PAUSA PLANIFICADA.** Próxima pausa sólo ante bloqueante real (ver "Cuándo pausar").

---

## FASE 2 — INICIO EN GITHUB

```bash
ISSUE_NUMBER=<seleccionado en Fase 1>

# Leer issue completo (incluye SDD ref, CA-XX y RN-XX)
gh issue view "$ISSUE_NUMBER" --repo "$ORG/$REPO"

# SLUG: 3–5 palabras kebab-case derivadas del título del issue
SLUG="<slug-descriptivo>"

# Extraer el milestone del issue (se aplicará también al PR)
MILESTONE=$(gh issue view "$ISSUE_NUMBER" --repo "$ORG/$REPO" --json milestone --jq '.milestone.title')

# Obtener ID del item en el Project
ITEM_ID=$(gh project item-list "$PROJECT_NUMBER" --owner "$ORG" --format json \
  | jq -r --argjson n "$ISSUE_NUMBER" '.items[] | select(.content.number == $n) | .id')

# Branch SIEMPRE desde develop — nunca desde otro feature branch
git fetch origin
git checkout develop
git pull origin develop   # incorpora los merges que el usuario haya hecho
git checkout -b "feature/${ISSUE_NUMBER}-${SLUG}"

# Mover a "In Progress" en el Project
gh project item-edit \
  --project-id "$PROJECT_ID" \
  --id "$ITEM_ID" \
  --field-id "$STATUS_FIELD_ID" \
  --single-select-option-id "$STATUS_IN_PROGRESS_ID"

# Labels disponibles en este repo: status:ready y status:blocked únicamente
# (no existen status:in-progress, status:in-review — no intentar agregarlos)

echo "✓ Branch: feature/${ISSUE_NUMBER}-${SLUG}"
echo "✓ Issue #$ISSUE_NUMBER → In Progress"
```

---

## FASE 3 — IMPLEMENTACIÓN

Implementar sin pausas salvo bloqueante real. Un commit por capa
completada y verificada. Convenciones de commit en `git-workflow.md`
(Conventional Commits + footer `Closes #N` + `Implements: CA-XX` + `Rule: RN-XX`).

### Capa 1 — Migración Alembic + seed (si aplica)

Aplica `docs/skills/database-migration.md` literal. Reglas no negociables:

- Archivo: `src/adminprop/db/migrations/versions/YYYYMMDD_HHMMSS_<slug>.py` (timestamp UTC).
- SQL con `op.execute(...)` raw (autogenerate falla con RLS, CHECK, índices parciales).
- Nombres y tipos **idénticos** a `spec_data_model.md`. Money: `NUMERIC(14,2|4)`. Timestamps: `TIMESTAMPTZ`. PKs: `UUID DEFAULT gen_random_uuid()`. Enums: `TEXT + CHECK`.
- **Toda tabla con `organization_id`** lleva:
  ```sql
  ALTER TABLE <t> ENABLE ROW LEVEL SECURITY;
  CREATE POLICY <t>_tenant_isolation ON <t>
    USING (organization_id = current_setting('app.current_tenant_id', true)::uuid)
    WITH CHECK (organization_id = current_setting('app.current_tenant_id', true)::uuid);
  ALTER TABLE <t> FORCE ROW LEVEL SECURITY;
  ```
- Seeds globales: idempotentes (`ON CONFLICT DO NOTHING`). Seeds per-tenant viven en `OrganizationProvisioningService`, **no** en migración.
- `downgrade()` implementada (o comentada con justificación si la tabla es append-only).

```bash
git add src/adminprop/db/migrations/versions/
git commit -m "migrate(<módulo>): <descripción imperativa>

<qué cambia y por qué — máx 3 líneas, cita SDD>

Closes #${ISSUE_NUMBER}
Implements: CA-XX-NN
Rule: RN-XX"
```

### Capa 2 — Models + Repository + Service

Aplica `docs/skills/module-structure.md`. Estructura canónica:

```
src/adminprop/modules/<módulo>/
├── __init__.py
├── router.py        ← Fase 3
├── service.py       ← esta capa — implementa RN-XX
├── repository.py    ← esta capa — SQL; SIEMPRE filtra organization_id
├── schemas.py       ← Pydantic v2, PascalCase singular
├── models.py        ← SQLAlchemy 2.0, PascalCase singular
└── exceptions.py    ← una subclase de AdminPropException por error.code del SDD
```

Reglas:

- `repository.py` recibe `organization_id: UUID` en cada método y lo aplica en `WHERE` (defense in depth sobre RLS). Sin excepciones.
- `service.py` no hace SQL; orquesta repositorios y enforza RN-XX. Cada invariante crítica lleva `# RN-XX` en la línea correspondiente.
- Excepciones de dominio heredan de `AdminPropException` y declaran `status_code` + `error_code` (ver `error-handling.md`). **Nunca** `raise HTTPException(detail="...")`.

```bash
git add src/adminprop/modules/<módulo>/{models,repository,service,schemas,exceptions}.py
git commit -m "feat(<módulo>): <descripción imperativa>

Implements: CA-XX-NN
Rule: RN-XX"
```

### Capa 3 — Endpoint (REST) o Worker (Celery)

#### Si es ENDPOINT — aplica `docs/skills/api-endpoint.md`

Reglas:

- Prefijo `/v1` (no `/api/v1`). Path kebab-case plural (`/v1/contracts`).
- Método HTTP, status code y permiso requerido **exactos** al `sdd_03`.
- `organization_id` siempre desde `Depends(get_current_tenant)`. **Nunca** del body/path/query (excepción: `/superadmin/*`).
- Permiso atómico vía `Depends(requires_permission("<resource>:<action>"))` — leer del array `permissions[]` del JWT, nunca por `role_name`.
- Rate limit del `sdd_04 §2.5` aplicado si corresponde.
- **Formato de error CUSTOM** (NO RFC 7807): `{ "error": { "code", "message", "field", "details" } }`. Mapeo en el handler global de `AdminPropException`.
- Cross-tenant retorna **404 NOT_FOUND** (no 403). El repository devuelve `None` → el router lanza `NotFoundException`.
- Operaciones > 5s: encolar en Celery, retornar `202 Accepted` con `{ data: { <id>, status, estimated_completion_seconds } }`.
- Mensajes anti-enumeration **literales del SDD**: `forgot-password` siempre 200, `login` con `"Credenciales incorrectas."` para email-no-existe y password-incorrecta.

#### Si es WORKER — aplica `docs/skills/async-worker.md`

Reglas:

- Archivo en `src/adminprop/workers/<worker>.py`. Importado en `celery_app.include`.
- Recibe IDs como string (no objetos ORM).
- **Antes de cualquier query** llama a `set_tenant_context(session, organization_id)` — los workers no tienen middleware que lo setee.
- Actualiza `<resource>.status` en cada transición (`pending → processing → completed | failed`).
- Diferencia `RetryableError` (429, 5xx, timeout — autoretry) vs `NonRetryableError` (400, 401, regla de negocio — failed inmediato + notificación).
- `max_retries`, `retry_backoff`, `retry_jitter` según política del `sdd_04 §1.3`.
- `request_id` se propaga al log y a notificaciones generadas.

```bash
git add src/adminprop/modules/<módulo>/router.py   # o src/adminprop/workers/<worker>.py
git commit -m "feat(<módulo>): <descripción imperativa>

Implements: CA-XX-NN
Rule: RN-XX"
```

### Capa 4 — Tests

Aplica `docs/skills/testing.md`. Reglas:

- Ruta: `tests/integration/<módulo>/test_<feature>.py`.
- Nombre del test: `test_<id_lowercase>_<descripcion_snake_case>` (ej: `test_rf02_01_super_admin_creates_org_in_pending_owner`).
- Docstring contiene el texto **exacto** del CA del SDD.
- Cada CA-XX del issue → un test.
- Cada FA del SDD (token expirado, slug taken, hours > 24, …) → un test dedicado, no catch genérico.
- **Test de aislamiento multi-tenant obligatorio** si el módulo toca datos: `tests/integration/<módulo>/test_tenant_isolation.py` que verifica GET, LIST, PATCH, DELETE cross-tenant → 404 (no 403).
- Tests verifican el `error.code` exacto, no sólo el status code.
- Si toca integraciones externas (ICL vía API del BCRA, IPC vía datos.gob.ar/INDEC, email transaccional vía Resend): usar fixtures/mocks deterministas en `tests/fixtures/<integración>/*.json`, **nunca** llamar al servicio externo real desde CI.
- Si toca rate-limit del `sdd_04 §2.5`: test que llega al límite y verifica 429 + header `Retry-After`.

```bash
git add tests/
git commit -m "test(<módulo>): cover CA-XX-01 to CA-XX-N + tenant isolation

Covers: CA-XX-01, CA-XX-02, ..., CA-XX-N"
```

---

## CUÁNDO PAUSAR

Sólo interrumpir ante bloqueantes reales. Para todo lo demás, decidir y
continuar usando el SDD como fuente de verdad.

```
⛔ BLOQUEANTE TIPO [A|B|C|D] — <título corto>
───────────────────────────────────────────────
Tipo:
  A — El SDD no especifica el comportamiento para un caso real
  B — Lo del SDD no es implementable con el stack del proyecto
  C — Falta algo que debía existir de un issue anterior
  D — Decisión de seguridad / aislamiento no resuelta en el SDD

Contexto:  <dónde estaba implementando>
Problema:  <descripción precisa>
SDD dice:  <cita textual con ruta + sección, o "no especificado">

Opciones:
  1. <opción A — implicaciones>
  2. <opción B — implicaciones>

Recomendación: Opción <N> — <razón técnica objetiva>

Necesito tu decisión para continuar.
```

Si el bloqueante es tipo A o D y requiere actualización de SDD, además
abrir el issue de divergencia (ver `github-project-workflow.md` §"Manejo
de divergencias"), mover el issue actual a `status:blocked` y esperar.

---

## FASE 4 — PULL REQUEST

### 4.1 Checklist pre-PR (silencioso — corregir antes de abrir)

- [ ] Suite local en verde (`pytest`).
- [ ] Cobertura del módulo afectado ≥ 95% (excluyendo DTOs, modelos declarativos sin método custom y boilerplate).
- [ ] Cada RN-XX implementada lleva comentario `# RN-XX` en la línea correspondiente.
- [ ] Cada CA-XX del issue tiene un test con el nombre exacto.
- [ ] Cada FA del SDD tiene manejo explícito (no catch genérico).
- [ ] Path + método + status code + permiso del endpoint **idénticos** al `sdd_03`.
- [ ] Formato de error **custom** (`{ "error": { "code", ... } }`), no RFC 7807.
- [ ] Toda query del repository filtra `organization_id` explícitamente (no sólo RLS).
- [ ] Tablas nuevas con `organization_id` tienen RLS + política + `FORCE ROW LEVEL SECURITY`.
- [ ] Test de aislamiento cross-tenant existe y retorna 404 (no 403).
- [ ] `organization_id` siempre del JWT vía `Depends(get_current_tenant)`, nunca del request.
- [ ] Endpoints async retornan 202 + `{ data: { <id>, status, estimated_completion_seconds } }`.
- [ ] Si toca Resend: clasifica errores en Retryable* vs NonRetryable*; secretos via variables de entorno locales (`.env`, no commiteado); migrar a un gestor de secretos cuando exista infra cloud.
- [ ] Logs JSON con `request_id`, `organization_id`, `user_id`, `service`; sin campos sensibles (password, tokens, bank_info).
- [ ] No hay decisiones de diseño sin respaldo en el SDD.
- [ ] No hay código fuera del scope del issue.

### 4.1.1 Checklist operativo (silencioso — corregir antes de abrir)

- [ ] Si la tarea agrega una variable de entorno nueva: actualizar `.env.example`.
- [ ] Si la tarea cambia el setup local: actualizar `docs/runbooks/RUNBOOK-LOCAL-001-backend.md`.

### 4.2 Push

```bash
# El branch ya nació de develop (Fase 2) — no se necesita rebase adicional
git push -u origin "feature/${ISSUE_NUMBER}-${SLUG}"
```

### 4.3 Crear el PR

```bash
gh pr create --repo "$ORG/$REPO" --base develop \
  --title "[#${ISSUE_NUMBER}] <título descriptivo>" \
  --assignee "@me" \
  --milestone "$MILESTONE" \
  --body "$(cat <<EOF
## Tarea
Closes #${ISSUE_NUMBER}

## SDD de referencia
- Documento: \`docs/sdd/<ruta>.md\`
- Secciones: <X.Y>, <X.Z>

## Criterios de aceptación implementados
- [x] CA-XX-01: <descripción exacta del SDD>
- [x] CA-XX-02: <descripción exacta del SDD>

## Reglas de negocio implementadas
- RN-XX: \`src/adminprop/modules/<módulo>/service.py:<línea>\`
- RN-YY: \`src/adminprop/modules/<módulo>/repository.py:<línea>\`

## Decisiones de implementación
<Decisiones tomadas no explícitas en el SDD. "Ninguna" si todo estaba especificado.>

## Divergencias del SDD detectadas
<"Ninguna" o lista con link al issue \`sdd:divergence\`>

## Checklist del autor
- [x] Path/método/status code coinciden con sdd_03
- [x] Formato de error CUSTOM (no RFC 7807)
- [x] organization_id desde JWT (no body/path)
- [x] Cross-tenant retorna 404 (no 403)
- [x] RLS habilitado + FORCE en tablas nuevas
- [x] Test de aislamiento multi-tenant incluido y pasa
- [x] FA-XX cubiertos individualmente
- [x] Tests con nombre CA-XX-NN y docstring del SDD
EOF
)"

PR_URL=$(gh pr view --repo "$ORG/$REPO" --json url --jq .url)
PR_NUMBER=$(gh pr view --repo "$ORG/$REPO" --json number --jq .number)
```

### 4.4 Asociación bidireccional PR ↔ issue + actualización de TODOs

Tras crear el PR, ejecutar **los tres pasos** sin omitir ninguno. La
asociación bidireccional vive en GitHub así:

- **Forward** (PR → issue): el body del PR tiene `Closes #N` (paso 4.3 ya
  lo escribió). Esto crea el "Linked pull request" automático en el
  sidebar del issue y dispara el cierre al mergear.
- **Backward** (issue → PR): comentario explícito en el issue + edición
  del body del issue con sección `## Pull Request`. Hace el link visible
  en el feed del issue y queda persistente.
- **Refuerzo simétrico**: comentario en el PR linkeando al issue (para
  navegación rápida desde el panel de reviewers).

```bash
# ─── Paso 1: PR al Project (idempotente; a veces GitHub lo agrega solo)
gh project item-add "$PROJECT_NUMBER" --owner "$ORG" --url "$PR_URL"
# El Project se mantiene en "In Progress" — el usuario lo mueve a "Done"
# al mergear. No intentar usar STATUS_IN_REVIEW_ID (no existe en este repo).

# ─── Paso 2: actualizar el body del issue
#  (a) marcar como completados los TODOs de los CA-XX cubiertos por el PR
#  (b) agregar sección "## Pull Request" linkeando al PR

# 2.a — Descargar body actual del issue
ISSUE_BODY_FILE="/tmp/adminprop-issue-${ISSUE_NUMBER}-body.md"
gh issue view "$ISSUE_NUMBER" --repo "$ORG/$REPO" --json body --jq .body > "$ISSUE_BODY_FILE"

# 2.b — Marcar los checkboxes de los CA-XX cubiertos.
# Lista de CA cubiertos = los criterios de done que pasaron al PR (Fase 1.3).
# Estrategia: una línea sed por cada CA. Reemplaza solo si el checkbox
# está vacío (`- [ ]`); preserva indentación y texto siguiente.
COVERED_CAS=("CA-XX-01" "CA-XX-02")   # editar con los CA reales del issue
for CA in "${COVERED_CAS[@]}"; do
  sed -i -E "s|^(\s*)- \[ \] (${CA}\b)|\1- [x] \2|g" "$ISSUE_BODY_FILE"
done

# 2.c — Agregar sección "## Pull Request" si no existe ya
if ! grep -q "^## Pull Request" "$ISSUE_BODY_FILE"; then
  cat >> "$ISSUE_BODY_FILE" <<EOF

## Pull Request
- $PR_URL (abierto $(date -u +%Y-%m-%dT%H:%M:%SZ))
EOF
fi

# 2.d — Subir el body actualizado al issue
gh issue edit "$ISSUE_NUMBER" --repo "$ORG/$REPO" --body-file "$ISSUE_BODY_FILE"

# ─── Paso 3: comentarios cross-referenciados
gh issue comment "$ISSUE_NUMBER" --repo "$ORG/$REPO" \
  --body "PR abierto: $PR_URL — TODOs cubiertos actualizados en el cuerpo del issue."

gh pr comment "$PR_NUMBER" --repo "$ORG/$REPO" \
  --body "Implementa #${ISSUE_NUMBER} — ver el cuerpo del issue para CA-XX cubiertos, RN-XX y SDD de referencia."

echo "✓ PR #$PR_NUMBER ↔ issue #$ISSUE_NUMBER (forward via 'Closes', backward via comentario + body)"
echo "✓ Milestone: $MILESTONE"
```

**Verificación rápida** (opcional, sólo si dudás de que el link quedó
firme):

```bash
# El issue debe listar el PR en su sección "Linked pull requests"
gh api "repos/$ORG/$REPO/issues/$ISSUE_NUMBER/timeline" \
  --jq '.[] | select(.event == "cross-referenced") | .source.issue.html_url' \
  | grep -F "$PR_URL" && echo "✓ Link bidireccional confirmado"
```

### 4.5 Resumen de sesión

```
✅ SESIÓN BACKEND COMPLETADA
────────────────────────────────────────────────────
Issue:   #<N> — <título>
Branch:  feature/<N>-<slug>
PR:      <PR_URL> (base: develop)
Estado:  In Progress — el usuario mergea y cierra la tarea

CRITERIOS DE ACEPTACIÓN
CA-XX-01 ✓  CA-XX-02 ✓  ...

ISSUES POTENCIALMENTE DESBLOQUEADOS (revisar tras el merge)
#<N> <título> — ejecutar para desbloquear:
gh issue edit <N> --repo $ORG/$REPO \
  --add-label "status:ready" \
  --remove-label "status:blocked"

DOCUMENTOS OPERATIVOS A ACTUALIZAR
Si esta tarea introdujo cambios en la infraestructura o en la
operación del sistema, actualizar ANTES de cerrar la sesión:

[ ] .env.example                         — si se agregó env var nueva
[ ] RUNBOOK-LOCAL-001-backend.md         — si cambiaron pasos de setup local

Si ninguno aplica: continuar con la próxima sesión.

PRÓXIMA SESIÓN
Releer este prompt en una nueva sesión de Claude Code para
continuar con la siguiente tarea del roadmap.
────────────────────────────────────────────────────
```

---

## NUNCA HACER (regla de oro)

- **Usar `--base feature/...` en un PR.** Todos los PRs van a `develop` sin excepción.
- **Crear el branch desde otro feature branch.** Siempre desde `develop` con `git pull origin develop`.
- **Omitir el bloque de actualización del body del issue** (Fase 4.4 paso 2). Marcar los TODOs en el issue es parte del contrato de "PR listo".
- **Omitir el comentario cross-referenciado** en el PR (Fase 4.4 paso 3). El `Closes #N` es necesario pero no suficiente — el comentario hace la asociación visible para el reviewer.
- Cerrar el issue antes del merge del PR.
- Modificar un contrato API sin actualizar `sdd_03` primero.
- Inventar un `error.code` que no existe en `sdd_03 §"Códigos de Error Globales"`.
- Inventar un mensaje de seguridad (anti-enumeration). Usar el texto literal del SDD.
- Filtrar por sólo RLS sin `WHERE organization_id = $org` explícito.
- Retornar 403 para acceso cross-tenant. Siempre 404.
- Aceptar `organization_id` en body/path/query (excepto `/superadmin/*`).
- Saltar hooks (`--no-verify`) o forzar push (`--force` sin `--with-lease`).
