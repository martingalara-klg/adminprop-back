"""Declarative base compartida por los modelos SQLAlchemy 2.0.

No hay modelos ORM todavia (issue #3 es infraestructura de migraciones;
las tablas de negocio llegan en el issue #5). `Base.metadata` existe para
que `db/migrations/env.py` tenga un `target_metadata` valido, aunque el
proyecto no usa `--autogenerate` (database-migration.md: el SQL de RLS,
CHECK e indices parciales se escribe a mano con `op.execute`).
"""

from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):  # pragma: no cover — declarativa, sin metodo custom (CLAUDE.md §3)
    """Base declarativa para futuros modelos ORM de `adminprop`."""
