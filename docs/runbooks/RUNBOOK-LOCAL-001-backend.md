# RUNBOOK-LOCAL-001 — Ambiente local backend

> Objetivo: backend de AdminProp corriendo localmente en **<15 minutos** desde una máquina limpia.

Referencias:
- Modelo de datos: `docs/sdd/infrastructure/spec_data_model.md` (se crea en el paso 3 del diseño SDD — este link resolverá cuando exista)
- Arquitectura general: `docs/sdd/core/sdd_04_nonfunctional.md` (se crea en el paso 4 del diseño SDD — este link resolverá cuando exista)

> **Nota de infraestructura:** el `docker-compose.yml` referenciado en este runbook **se crea en el primer issue de infraestructura local del roadmap**. Mientras no exista, los comandos de este documento son la especificación de lo que ese compose debe permitir (servicios `api`, `postgres`, `redis`, healthchecks, `make up`/`make migrate`/`make test`). No hay infra cloud en el MVP: todo corre local vía Docker Compose + CI de tests.

---

## 1. Prerequisitos

Verificar que tenés instalado:

```bash
python --version          # >= 3.11
docker --version          # >= 24.0
docker compose version    # >= 2.0
git --version
gh --version               # GitHub CLI
```

**Si falta algo:**
- Python 3.11+: https://www.python.org/downloads/ o `pyenv install 3.11`.
- Docker Desktop (incluye `docker compose`): https://www.docker.com/products/docker-desktop/.
- gh: https://cli.github.com/.

---

## 2. Primera vez — setup completo (~15 min)

### 2.1 Clonar el repo

```bash
git clone https://github.com/martingalara-klg/adminprop-back.git
cd adminprop-back
git checkout develop
```

### 2.2 Copiar variables de entorno

```bash
cp .env.example .env
```

Editar `.env` con valores para desarrollo local. **Todos los campos con `change-me` o keys vacías deben completarse**:

| Variable | Cómo obtener |
|---|---|
| `SECRET_KEY` | `python -c "import secrets; print(secrets.token_urlsafe(32))"` |
| `ANTHROPIC_API_KEY` | https://console.anthropic.com/settings/keys (si la sesión usa asistencia IA en algún flujo) |
| `RESEND_API_KEY` | https://resend.com/api-keys (gratis hasta 100 emails/día) |
| `JWT_PRIVATE_KEY_PATH` / `JWT_PUBLIC_KEY_PATH` | Generar con paso 2.3 |
| `SENTRY_DSN` | Dejar vacío en local — el SDK detecta y desactiva |

Todos los secretos (API keys, `SECRET_KEY`, credenciales de servicios externos) se manejan en local como **variables de entorno locales (`.env`, no commiteado); migrar a un gestor de secretos cuando exista infra cloud**.

**El archivo `.env` está en `.gitignore` — nunca commitearlo.**

### 2.3 Generar claves JWT RSA (RS256)

```bash
mkdir -p keys
openssl genrsa -out keys/private.pem 2048
openssl rsa -in keys/private.pem -pubout -out keys/public.pem
chmod 600 keys/private.pem
```

El `.gitignore` ya incluye `keys/`. **No commitear las claves.**

### 2.4 Levantar la infraestructura local

```bash
make up
# o equivalente:
# docker compose -f docker/docker-compose.yml up --build -d
```

> Ver nota de infraestructura al inicio de este documento: el `docker-compose.yml` todavía no existe en el repo — se crea en el primer issue de infraestructura local. Estos comandos describen el comportamiento esperado una vez que exista.

Espera ~30 segundos a que postgres + redis estén healthy:

```bash
docker compose -f docker/docker-compose.yml ps
# Todos los services con STATUS = "Up (healthy)" o "Up"
```

Verificar logs si algo falla:

```bash
make logs                    # todos los servicios
make logs service=postgres   # solo postgres
```

### 2.5 Verificar extensiones de PostgreSQL

El `docker/postgres/init.sql` (a crearse junto con el `docker-compose.yml`) instala `pgcrypto` automáticamente al crearse el container. Verificar:

```bash
docker compose -f docker/docker-compose.yml exec postgres \
  psql -U adminprop -d adminprop -c "\dx"
```

Esperado:
```
   Name    | Version |   Schema   |         Description
-----------+---------+------------+------------------------------
 pgcrypto  | ...     | public     | cryptographic functions
 plpgsql   | 1.0     | pg_catalog | PL/pgSQL procedural language
```

### 2.6 Cómo correr la migración localmente

```bash
make migrate
# equivalente: docker compose -f docker/docker-compose.yml run --rm api alembic upgrade head
```

Verificar:

```bash
docker compose -f docker/docker-compose.yml exec postgres \
  psql -U adminprop -d adminprop -c "\dt"
# Debe listar las tablas del modelo (ver spec_data_model.md cuando exista)
```

Convenciones de migración (ver `docs/skills/database-migration.md`):
- Archivo en `src/adminprop/db/migrations/versions/YYYYMMDD_HHMMSS_<slug>.py` (timestamp UTC).
- **No usar `alembic revision --autogenerate`** para tablas con RLS o índices parciales — el SQL de políticas y `FORCE ROW LEVEL SECURITY` no se autogenera bien. Usar `alembic revision -m "<slug>"` y escribir el SQL a mano.
- Rollback de una migración: `docker compose -f docker/docker-compose.yml exec api alembic downgrade -1`.

### 2.7 Verificar que el backend responde

```bash
curl http://localhost:8000/health/liveness
# {"status":"ok"}

curl http://localhost:8000/health/readiness
# {"status":"ok","checks":{"database":{"status":"ok",...},"redis":{...}}}
```

Si `/health/readiness` aún no existe en el código, el primer issue de infraestructura local es agregarlo.

### 2.8 Correr la suite de tests

```bash
make test
# o: docker compose -f docker/docker-compose.yml run --rm api pytest
```

Debe pasar con la cobertura mínima definida en `docs/sdd/core/sdd_04_nonfunctional.md` (se crea en el paso 4 del diseño SDD).

---

## 3. Sesiones subsecuentes (~1 min)

```bash
# Pull cambios
git checkout develop
git pull origin develop

# Levantar infra si no está corriendo
make up

# Aplicar migraciones nuevas
make migrate

# Iniciar sesión de trabajo siguiendo docs/prompts/session-start.md
```

---

## 4. Comandos útiles

| Comando | Hace |
|---|---|
| `make up` | Levanta toda la infra local (api + workers ilustrativos + postgres + redis) |
| `make down` | Apaga todos los containers |
| `make logs` | Logs en vivo de todos los servicios |
| `make logs service=api` | Logs solo de la API |
| `make shell` | Bash dentro del container `api` |
| `make migrate` | `alembic upgrade head` |
| `make test` | Suite pytest completa |
| `docker compose -f docker/docker-compose.yml exec api alembic revision -m "<slug>"` | Crear migración nueva (**NO usar `--autogenerate`** — ver §2.6) |
| `docker compose -f docker/docker-compose.yml exec api alembic downgrade -1` | Rollback una migración |
| `docker compose -f docker/docker-compose.yml exec postgres psql -U adminprop -d adminprop` | Shell SQL |

**Workers ilustrativos** (Celery): `indices_worker` (obtiene índices ICL/IPC y aplica ajustes programados), `notification_worker` (email + in-app), `documents_worker` (PDFs de recibos y liquidaciones). Lista canónica de workers: se define en `sdd_04_nonfunctional.md` (paso 4 del diseño); estos son ilustrativos.

---

## 5. Verificación de aislamiento de tenant en local

Cada vez que toques RLS, correr este chequeo:

```bash
make shell
pytest tests/integration -k "tenant_isolation" -v
```

Todos los tests `test_tenant_isolation_*` deben pasar. Si alguno falla, **no hacer PR** — investigar antes.

Query manual de verificación:

```sql
-- En psql como adminprop_app (RLS aplica):
SET app.current_tenant_id = '<uuid>';
SELECT count(*) FROM contracts;
-- debe retornar solo contratos del tenant <uuid>

-- En psql como adminprop (superuser, BYPASSRLS):
SELECT organization_id, count(*) FROM contracts GROUP BY organization_id;
-- debe mostrar todas las orgs
```

---

## 6. Conectarse a la base de datos compartida (opcional)

En el MVP no hay infraestructura cloud (ver nota al inicio del documento): no existe un ambiente `dev`/`staging` remoto todavía. Cuando exista infra cloud, esta sección se completará con el procedimiento de conexión remota. Hasta entonces, todo el trabajo se hace contra la base de datos local levantada en §2.4.

**Reglas (vigentes desde ya, aplican también cuando exista infra):**
- **Nunca** conectarse a `production` desde la laptop salvo emergencia documentada.
- **Nunca** correr migraciones contra un ambiente remoto desde local — usar el mecanismo de migración del pipeline de deploy correspondiente (a definir cuando exista infra cloud).

---

## 7. Troubleshooting

| Error | Causa | Solución |
|---|---|---|
| `connection refused` en puerto 5432 | Postgres no inició | `make logs service=postgres` — revisar disk space, permisos del volumen. Reintentar `make down && make up` |
| `permission denied for table organizations` | Rol `adminprop_app` sin grants | Verificar que las migraciones corrieron como `adminprop` (superuser); chequear `ALTER DEFAULT PRIVILEGES` en `init.sql` |
| Migraciones fallan con `relation "..." already exists` | Estado de DB inconsistente | `make down && docker volume rm adminprop-back_postgres_data && make up && make migrate` (borra todos los datos) |
| `Address already in use` en puerto 8000/5432 | Otro proceso usando el puerto | `lsof -i :8000` y matar el proceso, o cambiar puerto en `docker-compose.yml` |
| `RESEND_API_KEY not set` al correr tests que envían email | Tests intentan llamar Resend real | Los tests deben usar mocks/fixtures deterministas; verificar `tests/conftest.py` |
| `JWT_PRIVATE_KEY_PATH` not found | Faltó paso 2.3 | Generar las claves: `openssl genrsa ...` |

---

## 8. Cuándo regenerar todo desde cero

Si la BD quedó en estado raro y querés empezar de cero:

```bash
make down
docker volume rm adminprop-back_postgres_data adminprop-back_redis_data
make up
make migrate
```

⚠️ Esto **borra todos los datos locales**. Hacelo solo si estás seguro.

---

## 9. Próximos pasos

1. Leer [`docs/prompts/session-start.md`](../prompts/session-start.md) para iniciar la primera sesión.
2. Leer los SDDs del módulo en el que vas a trabajar (`docs/sdd/`).
3. Leer los skills relevantes (`docs/skills/`).

---

## 10. Cuándo actualizar este runbook

| Si en una sesión... | Actualizar |
|---|---|
| Se agrega una variable de entorno nueva | Tabla §2.2 + actualizar `.env.example` |
| Cambia el procedimiento de generar JWT keys | §2.3 |
| Cambia el comportamiento de `make migrate` | §2.6 |
| Se crea el `docker-compose.yml` (primer issue de infraestructura local) | Quitar la nota de §"Nota de infraestructura" y verificar que todos los comandos siguen siendo exactos |
| Aparece un error común que no está en troubleshooting | §7 |
