# database-migration

## Cuándo leer este skill

Leer **antes de**:

- Crear, modificar o eliminar una tabla.
- Agregar un índice, una constraint o una extensión PostgreSQL.
- Seedear datos (sea global o per-tenant).
- Cualquier cambio de schema en producción.

## Stack relevante

| Capa | Tecnología | Fuente |
|---|---|---|
| Migraciones | **Alembic** | backend `CLAUDE.md` §3 |
| Convención de naming | `YYYYMMDD_HHMMSS_<slug>.py` (timestamp ISO) | backend `CLAUDE.md` §3 / §5 |
| DB | PostgreSQL 16 + extensión `pgcrypto` | backend `CLAUDE.md` §3 |
| Pool de conexiones | PgBouncer (transaction-scoped) | backend `CLAUDE.md` §3 |
| Encriptación columnar | AES-256 vía `pgcrypto`; clave (KEK) en variable de entorno local (`.env`, no commiteado) en MVP — migrar a un gestor de secretos cuando exista infra cloud | backend `CLAUDE.md` §3 |
| RLS | Política `USING (organization_id = current_setting('app.current_tenant_id')::uuid)` | backend `CLAUDE.md` §4 |
| Roles DB | `adminprop_app` (default, sujeto a RLS), `adminprop_superadmin` (BYPASSRLS) | backend `CLAUDE.md` §3, `_index.md` §4 #42 |
| Ubicación | `src/adminprop/db/migrations/versions/` | backend `CLAUDE.md` §9 |

## SDDs de referencia

- `infrastructure/spec_data_model.md` — fuente de verdad de las tablas: nombres, tipos, restricciones, índices.
- `core/sdd_02_domain_model.md` §3 RN-D — invariantes de datos (soft delete, append-only, RLS).
- `core/sdd_04_nonfunctional.md` §2.3 — RLS + roles DB para multi-tenancy.
- `infrastructure/spec_data_model.md` §"Estrategia de Seed Data" — seed global vs per-tenant.

## El patrón

### Comando para crear una migración

Alembic se invoca vía `alembic` (instalado por el proyecto) o `python -m alembic`. El proyecto define `alembic.ini` y `src/adminprop/db/migrations/env.py`.

```bash
# Crear una migración vacía (recomendado: escribir el SQL a mano,
# autogenerate de Alembic suele perderse con RLS, índices parciales,
# CHECK constraints complejas).
alembic revision -m "create_organizations" --version-path src/adminprop/db/migrations/versions
```

El archivo generado se renombra para seguir la convención del proyecto: `YYYYMMDD_HHMMSS_<slug>.py`.

### Convención de nombres de archivos

```
src/adminprop/db/migrations/versions/
├── 20260617_120000_seed_plan_definitions.py
├── 20260617_120100_seed_ar_holidays.py
├── 20260617_120200_seed_notification_event_types.py
├── 20260617_130000_create_organizations.py
├── 20260617_130100_create_users.py
├── 20260617_130200_create_organization_invitations.py
├── ...
```

Reglas:

- `YYYYMMDD_HHMMSS` UTC al momento de crear el archivo (no tocar después).
- `<slug>` describe la operación: `create_<tabla>`, `add_<col>_to_<tabla>`, `seed_<dataset>`, `add_index_<idx_name>`.
- snake_case, sin guiones medios.
- Las dependencias entre migraciones se expresan vía `down_revision` (lo maneja Alembic automáticamente).

### Estructura del archivo de migración

```python
"""create_organizations table with RLS

SDD: infrastructure/spec_data_model.md §Capa 0 — Fundación
Implements: RN-D01 (tenant isolation), Decisión #42 (rol adminprop_superadmin BYPASSRLS)
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers (Alembic los autogenera)
revision: str = "20260617_130000"
down_revision: str | None = "20260617_120200_seed_notification_event_types"
branch_labels: tuple[str, ...] | None = None
depends_on: tuple[str, ...] | None = None


def upgrade() -> None:
    # Asegurar extensiones (idempotente — no falla si ya existen)
    op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")

    # Crear la tabla
    op.execute("""
        CREATE TABLE organizations (
            id                          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            slug                        TEXT NOT NULL UNIQUE,
            name                        TEXT NOT NULL,
            tipo_organizacion           TEXT NOT NULL,
            plan                        TEXT NOT NULL CHECK (plan IN ('starter','growth','enterprise')),
            timezone                    TEXT NOT NULL,
            locale                      TEXT NOT NULL DEFAULT 'es',
            settings                    JSONB NOT NULL DEFAULT '{}'::JSONB,
            is_active                   BOOLEAN NOT NULL DEFAULT TRUE,
            created_at                  TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at                  TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)

    # Índices (los del SDD §Capa 0)
    op.execute("CREATE INDEX idx_organizations_slug ON organizations (slug)")
    op.execute("CREATE INDEX idx_organizations_is_active ON organizations (is_active) WHERE is_active = TRUE")

    # NO se habilita RLS en `organizations`: es la tabla raíz; las queries
    # se restringen vía permisos a nivel de aplicación + el rol
    # adminprop_superadmin tiene BYPASSRLS para `/superadmin/*`.


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS organizations CASCADE")
```

### Plantilla para una tabla tenant-scoped (con RLS)

Toda tabla con `organization_id` debe habilitar RLS y declarar la política. Cualquier excepción se documenta en el SDD.

```python
"""create_contracts table with RLS

SDD: features/spec_module_03_contratos.md + infrastructure/spec_data_model.md §Capa 2
Implements: RN-D01, RN-D02 (soft delete)
"""
from alembic import op


revision: str = "20260617_150000"
down_revision: str | None = "20260617_140900_create_properties"


def upgrade() -> None:
    op.execute("""
        CREATE TABLE contracts (
            id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            organization_id UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
            property_id     UUID NOT NULL REFERENCES properties(id),
            tenant_person_id UUID NOT NULL REFERENCES people(id),
            start_date      DATE NOT NULL,
            end_date        DATE NOT NULL,
            monthly_amount  NUMERIC(14,2) NOT NULL,
            currency        TEXT NOT NULL DEFAULT 'ARS' CHECK (currency IN ('ARS','USD')),
            status          TEXT NOT NULL DEFAULT 'draft'
                CHECK (status IN ('draft','active','terminated','expired')),
            metadata        JSONB NOT NULL DEFAULT '{}'::JSONB,
            deleted_at      TIMESTAMPTZ,
            created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)

    # Índices del SDD
    op.execute("""
        CREATE INDEX idx_contracts_org_property_status
        ON contracts (organization_id, property_id, status)
        WHERE deleted_at IS NULL
    """)

    # ─── Row Level Security ─────────────────────────────────────────
    # OBLIGATORIO en toda tabla con organization_id.
    op.execute("ALTER TABLE contracts ENABLE ROW LEVEL SECURITY")

    # Política: las filas visibles son sólo las del tenant activo.
    # current_setting('app.current_tenant_id') lo setea el middleware
    # FastAPI al inicio de cada request (ver tenant-isolation.md).
    op.execute("""
        CREATE POLICY contracts_tenant_isolation ON contracts
        USING (organization_id = current_setting('app.current_tenant_id', true)::uuid)
        WITH CHECK (organization_id = current_setting('app.current_tenant_id', true)::uuid)
    """)

    # FORCE RLS: aplica también para el owner de la tabla (adminprop_app).
    # adminprop_superadmin tiene atributo BYPASSRLS, así que no se ve afectado.
    op.execute("ALTER TABLE contracts FORCE ROW LEVEL SECURITY")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS contracts CASCADE")
```

> **Nota sobre `current_setting('app.current_tenant_id', true)`:** el segundo argumento `true` significa "missing_ok" — si el setting no está definido (worker o test sin contexto), retorna NULL y la política falla cerrando el acceso. Esto previene leaks si el middleware no se ejecutó.

### Plantilla para columna con encriptación columnar (pgcrypto)

```python
"""add_bank_account_encrypted_to_organization_payout_data

SDD: features/spec_module_07_administracion.md §RF-08 (configuración de cobros/pagos)
+ sdd_04 §2.4 (campos sensibles cifrados AES-256 columnar via pgcrypto)
+ nota: la KEK vive en variable de entorno local (.env, no commiteado) en MVP;
  migrar a un gestor de secretos cuando exista infra cloud.
"""
from alembic import op


revision: str = "20260618_100000"
down_revision: str | None = "..."


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")

    op.execute("""
        ALTER TABLE organization_payout_data
        ADD COLUMN bank_account_encrypted   BYTEA,
        ADD COLUMN bank_account_last4       TEXT
    """)

    # La encriptación/desencriptación se hace en la aplicación con la DEK
    # cargada desde la variable de entorno local. pgcrypto provee el
    # algoritmo AES-256, no la gestión de claves.


def downgrade() -> None:
    op.execute("""
        ALTER TABLE organization_payout_data
        DROP COLUMN IF EXISTS bank_account_encrypted,
        DROP COLUMN IF EXISTS bank_account_last4
    """)
```

### Plantilla de seed global (idempotente)

`spec_data_model.md` §"Estrategia de Seed Data" exige migraciones de seed idempotentes (re-ejecutables sin duplicar filas).

```python
"""seed_plan_definitions

SDD: infrastructure/spec_data_model.md §Capa 0 — plan_definitions
Idempotente: usa INSERT ... ON CONFLICT DO NOTHING.
"""
from alembic import op


revision: str = "20260617_120000"
down_revision: str | None = None   # primera migración del proyecto


def upgrade() -> None:
    op.execute("""
        INSERT INTO plan_definitions (plan_code, display_name, limits, is_available_self_serve)
        VALUES
            ('starter', 'Starter',
             '{"seats":5,"properties_total":25,"contracts_active":25,"work_orders_per_month":30,
                "storage_gb":5,"audit_log_retention_months":12,"settlements_per_month":25}'::JSONB,
             TRUE),
            ('growth', 'Growth',
             '{"seats":25,"properties_total":150,"contracts_active":150,"work_orders_per_month":200,
                "storage_gb":25,"audit_log_retention_months":24,"settlements_per_month":150}'::JSONB,
             TRUE),
            ('enterprise', 'Enterprise',
             '{"seats":null,"properties_total":null,"contracts_active":null,"work_orders_per_month":null,
                "storage_gb":null,"audit_log_retention_months":null,"settlements_per_month":null}'::JSONB,
             FALSE)
        ON CONFLICT (plan_code) DO NOTHING
    """)


def downgrade() -> None:
    # Sólo borra los planes que esta migración insertó.
    op.execute("DELETE FROM plan_definitions WHERE plan_code IN ('starter','growth','enterprise')")
```

> Para seed per-tenant (roles, tipos de propiedad, tipos de actividad de mantenimiento al alta de una org), **NO usar migración Alembic**: vive en `OrganizationProvisioningService` (Python), en la misma transacción que el INSERT a `organizations`. Ver `spec_data_model.md §"Estrategia de Seed Data"`.

### Reglas para schemas mutables

| Operación | Recomendación |
|---|---|
| `ADD COLUMN ... NOT NULL` | Hacerlo en dos pasos para tablas grandes: (1) `ADD COLUMN ... NULL DEFAULT '<val>'` + backfill; (2) `ALTER COLUMN ... SET NOT NULL`. Evitar lock largos. |
| `RENAME COLUMN` | Evitar. Si es necesario: agregar nueva, copiar datos, despachar release, eliminar vieja en migración posterior. |
| `ALTER COLUMN ... TYPE` | Riesgoso. Validar con `pg_dump --schema-only` antes y después. |
| `CREATE INDEX` sobre tabla grande | Usar `CREATE INDEX CONCURRENTLY` (fuera de transacción). Alembic soporta esto vía `op.execute("CREATE INDEX CONCURRENTLY ...")` + `transactional_ddl = False` en el archivo. |
| `DROP TABLE` o `DROP COLUMN` | Esperar al menos un release. El nivel "estructural inmutable" (audit_logs) no se modifica. |

## Template

Skeleton de archivo de migración:

```python
"""<verbo>_<artefacto> — <descripción breve>

SDD: <ruta-del-SDD>.md §<sección>
Implements: <RN-XX o decisión #N>
"""
from alembic import op


revision: str = "<YYYYMMDD_HHMMSS>"
down_revision: str | None = "<previous-revision-id>"
branch_labels: tuple[str, ...] | None = None
depends_on: tuple[str, ...] | None = None


def upgrade() -> None:
    # Idempotencia para extensiones
    op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")

    # 1. Crear tabla / agregar columna / etc.
    op.execute("""
        CREATE TABLE <tabla> (
            id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            organization_id UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
            ...
            created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)

    # 2. Índices declarados en el SDD §"Índices PostgreSQL Recomendados"
    op.execute("CREATE INDEX idx_<tabla>_organization_id ON <tabla> (organization_id)")

    # 3. RLS (obligatorio si la tabla tiene organization_id)
    op.execute("ALTER TABLE <tabla> ENABLE ROW LEVEL SECURITY")
    op.execute("""
        CREATE POLICY <tabla>_tenant_isolation ON <tabla>
        USING (organization_id = current_setting('app.current_tenant_id', true)::uuid)
        WITH CHECK (organization_id = current_setting('app.current_tenant_id', true)::uuid)
    """)
    op.execute("ALTER TABLE <tabla> FORCE ROW LEVEL SECURITY")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS <tabla> CASCADE")
```

## Checklist pre-commit

- [ ] El archivo está en `src/adminprop/db/migrations/versions/` con nombre `YYYYMMDD_HHMMSS_<slug>.py`.
- [ ] Los nombres y tipos de columna coinciden **exactamente** con `spec_data_model.md`.
- [ ] Si la tabla tiene `organization_id`: `ENABLE ROW LEVEL SECURITY` está habilitado **+** política `USING (... current_setting('app.current_tenant_id', true)::uuid)` **+** `FORCE ROW LEVEL SECURITY`.
- [ ] Los índices declarados en el SDD están creados (incluido el de `organization_id` solo o compuesto).
- [ ] `gen_random_uuid()` se usa como default para UUID primary keys (no `BIGSERIAL`).
- [ ] Money fields usan `NUMERIC(14,2)` o `NUMERIC(14,4)`; nunca `FLOAT/REAL`.
- [ ] Timestamps usan `TIMESTAMPTZ`, no `TIMESTAMP`.
- [ ] Fechas operativas sin hora usan `DATE`.
- [ ] Enums declarados como `TEXT` + `CHECK` (no PG ENUM types).
- [ ] `downgrade()` está implementada **o** comentada explícitamente con justificación (audit_log, liquidación finalizada, etc.).
- [ ] Si la migración es de seed: es idempotente (`ON CONFLICT DO NOTHING`).
- [ ] El docstring del archivo cita el SDD por ruta + sección.
- [ ] El commit message usa `migrate(<módulo>): ...`.

## Antipatrones

```python
# ❌ Crear tabla tenant-scoped sin RLS
op.execute("""
    CREATE TABLE contracts (
        id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        organization_id UUID NOT NULL REFERENCES organizations(id),
        monthly_amount  NUMERIC(14,2)
    )
""")
# Y no se llama ALTER TABLE ... ENABLE ROW LEVEL SECURITY.
# Causa: cross-tenant leak en la primera query que olvide filtrar.

# ✅ RLS habilitado + política + FORCE
op.execute("""
    CREATE TABLE contracts (
        id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        organization_id UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
        monthly_amount  NUMERIC(14,2)
    )
""")
op.execute("ALTER TABLE contracts ENABLE ROW LEVEL SECURITY")
op.execute("""
    CREATE POLICY contracts_tenant_isolation ON contracts
    USING (organization_id = current_setting('app.current_tenant_id', true)::uuid)
    WITH CHECK (organization_id = current_setting('app.current_tenant_id', true)::uuid)
""")
op.execute("ALTER TABLE contracts FORCE ROW LEVEL SECURITY")
```

```python
# ❌ Usar BIGSERIAL o SERIAL como PK
op.execute("CREATE TABLE payments (id BIGSERIAL PRIMARY KEY, ...)")
# Causa: hotspot en INSERT, expone tamaño del corpus, FK incoherentes
# con el resto del modelo (UUID).

# ✅ UUID v4 vía gen_random_uuid()
op.execute("""
    CREATE TABLE payments (
        id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        ...
    )
""")
```

```python
# ❌ Usar FLOAT para montos
op.execute("CREATE TABLE payments (amount FLOAT NOT NULL, ...)")
# Causa: pérdida de precisión en cálculos financieros. Inaceptable
# en cobros y liquidaciones.

# ✅ NUMERIC con escala explícita
op.execute("CREATE TABLE payments (amount NUMERIC(14,2) NOT NULL, ...)")
```

```python
# ❌ Usar PG ENUM types
op.execute("CREATE TYPE contract_status AS ENUM ('draft','active','terminated')")
op.execute("CREATE TABLE contracts (status contract_status NOT NULL, ...)")
# Causa: agregar un valor requiere ALTER TYPE (no aceptado en transacción
# con otros statements en algunas versiones de PG), y migrar es doloroso.

# ✅ TEXT con CHECK constraint (puede modificarse en migración futura)
op.execute("""
    CREATE TABLE contracts (
        ...,
        status TEXT NOT NULL CHECK (status IN ('draft','active','terminated','expired'))
    )
""")
```

```python
# ❌ Modificar el schema en código sin migración
# El developer agrega un campo al modelo SQLAlchemy:
class Contract(Base):
    monthly_amount: Mapped[Decimal] = mapped_column(...)
    late_fee_percent: Mapped[Decimal] = mapped_column(...)  # ¡nuevo!
# Sin Alembic, prod queda fuera de sync con el modelo.

# ✅ Migración explícita primero
# 1. alembic revision -m "add_late_fee_percent_to_contracts"
# 2. Editar el archivo: op.execute("ALTER TABLE contracts ADD COLUMN late_fee_percent NUMERIC(5,2)")
# 3. Recién entonces actualizar el modelo SQLAlchemy.
```

```python
# ❌ Política RLS sin missing_ok ni FORCE
op.execute("""
    CREATE POLICY contracts_iso ON contracts
    USING (organization_id = current_setting('app.current_tenant_id')::uuid)
""")
# Si el middleware no setea el setting → la query falla con error
# críptico en lugar de retornar 0 filas.
# Sin FORCE → el owner de la tabla (adminprop_app) puede leer cualquier tenant.

# ✅ missing_ok=true + FORCE
op.execute("""
    CREATE POLICY contracts_iso ON contracts
    USING (organization_id = current_setting('app.current_tenant_id', true)::uuid)
    WITH CHECK (organization_id = current_setting('app.current_tenant_id', true)::uuid)
""")
op.execute("ALTER TABLE contracts FORCE ROW LEVEL SECURITY")
```

## Referencias

- `infrastructure/spec_data_model.md` — schema canónico de las tablas, convenciones, índices, política RLS.
- `infrastructure/spec_data_model.md` §"Estrategia de Seed Data" — seed global (Alembic) vs seed per-tenant (Python service).
- `infrastructure/spec_data_model.md` §"Orden de Migración" — secuencia recomendada por capas.
- Backend `CLAUDE.md` §5 "Modelo de datos" — convenciones de tipos (UUID, TIMESTAMPTZ, NUMERIC, JSONB), enums como TEXT+CHECK, soft delete universal.
- Backend `CLAUDE.md` §4 "Multi-tenancy" — el bypass via rol `adminprop_superadmin` (BYPASSRLS) es lo que permite a Super Admin operar; las migraciones no deben asumir que RLS se desactiva.
- `_index.md` §4 #23 — naming Alembic `YYYYMMDD_HHMMSS_<slug>.py` es decisión tomada.
- `_index.md` §4 #42 — bypass RLS para Super Admin via rol `adminprop_superadmin`.
- `_index.md` §4 #66 — estrategia híbrida de seed.
