"""setup_extensions_and_roles — extensiones + roles PostgreSQL para RLS

SDD: infrastructure/spec_data_model.md §Principios Arquitectonicos
     + core/sdd_04_nonfunctional.md §2.3, §2.4
Implements: Decision #42 (rol adminprop_superadmin BYPASSRLS),
            RN-D01 (aislamiento multi-tenant via RLS)

Primera migracion del proyecto (issue #3, previa a cualquier tabla de
negocio — llegan en el issue #5). Provee:

1. Las extensiones que el modelo de datos requiere (`pgcrypto` para
   `gen_random_uuid()` + cifrado columnar AES-256; `btree_gist` para las
   restricciones de no-solapamiento de contratos con EXCLUDE USING gist).
   `docker/postgres/init.sql` ya las crea en el volumen local, pero esta
   migracion las asegura tambien en ambientes donde ese script no corrio
   (ej: la base de test de CI, que solo pre-crea `pgcrypto`).

2. Los roles PostgreSQL `adminprop_app` (rol default de la app, sujeto a
   RLS) y `adminprop_superadmin` (`BYPASSRLS`, solo para `/superadmin/*`
   con JWT `is_super_admin=true`). Creacion idempotente (`DO` + chequeo
   contra `pg_roles`) porque distintos entornos (CI en este repo,
   ejecuciones repetidas de `alembic upgrade head`) pueden ya tenerlos.

`ALTER DEFAULT PRIVILEGES` se declara para el rol que corre esta
migracion (tipicamente el superuser `adminprop` de conexion): así las
tablas que creen migraciones futuras (issue #5+) ya nacen con los grants
correctos para `adminprop_app`/`adminprop_superadmin`, sin tener que
repetir el GRANT tabla por tabla.
"""

from alembic import op

from adminprop.config import get_settings

# revision identifiers, used by Alembic.
revision: str = "20260812_114322"
down_revision: str | None = None
branch_labels: tuple[str, ...] | None = None
depends_on: tuple[str, ...] | None = None


def _sql_quote_literal(value: str) -> str:
    """Escapa un string para uso como literal SQL (duplica comillas simples)."""
    return value.replace("'", "''")


def upgrade() -> None:
    settings = get_settings()

    # ─── Extensiones (idempotente) ──────────────────────────────────────
    op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")
    op.execute("CREATE EXTENSION IF NOT EXISTS btree_gist")

    # ─── Rol adminprop_app: rol default de runtime, sujeto a RLS ────────
    # RN-D01 / spec_data_model §Principios: "el resto opera con
    # adminprop_app (sujeto a RLS)". NOSUPERUSER + NOBYPASSRLS explicitos
    # (aunque son el default) para que la intencion quede documentada en
    # el propio DDL.
    op.execute(
        f"""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_catalog.pg_roles WHERE rolname = 'adminprop_app') THEN
                CREATE ROLE adminprop_app
                    LOGIN
                    NOSUPERUSER
                    NOBYPASSRLS
                    PASSWORD '{_sql_quote_literal(settings.app_role_password)}';
            END IF;
        END
        $$;
        """
    )

    # ─── Rol adminprop_superadmin: BYPASSRLS, solo /superadmin/* ────────
    # Decision #42 / spec_module_00_superadmin §RN-01: opera con este rol
    # solo cuando el JWT declara is_super_admin=true. El middleware
    # (issue futuro) hace `SET ROLE adminprop_superadmin` dentro de la
    # transaccion — no necesita loguearse directo con este rol en runtime
    # normal, pero se deja LOGIN habilitado para uso administrativo /
    # tooling directo (paridad con el step manual que ya existia en CI).
    op.execute(
        f"""
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM pg_catalog.pg_roles WHERE rolname = 'adminprop_superadmin'
            ) THEN
                CREATE ROLE adminprop_superadmin
                    LOGIN
                    NOSUPERUSER
                    BYPASSRLS
                    PASSWORD '{_sql_quote_literal(settings.superadmin_role_password)}';
            END IF;
        END
        $$;
        """
    )

    # ─── Permisos base ───────────────────────────────────────────────────
    # GRANT CONNECT ON DATABASE exige un identificador literal (no acepta
    # current_database() directamente) — se resuelve dinamicamente via
    # EXECUTE format(...) para no hardcodear el nombre de la base (que
    # difiere entre local `adminprop` y CI `adminprop_test`).
    op.execute(
        """
        DO $$
        BEGIN
            EXECUTE format(
                'GRANT CONNECT ON DATABASE %I TO adminprop_app, adminprop_superadmin',
                current_database()
            );
        END
        $$;
        """
    )
    op.execute("GRANT USAGE ON SCHEMA public TO adminprop_app, adminprop_superadmin")

    # RN-D (spec_data_model.md §"audit_logs"): adminprop_app nunca tiene
    # UPDATE/DELETE sobre tablas append-only. Esa restriccion se aplica
    # por-tabla en la migracion que crea cada tabla (ej: audit_logs en un
    # issue futuro, con un GRANT explicito de solo INSERT+SELECT). El
    # default de abajo es el piso general (CRUD completo) que esas
    # migraciones puntuales angostan con un REVOKE dirigido.
    op.execute(
        """
        ALTER DEFAULT PRIVILEGES IN SCHEMA public
        GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES
        TO adminprop_app, adminprop_superadmin
        """
    )
    op.execute(
        """
        ALTER DEFAULT PRIVILEGES IN SCHEMA public
        GRANT USAGE, SELECT ON SEQUENCES
        TO adminprop_app, adminprop_superadmin
        """
    )


def downgrade() -> None:
    # No se hace DROP EXTENSION: pgcrypto/btree_gist son fundacionales
    # (gen_random_uuid() en cada PK futura, EXCLUDE USING gist en
    # contratos) y `docker/postgres/init.sql` las crea igual en el
    # volumen local — un downgrade de ESTA migracion puntual no debe
    # arrastrar el DROP EXTENSION CASCADE de objetos que ninguna
    # migracion de este repo controla.
    op.execute(
        "ALTER DEFAULT PRIVILEGES IN SCHEMA public "
        "REVOKE SELECT, INSERT, UPDATE, DELETE ON TABLES "
        "FROM adminprop_app, adminprop_superadmin"
    )
    op.execute(
        "ALTER DEFAULT PRIVILEGES IN SCHEMA public "
        "REVOKE USAGE, SELECT ON SEQUENCES "
        "FROM adminprop_app, adminprop_superadmin"
    )
    op.execute("REVOKE USAGE ON SCHEMA public FROM adminprop_app, adminprop_superadmin")
    op.execute(
        """
        DO $$
        BEGIN
            EXECUTE format(
                'REVOKE CONNECT ON DATABASE %I FROM adminprop_app, adminprop_superadmin',
                current_database()
            );
        END
        $$;
        """
    )
    # DROP OWNED antes de DROP ROLE: limpia cualquier privilegio/objeto
    # que el rol pueda haber heredado (grants ya revocados arriba, pero
    # DROP ROLE falla si queda cualquier dependencia residual).
    op.execute("DROP OWNED BY adminprop_app")
    op.execute("DROP OWNED BY adminprop_superadmin")
    op.execute("DROP ROLE IF EXISTS adminprop_app")
    op.execute("DROP ROLE IF EXISTS adminprop_superadmin")
