# github-project-workflow

## Cuándo leer este skill

Leer **al inicio de cada sesión** que vaya a implementar una tarea del roadmap. También leer cuando:

- Hay que crear un PR.
- Hay que mover un issue entre columnas del Project.
- Hay que reportar una divergencia con el SDD.
- Hay que desbloquear issues dependientes tras un merge.

Este skill define el contrato exacto entre Claude Code y los dos GitHub Projects del owner.

## Stack relevante

| Item | Valor |
|---|---|
| Owner (org en GitHub) | `martingalara-klg` |
| Repo backend | `martingalara-klg/adminprop-back` |
| Repo frontend | `martingalara-klg/adminprop-front` |
| Project backend | Project del repo (owner `martingalara-klg`; el número se confirma en el bootstrap — paso 7 del diseño) |
| Project frontend | Project del repo (owner `martingalara-klg`; el número se confirma en el bootstrap — paso 7 del diseño) |
| CLI | `gh` v2.x (autenticado como `martingalara-klg`, scopes `repo, project, read:org`) |
| Convención de status del Project | Campo `Status` con valores `Todo`, `In progress`, `Done` |
| Labels de issue | `status:ready`, `status:blocked`, `sdd:divergence` |

> **Verificar al inicio de la sesión:** `gh auth status` debe reportar el usuario `martingalara-klg` activo. Si la sesión es de Claude Code en un entorno nuevo, ejecutar `gh repo view martingalara-klg/adminprop-back --json name,owner` para confirmar acceso. Si falla → detenerse y reportar.

## SDDs de referencia

- `docs/sdd/_index.md` §4 — los SDDs viven en `adminprop-back/docs/sdd/`; `adminprop-front/docs/sdd/` se sincroniza vía CI.
- Backend `CLAUDE.md` §2 "Fuente de verdad" y §8 "Cuando encontrar algo no especificado en el SDD".
- Frontend `CLAUDE.md` §2 "Regla de oro" — si la API real diverge del SDD, **reportar**, no adaptar.

## El patrón

El ciclo de vida de una tarea cruza 3 estados del Project:

```
Todo → In progress → Done
```

El label `status:blocked` puede aplicarse en cualquier momento (no es una
columna del Project) para señalar que una tarea quedó pausada por una
divergencia con el SDD — ver "Manejo de divergencias con el SDD" más abajo.

### Variables de sesión (definir al inicio)

```bash
# Identificación del repo activo en esta sesión:
REPO=adminprop-back          # o adminprop-front
ORG=martingalara-klg
PROJECT_NUMBER=<NUMERO>      # Project del repo (owner martingalara-klg); el
                             # número se confirma en el bootstrap — paso 7 del diseño

# Identificación de la tarea:
ISSUE_NUMBER=<número del issue de esta sesión>
```

Estas variables se usan en todos los comandos siguientes. Definirlas una vez ahorra errores.

### Paso 0 — Leer la tarea antes de implementar

```bash
# Ver descripción completa del issue (título, body, labels, assignees)
gh issue view "$ISSUE_NUMBER" --repo "$ORG/$REPO"

# Ver los custom fields del item dentro del Project (Status, SDD ref, prioridad, etc.)
gh project item-list "$PROJECT_NUMBER" \
  --owner "$ORG" \
  --format json \
  | jq --argjson n "$ISSUE_NUMBER" '.items[] | select(.content.number == $n)'
```

El body del issue indica qué SDD implementar y qué CA-XX/RN-XX cubrir. **Si hay ambigüedad entre el issue y el SDD, el SDD manda.** Reportar la discrepancia (ver "Manejo de divergencias" más abajo).

### Paso 1 — Mover el issue a "In progress" y crear el branch

```bash
# Obtener el ID interno del item dentro del Project (necesario para item-edit)
ITEM_ID=$(gh project item-list "$PROJECT_NUMBER" \
  --owner "$ORG" \
  --format json \
  | jq -r --argjson n "$ISSUE_NUMBER" \
    '.items[] | select(.content.number == $n) | .id')

# Status → "In progress"
gh project item-edit \
  --id "$ITEM_ID" \
  --project-id "$(gh project view "$PROJECT_NUMBER" --owner "$ORG" --format json | jq -r .id)" \
  --field-id "$(gh project field-list "$PROJECT_NUMBER" --owner "$ORG" --format json \
    | jq -r '.fields[] | select(.name == "Status") | .id')" \
  --single-select-option-id "$(gh project field-list "$PROJECT_NUMBER" --owner "$ORG" --format json \
    | jq -r '.fields[] | select(.name == "Status") | .options[] | select(.name == "In progress") | .id')"

# Actualizar labels del issue (el modelo de 3 estados no tiene label
# "in-progress"; el estado real vive en el campo Status del Project)
gh issue edit "$ISSUE_NUMBER" --repo "$ORG/$REPO" \
  --remove-label "status:ready"

# Definir un slug de 3-5 palabras kebab-case y crear el branch (ver git-workflow.md)
SLUG="<slug-descriptivo>"
git fetch origin
git checkout develop
git pull origin develop
git checkout -b "feature/${ISSUE_NUMBER}-${SLUG}"
```

> **Nota:** `gh project item-edit` requiere `--project-id`, `--field-id` y o bien `--text/--number/--date` o `--single-select-option-id`. Si querés guardar los IDs para no recalcularlos: `gh project field-list <PROJECT_NUMBER> --owner martingalara-klg --format json > /tmp/backend-fields.json` y consultarlos con `jq`.

### Paso 2 — Commits durante la implementación

Seguir `git-workflow.md`. Un commit por capa, con footer `Closes #N` + `Implements: CA-XX-...` + `Rule: RN-XX`.

### Paso 3 — Rebase y push antes del PR

```bash
git fetch origin
git rebase origin/develop
# Resolver conflictos si los hay, continuar con git rebase --continue
git push -u origin "feature/${ISSUE_NUMBER}-${SLUG}"
```

### Paso 4 — Abrir el PR con template completo

```bash
gh pr create \
  --repo "$ORG/$REPO" \
  --base develop \
  --title "[#${ISSUE_NUMBER}] <Título descriptivo de la tarea>" \
  --body "$(cat <<EOF
## Tarea
Closes #${ISSUE_NUMBER}

## SDD de referencia
- Documento: \`docs/sdd/<ruta-del-SDD>.md\`
- Secciones: <X.Y>, <X.Z>

## Criterios de aceptación implementados
- [x] CA-XX-01: <descripción exacta del SDD>
- [x] CA-XX-02: <descripción exacta del SDD>

## Reglas de negocio implementadas
- RN-XX: implementada en \`<ruta/archivo>:<línea aprox>\`
- RN-YY: implementada en \`<ruta/archivo>:<línea aprox>\`

## Decisiones de implementación
<Cualquier decisión tomada que no estaba explícita en el SDD. Si hay
divergencia con el SDD, abrir un issue con label \`sdd:divergence\` y
linkearlo acá.>

## Divergencias del SDD detectadas
<\"Ninguna\" o lista de divergencias con link al issue \`sdd:divergence\`>

## Checklist del autor
- [x] El código implementa exactamente lo especificado en el SDD.
- [x] Todos los flujos alternativos del SDD están cubiertos.
- [x] Los mensajes de error coinciden con el SDD.
- [x] Los tests tienen nombres con el ID CA-XX y pasan en CI.
- [x] No hay decisiones de diseño sin respaldo en el SDD.
- [x] Si hay migración, está dentro de transacción y RLS está habilitado.
EOF
)" \
  --assignee "@me"

# Capturar referencias para los pasos siguientes
PR_URL=$(gh pr view --repo "$ORG/$REPO" --json url --jq .url)
PR_NUMBER=$(gh pr view --repo "$ORG/$REPO" --json number --jq .number)
```

### Paso 5 — Vincular el PR al Project

```bash
# Agregar el PR al Project (lo trackea el campo Status también)
gh project item-add "$PROJECT_NUMBER" \
  --owner "$ORG" \
  --url "$PR_URL"

# Comentario en el issue
gh issue comment "$ISSUE_NUMBER" --repo "$ORG/$REPO" \
  --body "PR abierto para revisión: $PR_URL"
```

El issue permanece en "In progress" durante la revisión: el modelo de 3
estados (`Todo` / `In progress` / `Done`) no tiene una columna separada para
"en revisión"; el PR abierto y vinculado al Project es la señal de que la
tarea está en revisión.

### Paso 6 — Post-merge (sólo cuando el PR sea aprobado y mergeado)

**No ejecutar este paso anticipadamente.** Esperar a que el merge ocurra (manual del owner, o vía auto-merge si está configurado).

```bash
# Cerrar el issue y registrar el merge
gh issue close "$ISSUE_NUMBER" --repo "$ORG/$REPO" \
  --comment "Implementación completada. PR mergeado: $PR_URL"

# Mover el item del Project a "Done"
gh project item-edit \
  --id "$ITEM_ID" \
  --project-id "$(gh project view "$PROJECT_NUMBER" --owner "$ORG" --format json | jq -r .id)" \
  --field-id "$(gh project field-list "$PROJECT_NUMBER" --owner "$ORG" --format json \
    | jq -r '.fields[] | select(.name == "Status") | .id')" \
  --single-select-option-id "$(gh project field-list "$PROJECT_NUMBER" --owner "$ORG" --format json \
    | jq -r '.fields[] | select(.name == "Status") | .options[] | select(.name == "Done") | .id')"

# Limpiar el branch local y remoto
git checkout develop
git pull origin develop
git branch -d "feature/${ISSUE_NUMBER}-${SLUG}"
git push origin --delete "feature/${ISSUE_NUMBER}-${SLUG}"
```

### Paso 7 — Desbloquear issues dependientes

Si el issue cerrado tenía issues que dependían de él (bloqueados con `status:blocked`), revisarlos y desbloquear:

```bash
# Listar issues bloqueados que referencian #ISSUE_NUMBER en su body
gh issue list --repo "$ORG/$REPO" \
  --label "status:blocked" \
  --json number,title,body \
  | jq --arg ref "#$ISSUE_NUMBER" '.[] | select(.body | contains($ref)) | {number, title}'

# Para cada uno que ahora esté desbloqueado (revisar caso por caso):
gh issue edit <NUMERO_DESBLOQUEADO> --repo "$ORG/$REPO" \
  --add-label "status:ready" \
  --remove-label "status:blocked"
```

Si el item quedó en una columna distinta de `Todo` mientras estaba bloqueado, moverlo de vuelta a `Todo` en el Project (mismo patrón de `item-edit` que arriba).

### Manejo de divergencias con el SDD

Si durante la implementación encontrás que el SDD es ambiguo, contradice otra fuente o no cubre un caso real:

```bash
gh issue create --repo "$ORG/$REPO" \
  --title "[SDD-DIVERGENCE] <doc>.md §<sección> — <descripción corta>" \
  --body "$(cat <<EOF
## Contexto
Detectado durante la implementación de #${ISSUE_NUMBER}.

## Qué dice el SDD
<cita textual del SDD, con ruta + sección>

## Qué encontramos en la implementación
<descripción concreta del conflicto o de la limitación técnica>

## Opciones de resolución
1. <opción A — implicaciones>
2. <opción B — implicaciones>

## Impacto si no se resuelve
<consecuencia concreta en el desarrollo: bloquea otro issue, cambia
contrato API ya consumido por frontend, etc.>
EOF
)" \
  --label "sdd:divergence" \
  --label "status:blocked"
```

Mover el issue **original** a `status:blocked` también (no a `Done`) hasta que la divergencia se resuelva, y agregar un comentario linkeando al nuevo issue de divergencia.

## Template

Template completo para el inicio de una sesión de implementación:

```bash
# 1. Variables de sesión
REPO=adminprop-back
ORG=martingalara-klg
PROJECT_NUMBER=<NUMERO>   # Project del repo (owner martingalara-klg); el
                          # número se confirma en el bootstrap — paso 7 del diseño
ISSUE_NUMBER=<NUMERO>
SLUG=<slug-descriptivo-kebab-case>

# 2. Verificar prerequisitos
gh auth status || { echo "gh no autenticado"; exit 1; }
gh repo view "$ORG/$REPO" --json name >/dev/null || { echo "Sin acceso al repo"; exit 1; }

# 3. Leer la tarea
gh issue view "$ISSUE_NUMBER" --repo "$ORG/$REPO"

# 4. Mover a In progress + crear branch (ver Paso 1 arriba)

# 5. Implementar siguiendo el SDD + git-workflow.md

# 6. Antes del PR: rebase + push (ver Paso 3)

# 7. Crear PR con template (ver Paso 4) y vincular al Project (ver Paso 5)
```

## Checklist pre-commit

- [ ] El issue está en "In progress" en el Project antes del primer commit.
- [ ] El branch sigue la convención `feature/<issue-number>-<slug>` (ver `git-workflow.md`).
- [ ] El issue está linkeado en el body del PR con `Closes #N`.
- [ ] El PR cita el SDD por documento + sección.
- [ ] El PR enumera los CA-XX y RN-XX implementados.
- [ ] El PR declara explícitamente si hay divergencias detectadas (o "Ninguna").
- [ ] El PR está vinculado al Project (Paso 5).
- [ ] No se cerró el issue antes de que el PR sea mergeado.

## Antipatrones

```bash
# ❌ Mover el issue a "Done" antes del merge
gh issue close "$ISSUE_NUMBER" ...   # ANTES del merge del PR
# Causa: si el PR se rechaza o necesita cambios, el dashboard miente.

# ✅ Sólo cerrar tras merge confirmado (Paso 6).
```

```bash
# ❌ Cambiar el comportamiento esperado para que el código pase los tests
# Bajar la cantidad de caracteres mínimos de un reason de NC porque el
# test es más fácil sin esa validación.
# Causa: divergencia silenciosa con el SDD. El próximo developer no
# tendrá manera de saber por qué el código discrepa.

# ✅ Abrir issue [SDD-DIVERGENCE], pausar la implementación con
# status:blocked, y esperar instrucción.
```

```bash
# ❌ Adaptar el frontend para compensar una API que diverge del SDD
# Si el endpoint retorna 201 pero el SDD dice 202, NO hacer:
#   if (status === 201 || status === 202) { ... }
# Causa: la convención de async (202 Accepted) se rompe; el cliente
# trata respuestas que el SDD asume distintas como equivalentes.

# ✅ Reportar la divergencia (issue sdd:divergence), pausar el frontend,
# esperar fix del backend.
```

```bash
# ❌ Skipear el Paso 5 (vincular PR al Project)
gh pr create ...
# (sin hacer item-add ni mover Status)
# Causa: el dashboard del Project queda desactualizado y el owner
# pierde visibilidad del flujo real.

# ✅ Siempre ejecutar Paso 5 inmediatamente después del Paso 4.
```

```bash
# ❌ Commits que sólo dicen "address review"
git commit -m "fix review"
# Causa: el reviewer no puede saber qué cambió sin leer el diff completo.

# ✅ Commit que describe el cambio
git commit -m "fix(onboarding): validate slug regex on server before insert"
```

## Referencias

- `core/sdd_03_api_contracts.md` §"Regla de oro" — ningún contrato API se modifica sin actualizar el SDD primero. Esto refuerza el ciclo de divergencias.
- Backend `CLAUDE.md` §2 "Regla de conflicto" y §8 "Cuando encontrar algo no especificado en el SDD" — el flujo de pause + report es la regla operacional, este skill lo materializa con comandos `gh`.
- Frontend `CLAUDE.md` §8 "Cuando la API no coincide con el SDD" — mismo flujo desde el frontend.
- `docs/sdd/_index.md` §4 — los SDDs son source-of-truth en `adminprop-back`; el sync a `adminprop-front` es automático vía GH Actions. Las divergencias se abren contra `adminprop-back`.
