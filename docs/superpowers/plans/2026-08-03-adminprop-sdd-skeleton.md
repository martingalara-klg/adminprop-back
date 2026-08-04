# AdminProp — Esqueleto SDD (Paso 1 del diseño) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Copiar y adaptar el esqueleto reutilizable del sistema SDD de clarix (skills, prompts de sesión, workflows CI, runbooks locales) a `adminprop-back` y `adminprop-front`, dejando ambos repos listos para recibir los SDDs de producto (pasos 2–6 del diseño).

**Architecture:** Trabajo de copia-y-adaptación documental. Cada tarea lee archivos fuente de `clarix-backend`/`clarix-frontend`, escribe la versión adaptada en `adminprop-back`/`adminprop-front` aplicando las reglas de transformación globales + las notas específicas de la tarea, verifica con grep que no queden referencias a clarix, y commitea. No se escribe código de aplicación.

**Tech Stack:** Markdown, YAML (GitHub Actions), bash (grep de verificación), git.

**Spec:** `adminprop-back/docs/superpowers/specs/2026-08-03-adminprop-sdd-design.md`

## Global Constraints

- Rama de trabajo: `develop` en ambos repos (en `adminprop-front` hay que crearla — Task 5). Commits locales, **sin push** (el usuario decide cuándo pushear).
- Commits en español, Conventional Commits, **sin** bloque `Co-Authored-By`.
- Rutas fuente: `C:\Users\Martin\Desktop\WORK\KLG\clarix-backend` y `C:\Users\Martin\Desktop\WORK\KLG\clarix-frontend`. Rutas destino: `C:\Users\Martin\Desktop\WORK\KLG\adminprop-back` y `C:\Users\Martin\Desktop\WORK\KLG\adminprop-front`.
- **No copiar:** `infra/terraform/`, `docker/`, runbooks de deploy (`RUNBOOK-DEPLOY-*`, `RUNBOOK-OPS-*`, `INFRA-SETUP-OVERVIEW`), workflows `cd-staging.yml` / `cd-production.yml` / `backport-master-to-develop.yml`, `docs/sdd/**` (los SDDs de adminprop se escriben nuevos en pasos 2–5), `docs/afip-setup.md`, `docs/spikes/`, `CLAUDE.md` (se escribe en paso 6).
- Los links hacia `docs/sdd/*` en los archivos adaptados **pueden apuntar a archivos que aún no existen** (se crean en pasos 2–5) siempre que la ruta coincida con el árbol del diseño §5. Los links hacia skills/prompts/runbooks **deben resolver** a archivos creados por este plan.
- Después de cada Write, verificar con los greps indicados antes de commitear.

### Reglas de transformación globales (aplican a TODOS los archivos)

| Buscar (case-sensitive) | Reemplazar por |
|---|---|
| `clarix-backend` | `adminprop-back` |
| `clarix-frontend` | `adminprop-front` |
| `jose-kleiner-klg` | `martingalara-klg` |
| `Clarix` | `AdminProp` |
| `clarix` (resto de casos: identificadores, rutas) | `adminprop` |
| `src/clarix/` | `src/adminprop/` |
| `ClarixException` | `AdminPropException` |
| `clarix_app` / `clarix_superadmin` (roles PG) | `adminprop_app` / `adminprop_superadmin` |
| `api.clarix.io` / `app.clarix.io` / `admin.clarix.io` | `api.adminprop.local` / `app.adminprop.local` / `admin.adminprop.local` (dominios definitivos se deciden con la infra; nota inline: `<!-- dominio provisorio hasta definir infra -->`) |

### Reglas de contenido globales

1. **Quitar todo lo AFIP/WSAA/WSFE/zeep/CAE**: secciones, ejemplos, fixtures. Donde el texto necesite un ejemplo de integración externa, usar los de adminprop: obtención del índice **ICL (API del BCRA)** / **IPC (datos.gob.ar, INDEC)** y **email transaccional (Resend)**.
2. **Quitar referencias a infra cloud GCP** (Cloud Run, Secret Manager, KMS, GCS, Memorystore, Datastream, runbooks de deploy, `sdd_infra_*`). Donde se hable de secretos: "variables de entorno locales (`.env`, no commiteado); migrar a un secret manager cuando exista infra cloud". Donde se hable de storage de archivos (PDFs de recibos/liquidaciones, comprobantes de cotizaciones): "filesystem local vía volumen Docker en MVP; storage cloud post-infra".
3. **Ejemplos de dominio**: donde clarix usa `credit-notes`, `timesheets`, `invoices`, `minutas` como entidad de ejemplo, usar entidades adminprop: `contratos` (`/v1/contracts`), `cobros` (`/v1/payments`), `liquidaciones` (`/v1/settlements`), `ordenes-trabajo` (`/v1/work-orders`), `propiedades` (`/v1/properties`). Mantener la mecánica del ejemplo idéntica (solo cambia el sustantivo).
4. **Workers de ejemplo**: donde clarix lista sus 6 workers Celery, usar los ilustrativos de adminprop con nota: `indices_worker` (obtiene índices y aplica ajustes programados), `notification_worker` (email + in-app), `documents_worker` (PDFs de recibos y liquidaciones). Agregar la línea: *"Lista canónica de workers: se define en `sdd_04_nonfunctional.md` (paso 4 del diseño); estos son ilustrativos."*
5. **Roles**: donde clarix usa `owner`/`admin`/`developer`, adminprop usa `owner`/`admin`/`maintenance`. El ejemplo de restricción de rol pasa de "developer no ve datos financieros" a "**maintenance solo accede al módulo de mantenimiento** (órdenes de trabajo asignadas y sus cotizaciones); nunca a contratos, cobranzas ni liquidaciones".
6. **Referencias a SDDs**: los nombres de archivo SDD de clarix se mapean al árbol del diseño §5. Mapa: `sdd_01_prd` → igual; `sdd_02_domain_model` → igual; `sdd_03_api_contracts` → igual; `sdd_04_nonfunctional` → igual; `spec_data_model` → igual (en `infrastructure/`); `spec_module_00_superadmin` → igual; `spec_module_06_administracion` → `spec_module_07_administracion`; `spec_module_05_notificaciones` → `spec_notificaciones`; módulos de features de clarix (01 minutas, 02 timesheets, 03 staff, 04 financiero, 07 indicadores, 08 reportes) → **no mapear**: si un skill los cita como ejemplo, reemplazar por el spec adminprop temáticamente análogo (`spec_module_03_contratos`, `spec_module_04_cobranzas`, `spec_module_05_liquidaciones`, `spec_module_06_mantenimiento`). Citas a secciones específicas (`§2.5`, `§4.4`) se conservan como referencia al doc adminprop equivalente — los SDDs de los pasos 2–5 deberán cubrir esos temas.
7. **Frontmatter**: conservar el formato de frontmatter YAML de cada archivo fuente, actualizando `name`, `description` y `fecha: 2026-08-03`.

---

### Task 1: Skills de workflow GitHub — backend

**Files:**
- Create: `adminprop-back/docs/skills/git-workflow.md` (fuente: `clarix-backend/docs/skills/git-workflow.md`)
- Create: `adminprop-back/docs/skills/github-project-workflow.md` (fuente: `clarix-backend/docs/skills/github-project-workflow.md`)

**Interfaces:**
- Consumes: nada (primera tarea).
- Produces: los dos skills que `session-start.md` (Task 3) lista como lectura obligatoria 2 y 3. Nombres de archivo exactos: `git-workflow.md`, `github-project-workflow.md`.

**Notas específicas:**
- En `git-workflow.md`: la tabla "Stack relevante" queda con hosting `github.com/martingalara-klg/adminprop-back` y `adminprop-front`, CLI `gh` autenticado como `martingalara-klg`. Los ejemplos de nombres de branch usan issues de adminprop (ej: `feature/12-crear-tabla-properties`, `feature/27-worker-ajuste-icl`, `bugfix/45-calculo-punitorios-redondeo`).
- En `github-project-workflow.md`: todo lo que mencione el Project de clarix pasa a "Project del repo (owner `martingalara-klg`; el número se confirma en el bootstrap — paso 7 del diseño)". Mantener labels `status:ready` / `status:blocked` / `sdd:divergence` y estados Todo / In progress / Done. Mantener íntegra la mecánica de "Manejo de divergencias" (issue `sdd:divergence` + mover a blocked).

- [ ] **Step 1: Leer los dos archivos fuente completos** (Read).
- [ ] **Step 2: Escribir las dos versiones adaptadas** aplicando reglas globales + notas.
- [ ] **Step 3: Verificar**

Run (Git Bash, desde `adminprop-back`):
```bash
grep -riE "clarix|jose-kleiner|afip|wsaa" docs/skills/ && echo "FALLO: quedaron referencias" || echo OK
```
Expected: `OK` (cero matches).

- [ ] **Step 4: Commit**

```bash
git add docs/skills/git-workflow.md docs/skills/github-project-workflow.md
git commit -m "docs: skills de workflow git y github project adaptados de clarix"
```

---

### Task 2: Skills de patrones de código — backend

**Files (fuente → destino, mismo nombre, en `docs/skills/`):**
- Create: `adminprop-back/docs/skills/module-structure.md`
- Create: `adminprop-back/docs/skills/api-endpoint.md`
- Create: `adminprop-back/docs/skills/error-handling.md`
- Create: `adminprop-back/docs/skills/database-migration.md`
- Create: `adminprop-back/docs/skills/tenant-isolation.md`
- Create: `adminprop-back/docs/skills/testing.md`
- Create: `adminprop-back/docs/skills/async-worker.md`
- Create: `adminprop-back/docs/skills/external-integrations.md`
- Create: `adminprop-back/docs/skills/code-review.md`

**Interfaces:**
- Consumes: nada.
- Produces: los 9 skills que `session-start.md` (Task 3) mapea por tipo de tarea (`MIGRATION`, `ENDPOINT`, `WORKER`, `INFRA/EXTERNAL`, `TEST`). Nombres de archivo exactos como arriba.

**Notas específicas:**
- `module-structure.md`: la estructura canónica del módulo queda idéntica (router/service/repository/schemas/models/exceptions/tests) bajo `src/adminprop/modules/<módulo>/`. Lista de módulos de ejemplo: `properties`, `people`, `contracts`, `payments`, `settlements`, `maintenance`, `admin`, `superadmin`, `notifications`.
- `api-endpoint.md`: mantener íntegras las reglas transversales (prefijo `/v1`, kebab-case plural, `organization_id` de `Depends(get_current_tenant)`, permisos atómicos, formato de error custom, cross-tenant 404, async 202). Ejemplo principal: `POST /v1/contracts` en lugar del ejemplo clarix.
- `database-migration.md`: mantener literal el bloque RLS (`ENABLE ROW LEVEL SECURITY` + política + `FORCE`), naming `YYYYMMDD_HHMMSS_<slug>.py`, `op.execute` raw, money `NUMERIC(14,2|4)`, `TIMESTAMPTZ`, UUID `gen_random_uuid()`, TEXT+CHECK. Ejemplo: tabla `contracts` con `organization_id`.
- `tenant-isolation.md`: sin cambios de fondo — es el skill más transferible. Solo renombres y ejemplos (query de `payments` filtrada por `organization_id`).
- `testing.md`: mantener naming `test_<ca_id>_<descripcion>`, docstring con texto exacto del CA, test de aislamiento cross-tenant obligatorio (404), verificación de `error.code` exacto. Quitar fixtures AFIP; los mocks de servicios externos de ejemplo son: respuesta JSON del índice ICL (BCRA) y webhook de Resend. Cobertura mínima: **igual que clarix, 95%** con las mismas exclusiones (DTOs, modelos declarativos, boilerplate).
- `async-worker.md`: mantener `set_tenant_context` obligatorio antes de queries, transiciones de status, `RetryableError` vs `NonRetryableError`, propagación de `request_id`. Ejemplo: `indices_worker.aplicar_ajuste_contrato(contract_id: str, organization_id: str)`.
- `external-integrations.md`: reescribir la sección de servicios concretos: (a) índice ICL — API pública BCRA, (b) IPC — datos.gob.ar/INDEC, (c) email Resend. Mantener el patrón de clasificación de errores y la regla "nunca llamar al servicio real desde CI — fixtures".
- `code-review.md`: mantener el "Checklist por tipo de artefacto" (session-start lo referencia por ese nombre de sección — **conservar el heading literal**).

- [ ] **Step 1: Leer los 9 archivos fuente** (Read, pueden ser varios en paralelo).
- [ ] **Step 2: Escribir las 9 versiones adaptadas.**
- [ ] **Step 3: Verificar**

```bash
grep -riE "clarix|jose-kleiner|afip|wsaa|wsfe|zeep|minuta|timesheet|invoice|credit.note" docs/skills/ && echo "FALLO" || echo OK
grep -l "Checklist por tipo de artefacto" docs/skills/code-review.md
```
Expected: `OK` + la segunda línea imprime la ruta del archivo.

- [ ] **Step 4: Commit**

```bash
git add docs/skills/
git commit -m "docs: skills de patrones backend adaptados de clarix"
```

---

### Task 3: Prompt de sesión — backend

**Files:**
- Create: `adminprop-back/docs/prompts/session-start.md` (fuente: `clarix-backend/docs/prompts/session-start.md`)

**Interfaces:**
- Consumes: nombres exactos de skills de Tasks 1–2 y del runbook de Task 4 (`RUNBOOK-LOCAL-001-backend.md`).
- Produces: el comando ejecutable del paso 8 del diseño.

**Notas específicas (además de las reglas globales):**
1. Bloque de variables queda exactamente así:
```bash
REPO="adminprop-back"
ORG="martingalara-klg"
PROJECT_NUMBER=1   # ⚠ confirmar al crear el Project en el bootstrap (paso 7 del diseño)
```
   (resto del bloque de caching de IDs idéntico a clarix).
2. En "LECTURA OBLIGATORIA": el mapeo por tipo de tarea referencia solo docs que existen o existirán: quitar toda mención a `sdd_infra_003_backend_cloud_run.md`, `RUNBOOK-OPS-001` y `RUNBOOK-DEPLOY-001`. `MIGRATION` → `database-migration.md` + `tenant-isolation.md` + `RUNBOOK-LOCAL-001-backend.md` §"cómo correr la migración localmente". `WORKER` → `async-worker.md` + `external-integrations.md` (si aplica) + `tenant-isolation.md`.
3. Quitar el callout "el merge dispara deploy automático a staging…" (no hay CD). Reemplazar por: "> Sin CD todavía: el merge a develop solo corre CI. El deploy se incorpora cuando exista infra."
4. En Fase 1.2, la referencia al orden canónico pasa a: `spec_data_model.md §"Orden de Migración"` de adminprop (mismo nombre de sección — el data model del paso 3 del diseño debe incluirla) con las capas: Fundación → Propiedades → Personas → Contratos → Cobranzas → Mantenimiento → Liquidaciones → Notificaciones/Auditoría.
5. Checklist 4.1: quitar los ítems AFIP; el resto queda igual (RLS, 404 cross-tenant, error format, 95% cobertura, CA/RN).
6. Checklist 4.1.1 (operativo) se reduce a dos ítems: "si agrega env var nueva → actualizar `.env.example`" y "si cambia el setup local → actualizar `RUNBOOK-LOCAL-001-backend.md`".
7. Resumen de sesión (4.5): la lista "DOCUMENTOS OPERATIVOS A ACTUALIZAR" se reduce a `.env.example` y `RUNBOOK-LOCAL-001-backend.md`.
8. Sección "NUNCA HACER": quitar las líneas de AFIP/WSDL y Secret Manager; el resto se conserva literal.

- [ ] **Step 1: Releer la fuente** (ya leída en sesión, releer para fidelidad).
- [ ] **Step 2: Escribir la versión adaptada.**
- [ ] **Step 3: Verificar**

```bash
grep -riE "clarix|jose-kleiner|afip|cloud run|secret manager|RUNBOOK-DEPLOY|RUNBOOK-OPS|sdd_infra" docs/prompts/ && echo "FALLO" || echo OK
grep -c "martingalara-klg" docs/prompts/session-start.md
```
Expected: `OK`; el segundo comando ≥ 1.

- [ ] **Step 4: Verificar que todo doc referenciado existe o está en el árbol del diseño §5**

```bash
grep -oE "docs/(skills|runbooks|prompts)/[a-zA-Z0-9_-]+\.md" docs/prompts/session-start.md | sort -u | while read f; do [ -f "$f" ] || echo "FALTA: $f"; done
```
Expected: solo puede faltar `docs/runbooks/RUNBOOK-LOCAL-001-backend.md` (se crea en Task 4). Cualquier otro `FALTA` es error.

- [ ] **Step 5: Commit**

```bash
git add docs/prompts/session-start.md
git commit -m "docs: prompt de sesion autonoma del backend adaptado de clarix"
```

---

### Task 4: Runbook local + workflows CI — backend

**Files:**
- Create: `adminprop-back/docs/runbooks/RUNBOOK-LOCAL-001-backend.md` (fuente: `clarix-backend/docs/runbooks/RUNBOOK-LOCAL-001-backend.md`)
- Create: `adminprop-back/.github/workflows/ci.yml` (fuente: `clarix-backend/.github/workflows/ci.yml`)
- Create: `adminprop-back/.github/workflows/pr-format.yml` (fuente: ídem clarix)
- Create: `adminprop-back/.github/workflows/sdd-integrity.yml` (fuente: ídem clarix)
- Create: `adminprop-back/.github/workflows/sync-sdd-to-frontend.yml` (fuente: ídem clarix)

**Interfaces:**
- Consumes: nada nuevo.
- Produces: `RUNBOOK-LOCAL-001-backend.md` (referenciado por Task 3) con una sección titulada literalmente "**Cómo correr la migración localmente**" (o conservar la numeración §2.6 de clarix si la fuente la tiene — en ese caso mantener el anchor que usa session-start).

**Notas específicas:**
- Runbook local: quitar pasos de GCP/credenciales cloud y certificados AFIP; queda: prerequisitos (Docker, Python 3.11, uv/poetry según fuente), levantar Postgres+Redis con Docker Compose, correr migraciones Alembic, correr la API, correr tests. Si la fuente referencia `docker/docker-compose.yml` (que no copiamos), anotar: *"El `docker-compose.yml` se crea en el primer issue de infraestructura local del roadmap"* y mantener los comandos como especificación de lo que ese compose debe permitir.
- `ci.yml`: conservar jobs de lint+tests con services de Postgres/Redis si la fuente los tiene, pero agregar como **primer step** de cada job el guard:
```yaml
      - uses: actions/checkout@v4
      - name: Skip si el proyecto aún no está scaffoldeado
        id: guard
        run: |
          if [ ! -f pyproject.toml ]; then
            echo "skip=true" >> $GITHUB_OUTPUT
            echo "Proyecto sin scaffolding todavía — CI en no-op"
          fi
```
  y condicionar los steps siguientes con `if: steps.guard.outputs.skip != 'true'`.
- `sdd-integrity.yml`: copia casi literal (es agnóstico del dominio). Verificar que no tenga hardcodeado el nombre del repo (usa `${{ github.repository }}` — conservar).
- `sync-sdd-to-frontend.yml`: el repo destino pasa a `martingalara-klg/adminprop-front`. Conservar el mecanismo (push a `main` que toca `docs/sdd/**` → PR automático al front). Anotar en un comentario YAML al inicio: `# Requiere secret con permisos sobre adminprop-front — ver guía en clarix-backend/docs/ops/ci-sync-sdd-setup.md (bootstrap, paso 7)`.

- [ ] **Step 1: Leer los 5 archivos fuente.**
- [ ] **Step 2: Escribir las 5 versiones adaptadas.**
- [ ] **Step 3: Verificar**

```bash
grep -riE "clarix|jose-kleiner|afip|gcloud|artifact.registry|cloud.run" docs/runbooks/ .github/ | grep -v "ci-sync-sdd-setup" && echo "FALLO" || echo OK
grep -q "pyproject.toml" .github/workflows/ci.yml && echo "guard OK"
grep -q "adminprop-front" .github/workflows/sync-sdd-to-frontend.yml && echo "sync target OK"
```
Expected: `OK`, `guard OK`, `sync target OK`.

- [ ] **Step 4: Re-correr la verificación de links de Task 3 Step 4** — ahora sin ningún `FALTA`.
- [ ] **Step 5: Commit**

```bash
git add docs/runbooks/ .github/
git commit -m "ci: workflows de integridad SDD y CI + runbook local adaptados de clarix"
```

---

### Task 5: Skills + prompt + runbook + CI — frontend

**Files (fuentes en `clarix-frontend/`, destinos en `adminprop-front/`, mismos nombres):**
- Create: `docs/skills/api-client.md`, `code-review.md`, `error-handling.md`, `flow-implementation.md`, `git-workflow.md`, `github-project-workflow.md`, `module-structure.md`, `state-management.md`, `tenant-context.md`, `testing.md`
- Create: `docs/prompts/session-start.md`
- Create: `docs/runbooks/RUNBOOK-LOCAL-002-frontend.md`
- Create: `.github/workflows/ci.yml`

**Interfaces:**
- Consumes: convenciones idénticas a Tasks 1–4.
- Produces: el espejo frontend completo del sistema.

**Notas específicas:**
- **Antes de escribir:** `git checkout -b develop` en `adminprop-front` (nace de `main`).
- Aplican todas las reglas globales y de contenido, más:
  - **Sin Turborepo/dos apps** (decisión default del diseño §5): toda mención a `apps/main` / `apps/superadmin` / `packages/shared` / `pnpm-workspace` / `turbo.json` se reemplaza por una única app Vite (`src/`) con rutas `/superadmin/*` protegidas por `is_super_admin`. En `session-start.md` del front: **eliminar** la variable `APP=` y su lógica.
  - `session-start.md` front: mismas adaptaciones 1–8 de la Task 3 (variables con `REPO="adminprop-front"`, `PROJECT_NUMBER=2   # ⚠ confirmar en bootstrap`, sin deploy/CD, checklist operativo reducido a `.env.example` + `RUNBOOK-LOCAL-002-frontend.md`).
  - `api-client.md`: mantener generación de tipos desde OpenAPI del backend, Axios `withCredentials: true`, interceptor de refresh. Ejemplos con `contracts`/`payments`.
  - `tenant-context.md`: regla intacta — el front nunca envía `organization_id`; ejemplos con selector multi-org en login (logout+login para cambiar).
  - `module-structure.md` front: módulos de ejemplo `auth`, `properties`, `people`, `contracts`, `payments`, `settlements`, `maintenance`, `admin`, `notifications`, `account`.
  - `testing.md` front: Vitest + RTL + Playwright; naming por CA-XX igual que backend.
  - `ci.yml` front: guard equivalente con `package.json` en lugar de `pyproject.toml`.
  - Runbook local front: prerequisitos Node 20+, `npm/pnpm install`, `dev server`, generación de tipos OpenAPI apuntando al backend local; misma nota sobre archivos aún no scaffoldeados.

- [ ] **Step 1: Crear rama develop en adminprop-front** (`git checkout -b develop`).
- [ ] **Step 2: Leer los 13 archivos fuente.**
- [ ] **Step 3: Escribir las 13 versiones adaptadas.**
- [ ] **Step 4: Verificar**

```bash
cd adminprop-front
grep -riE "clarix|jose-kleiner|turborepo|apps/main|apps/superadmin|pnpm-workspace|afip" docs/ .github/ && echo "FALLO" || echo OK
grep -oE "docs/(skills|runbooks|prompts)/[a-zA-Z0-9_-]+\.md" docs/prompts/session-start.md | sort -u | while read f; do [ -f "$f" ] || echo "FALTA: $f"; done
```
Expected: `OK` y ningún `FALTA`.

- [ ] **Step 5: Commit**

```bash
git add docs/ .github/
git commit -m "docs: esqueleto SDD frontend (skills, prompt de sesion, runbook, ci) adaptado de clarix"
```

---

### Task 6: Barrido final de verificación cruzada

**Files:**
- Modify: ninguno salvo que el barrido encuentre problemas (correcciones puntuales en archivos de Tasks 1–5).

**Interfaces:**
- Consumes: todo lo producido en Tasks 1–5.
- Produces: esqueleto verificado, listo para el paso 2 del diseño (redacción del PRD con el usuario).

- [ ] **Step 1: Barrido de referencias residuales en ambos repos**

```bash
cd adminprop-back  && grep -riE "clarix|jose-kleiner" --include="*.md" --include="*.yml" docs/skills docs/prompts docs/runbooks .github | grep -v "ci-sync-sdd-setup" && echo "FALLO back" || echo "OK back"
cd ../adminprop-front && grep -riE "clarix|jose-kleiner" --include="*.md" --include="*.yml" docs .github && echo "FALLO front" || echo "OK front"
```
Expected: `OK back` y `OK front`. (La única mención permitida a clarix es el comentario del sync workflow que apunta a la guía `ci-sync-sdd-setup.md` del repo clarix.)

- [ ] **Step 2: Inventario contra el diseño §5** — listar árbol y comparar:

```bash
cd adminprop-back && find docs .github -type f | sort
cd ../adminprop-front && find docs .github -type f | sort
```
Expected back: 11 skills + 1 prompt + 1 runbook + 4 workflows (+ specs/plans de superpowers). Expected front: 10 skills + 1 prompt + 1 runbook + 1 workflow. Cualquier faltante → volver a la task correspondiente.

- [ ] **Step 3: Verificar consistencia interna** — que cada skill citado en ambos `session-start.md` exista con ese nombre exacto, y que `code-review.md` (back y front) conserve el heading "Checklist por tipo de artefacto".
- [ ] **Step 4: Commit de correcciones si las hubo**

```bash
git add -A && git commit -m "docs: correcciones del barrido de verificacion del esqueleto"
```
(Solo si hubo cambios; si no, no commitear nada.)

---

## Fuera de este plan (pasos siguientes del diseño)

- **Pasos 2–5:** redacción de los SDDs de producto — sesiones interactivas con el usuario, no delegables.
- **Paso 6:** `CLAUDE.md` de ambos repos + `_index.md`.
- **Paso 7:** bootstrap de GitHub (develop remoto, Projects — confirmar `PROJECT_NUMBER` en ambos session-start —, labels, milestones, issues).
- **Paso 8:** ejecución del comando.
