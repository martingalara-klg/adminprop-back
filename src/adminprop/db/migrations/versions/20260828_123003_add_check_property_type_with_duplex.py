"""add_check_property_type_with_duplex — catalogo cerrado para
properties.property_type, incluye duplex (issue #103)

SDD: infrastructure/spec_data_model.md v1.3 §Capa 2 "properties" (columna
     property_type) + core/sdd_02_domain_model.md v1.5 §2.5 (Propiedad)
Implements: CA-01-10, decision #122 (docs/sdd/_index.md)

Issue #103 (ronda de feedback #2 del PO, 2026-08-28): agregar `duplex` al
catalogo de `property_type` junto a departamento/casa/local/cochera/otro.

Decision de implementacion: `property_type` NUNCA tuvo un CHECK en DB --
la migracion original (`20260815_100000_create_capa2_propiedades.py`,
issue #14) lo declaro deliberadamente como texto libre sugerido en UI
("sin CHECK, spec no lo restringe a un catalogo cerrado"), y el schema
Pydantic (`modules/properties/schemas.py`) es `str` con `max_length` en
vez de `Literal` por ese mismo motivo. El issue #103 (y la SDD actualizada
en el commit `docs(propiedades): ...` de este mismo PR) cierra el
catalogo ahora: en vez de "agregar un valor a un CHECK existente" (patron
de `20260821_100000_add_quote_approved_to_notifications.py`), esta
migracion CREA el CHECK por primera vez, ya con los 6 valores vigentes
(el catalogo previo + `duplex`).

Filas legacy: por si algun ambiente tiene valores fuera del catalogo
sugerido (texto libre, nunca enforced), se normalizan a `otro` ANTES de
agregar el CHECK -- evita que `ADD CONSTRAINT` falle contra datos viejos
no anticipados. Es un `UPDATE` acotado a filas que ya violarian el CHECK
nuevo, no-op si todas las filas ya estan en el catalogo (caso esperado en
dev/MVP, decision #111: sin infra cloud/CD todavia).
"""

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "20260828_123003"
down_revision: str | None = "20260827_100000"
branch_labels: tuple[str, ...] | None = None
depends_on: tuple[str, ...] | None = None

_CONSTRAINT_NAME = "properties_property_type_check"
_CATALOG = ("departamento", "casa", "duplex", "local", "cochera", "otro")
_CATALOG_SQL = ", ".join(f"'{value}'" for value in _CATALOG)


def upgrade() -> None:
    # Normaliza cualquier valor fuera del catalogo (texto libre historico,
    # nunca enforced hasta esta migracion) a 'otro' antes de agregar el
    # CHECK -- sin esto, ADD CONSTRAINT falla si existe una sola fila con
    # un valor inesperado.
    op.execute(
        f"""
        UPDATE properties
        SET property_type = 'otro'
        WHERE property_type NOT IN ({_CATALOG_SQL})
        """
    )

    op.execute(
        f"""
        ALTER TABLE properties ADD CONSTRAINT {_CONSTRAINT_NAME}
        CHECK (property_type IN ({_CATALOG_SQL}))
        """
    )


def downgrade() -> None:
    # Angostar el catalogo de vuelta a los 5 valores originales (sin
    # duplex) requeriria decidir que hacer con filas 'duplex' existentes.
    # A diferencia de `20260821_100000_add_quote_approved_to_notifications`
    # (que borra las notificaciones del evento removido -- dato efimero,
    # sin dependientes), `properties` es una entidad de negocio central con
    # FKs entrantes (contratos, cobros, reparaciones); borrar la fila
    # destruiria ese historial. Se remapea 'duplex' -> 'otro' (el catch-all
    # del catalogo original) en vez de eliminar la propiedad, y se dropea
    # el CHECK por completo (no se re-crea uno mas angosto): asi el estado
    # post-downgrade coincide exactamente con el pre-upgrade (texto libre,
    # sin CHECK), que es el contrato real que esta migracion esta revirtiendo.
    # Sin infra cloud/CD en el MVP (decision #111), downgrade solo se
    # ejerce en dev/test -- nunca contra datos de produccion reales.
    op.execute("UPDATE properties SET property_type = 'otro' WHERE property_type = 'duplex'")
    op.execute(f"ALTER TABLE properties DROP CONSTRAINT {_CONSTRAINT_NAME}")
