"""Entry point de Alembic.

`sqlalchemy.url` NO se lee de `alembic.ini`: se toma de
`adminprop.config.get_settings().database_url` (que a su vez lee
`DATABASE_URL` de entorno/`.env`), para no duplicar la configuracion de
conexion entre local (`docker/docker-compose.yml`), CI
(`.github/workflows/ci.yml`) y produccion.

`target_metadata` apunta a `Base.metadata` (issue #3: sin modelos ORM
todavia) para que Alembic pueda comparar el estado si algun dia se usa
`--autogenerate` en un modulo puntual; la convencion del proyecto
(docs/skills/database-migration.md) es escribir el SQL de las
migraciones a mano con `op.execute`, así que esto no se ejercita hoy.
"""

from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from adminprop.config import get_settings
from adminprop.db.base import Base

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def get_url() -> str:
    return get_settings().database_url


def run_migrations_offline() -> None:
    """Genera SQL sin conectarse a la base (modo `--sql`)."""
    context.configure(
        url=get_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Corre las migraciones conectandose a la base (modo normal)."""
    configuration = config.get_section(config.config_ini_section) or {}
    configuration["sqlalchemy.url"] = get_url()

    connectable = engine_from_config(
        configuration,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
