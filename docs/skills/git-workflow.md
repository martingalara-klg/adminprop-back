# git-workflow

## Cuándo leer este skill

Leer **antes de**:

- Crear un branch nuevo (feature, bugfix, hotfix, chore, release).
- Hacer un commit.
- Abrir un Pull Request.
- Mergear o eliminar branches.
- Cualquier sesión de implementación de una tarea del GitHub Project.

Si la operación involucra el ciclo `develop → feature → PR → merge → develop`, este es el contrato.

## Stack relevante

| Capa | Tecnología | Fuente |
|---|---|---|
| Hosting | GitHub (`github.com/martingalara-klg/adminprop-back` y `adminprop-front`) | `gh repo view` |
| CLI principal | `gh` (autenticado como `martingalara-klg`) | `gh auth status` |
| Convención de commits | Conventional Commits (`feat`, `fix`, `test`, `migrate`, `refactor`, `chore`, `docs`, `perf`) | Este skill |
| Estilo de branching | GitFlow adaptado (`main`, `develop`, `feature/*`, `bugfix/*`, `hotfix/*`, `chore/*`, `release/*`) | Este skill |

## SDDs de referencia

- `core/sdd_03_api_contracts.md` §"Regla de oro" — los contratos no se modifican sin actualizar el SDD primero.
- `docs/sdd/_index.md` §6 "Estado de los SDDs" — versionado `1.x` para adiciones backwards-compatible, `2.x` para breaking changes.
- Backend `CLAUDE.md` §2 y Frontend `CLAUDE.md` §2 — el SDD manda; ante divergencia, detenerse y reportar.

## El patrón

### Estructura de branches

```
main                      ← producción. Protegida. Solo merge vía PR aprobado.
develop                   ← integración. Base de todos los feature branches.
release/vX.Y.Z            ← preparación de release. Solo bugfixes.
hotfix/<slug>             ← fix urgente sobre main.

feature/<issue-number>-<slug>   ← implementación de una tarea del roadmap
bugfix/<issue-number>-<slug>    ← corrección de un issue
chore/<slug>                    ← configuración, dependencias, CI (no requiere issue)
```

### Nomenclatura de branches derivada del issue

El nombre de un `feature/`, `bugfix/` o `hotfix/` se deriva del **número del issue de GitHub** y un slug descriptivo en kebab-case (3–5 palabras). Esto sirve para los dos repos (backend y frontend) sin prefijo adicional — el repo ya identifica el contexto.

Ejemplos:

```
feature/12-crear-tabla-properties
feature/27-worker-ajuste-icl
bugfix/45-calculo-punitorios-redondeo
hotfix/72-icl-index-fetch-timeout-blocks-liquidacion
chore/setup-ci-pipeline
```

Reglas:

- Todo en minúsculas.
- Palabras separadas por guión, sin underscore.
- El slug describe la **acción** o el **artefacto principal**, no el módulo (el módulo se infiere del issue).
- Si la tarea no tiene issue (ej: setup inicial de CI), usar `chore/<slug>` sin número.

### Convención de commits (Conventional Commits)

Plantilla:

```
<tipo>(<módulo>): <descripción imperativa en minúsculas, ≤ 72 chars>

<cuerpo opcional: qué cambia y por qué, no cómo>
<wrap a 72 chars>

<footer>
Closes #<issue-number>
Implements: CA-XX-01, CA-XX-02
Rule: RN-XX, RN-YY
```

Tipos válidos:

| Tipo | Cuándo usarlo |
|---|---|
| `feat` | Nueva funcionalidad especificada en el SDD |
| `fix` | Corrección de bug |
| `test` | Agregar o corregir tests (especialmente CA-XX o flujos alternativos) |
| `migrate` | Nueva migración Alembic (backend) — separado de `feat` para que el reviewer foque en el schema |
| `refactor` | Cambio sin alterar comportamiento |
| `chore` | Configuración, dependencias, CI, scripts |
| `docs` | Actualización de SDD o documentación (incluye este directorio) |
| `perf` | Mejora de performance sin cambio de comportamiento |

El `<módulo>` es el slug del módulo afectado (`propiedades`, `personas`, `contratos`, `cobranzas`, `liquidaciones`, `mantenimiento`, `notificaciones`, `administracion`, `superadmin`, etc.). Para cambios transversales: `shared`, `infra`, `ci`.

#### Ejemplos reales del proyecto

```
feat(onboarding): implement organization registration endpoint

Implements POST /superadmin/organizations as specified in
spec_module_00_superadmin.md §RF-02.

Closes #12
Implements: CA-RF02-01, CA-RF02-02
Rule: RN-01, RN-03, RN-06
```

```
migrate(liquidaciones): add settlement_lines.pdf_path column

Adds la ruta al PDF de liquidación en filesystem local (volumen
Docker en MVP) per spec_module_05_liquidaciones §4.

Closes #8
Rule: RN-LIQ-04 (aislamiento multi-tenant)
```

```
feat(cobranzas): support per_property payment scope

Implements RF-03 de spec_module_04_cobranzas. Agrega columna
payment_scope a payments, valida contra contract-coverage al
momento de la generación, y actualiza POST /payments/generate.

Closes #34
Implements: CA-04-09, CA-04-10, CA-04-11
Rule: RN-F-11, RN-F-12
```

```
fix(liquidaciones): reject period reopen with notes < 10 chars

Frontend was accepting empty notes and backend was rejecting with
500. Added Zod validation aligned with spec_module_05_liquidaciones
§validaciones.

Closes #58
```

#### Regla de un commit por capa implementada

Para una tarea típica de backend, dividir en al menos estos commits:

1. `migrate(<módulo>): ...` — cambios de schema (Alembic).
2. `feat(<módulo>): ...` — modelos, repository, service, router.
3. `test(<módulo>): ...` — tests CA-XX y de aislamiento de tenant.

Para frontend:

1. `feat(<módulo>): ...` — hooks + API client + tipos.
2. `feat(<módulo>): ...` — pages + components.
3. `test(<módulo>): ...` — tests Vitest/Playwright.

El reviewer puede aprobar capa por capa.

### Ciclo de vida de un branch

```
1. Crear desde develop (NUNCA desde main):
   git fetch origin
   git checkout develop
   git pull origin develop
   git checkout -b feature/<issue-number>-<slug>

2. Commits atómicos durante el desarrollo (uno por capa terminada y
   verificada — ver "regla de un commit por capa" arriba).

3. Antes del PR, rebase sobre develop (NO merge de develop dentro
   del feature branch):
   git fetch origin
   git rebase origin/develop
   # Resolver conflictos si los hay
   git push --force-with-lease origin feature/<issue-number>-<slug>

4. Abrir PR a develop (ver github-project-workflow.md):
   gh pr create --base develop ...

5. Tras merge, eliminar el branch local y remoto:
   git checkout develop
   git pull origin develop
   git branch -d feature/<issue-number>-<slug>
   git push origin --delete feature/<issue-number>-<slug>
```

### Reglas de protección de branches

Configuración esperada en GitHub (responsabilidad operativa del owner):

- **`main`**: require PR + review aprobado, status checks deben pasar, no force push, no delete, signed commits opcionales.
- **`develop`**: require PR, CI (lint + tests) debe pasar, no force push, no delete.

## Template

Template de commit message (HEREDOC para multi-line, lo usa el skill `github-project-workflow`):

```bash
git commit -m "$(cat <<'EOF'
<tipo>(<módulo>): <descripción imperativa>

<cuerpo opcional explicando QUÉ cambia y POR QUÉ (no CÓMO).
Referenciar el SDD por documento + sección.>

Closes #<issue-number>
Implements: CA-XX-01, CA-XX-02
Rule: RN-XX
EOF
)"
```

Template de creación de feature branch:

```bash
# Asumiendo $ISSUE_NUMBER y $SLUG ya definidos en la sesión
git fetch origin
git checkout develop
git pull origin develop
git checkout -b "feature/${ISSUE_NUMBER}-${SLUG}"
```

## Checklist pre-commit

- [ ] El branch nació de `develop`, no de `main`.
- [ ] El nombre del branch incluye el número del issue.
- [ ] El mensaje del commit usa Conventional Commits (`tipo(módulo): ...`).
- [ ] El cuerpo del commit referencia el SDD por documento + sección si introduce reglas de negocio.
- [ ] El footer incluye `Closes #N` cuando existe issue.
- [ ] El footer incluye `Implements: CA-XX-...` y `Rule: RN-XX` cuando aplica.
- [ ] Cada commit es atómico (una capa: migración, lógica, tests). No mezclar capas en un commit.
- [ ] Si hay migraciones, son su propio commit con tipo `migrate`.
- [ ] Antes del `git push` final del PR, se hizo `git rebase origin/develop` (no `git merge develop`).

## Antipatrones

```bash
# ❌ Branch creado desde main
git checkout main
git checkout -b feature/12-new-endpoint
# Causa: cuando develop tiene cambios no liberados, el feature parte
# de un baseline obsoleto y los conflictos aparecen al PR.

# ✅ Branch desde develop
git checkout develop
git pull origin develop
git checkout -b feature/12-new-endpoint
```

```bash
# ❌ Commit gigante mezclando capas
git add migrations/ src/adminprop/modules/onboarding/ tests/
git commit -m "wip onboarding"

# ✅ Tres commits, uno por capa
git add src/adminprop/db/migrations/versions/20260617_*_create_organizations.py
git commit -m "migrate(onboarding): create organizations table with RLS"

git add src/adminprop/modules/onboarding/
git commit -m "feat(onboarding): add POST /superadmin/organizations endpoint"

git add tests/integration/test_onboarding.py
git commit -m "test(onboarding): cover CA-RF02-01 to CA-RF02-04"
```

```bash
# ❌ Merge de develop dentro del feature branch
git checkout feature/12-new-endpoint
git merge develop
# Causa: introduce un merge commit ruidoso en la historia del feature
# y hace el PR difícil de leer.

# ✅ Rebase
git fetch origin
git rebase origin/develop
git push --force-with-lease origin feature/12-new-endpoint
```

```bash
# ❌ Force push sin --force-with-lease
git push --force origin feature/12-new-endpoint
# Causa: si alguien más comiteó en el branch (improbable en feature
# branches solo, pero posible), sus commits se pierden silenciosamente.

# ✅ Force-with-lease aborta el push si el remoto tiene commits nuevos
git push --force-with-lease origin feature/12-new-endpoint
```

```bash
# ❌ Subjects que no dicen nada
git commit -m "fix bug"
git commit -m "wip"
git commit -m "asdf"

# ✅ Subjects que describen el cambio
git commit -m "fix(liquidaciones): reject period reopen with notes < 10 chars"
git commit -m "feat(indices): wire BCRA ICL fetch into indices_worker"
```

```bash
# ❌ Skip hooks (--no-verify) para evitar lint o tests
git commit --no-verify -m "feat(...): ..."
# Si el hook falla, investigá la causa. Saltarlo introduce código que
# romperá CI.

# ✅ Resolver el problema reportado por el hook
# (ej: correr el formatter, arreglar el lint, corregir el test que falló)
```

## Referencias

- `core/sdd_03_api_contracts.md` §"Regla de oro" — ningún contrato API cambia sin actualizar el SDD primero. Este principio se traslada a la convención de commits: el footer `Rule:` y `Implements:` ata el código al SDD.
- `docs/sdd/_index.md` §6 — versionado de SDDs (`1.x` aditivo, `2.x` breaking) que el commit `docs(...)` debe respetar.
- Backend `CLAUDE.md` §8 "Comportamiento esperado de Claude Code" — "referenciar la regla de negocio (RN-XX) en el código cuando se implemente una invariante crítica" se materializa en el footer del commit.
- Frontend `CLAUDE.md` §8 — "nombrar los tests con el ID del criterio de aceptación (CA-XX o UC-XX) del SDD" es coherente con `test(...)` + `Implements:` en el footer.
