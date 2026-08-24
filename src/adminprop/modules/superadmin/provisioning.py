"""Catalogo de seed per-tenant + generacion de slug (issue #7).

SDD: infrastructure/spec_data_model.md §"Estrategia de Seed Data" (seed
per-tenant, `OrganizationProvisioningService`) + core/sdd_03_api_contracts.md
§"Catalogo de Permisos". Implements: CA-00-01 (3 roles de sistema + settings
default sembrados en la misma transaccion que la organizacion).

No se declara un "OrganizationProvisioningService" como clase separada:
la transaccion atomica (INSERT organizations + INSERT roles x3) vive en
`repository.SuperAdminRepository.create_organization_with_roles` (un solo
round-trip de escritura, un solo commit -- ver docs/skills/module-structure.md
"repository.py hace SQL"). Este modulo solo provee los datos puros del seed
(catalogo de permisos + slug), sin tocar la sesion de DB, para poder
testearlos como funciones unitarias sin necesitar Postgres.
"""

from __future__ import annotations

import re

# sdd_03 §"Catalogo de Permisos" -- permisos atomicos completos.
ALL_PERMISSIONS: tuple[str, ...] = (
    "landlord:read",
    "landlord:manage",
    "landlord:set-commission",
    "renter:read",
    "renter:manage",
    "property:read",
    "property:manage",
    "contract:read",
    "contract:manage",
    "adjustment:apply",
    "rent-period:read",
    "payment:create",
    "payment:void",
    "charge:manage",
    "settlement:read",
    "settlement:generate",
    "settlement:issue",
    "work-order:read",
    "work-order:create",
    "work-order:quote",
    "work-order:approve",
    "work-order:close",
    "work-order:cancel",
    "attachment:manage",
    "user:manage",
    "role:read",
    "organization:configure",
    "audit:read",
    "notification:read",
)

# spec_data_model.md §"Estrategia de Seed Data":
# - owner: todos los permisos.
# - admin: todo excepto user:manage, role:manage (=> role:read en el
#   catalogo real, ver sdd_03 §"Resumen de Autorizacion por Recurso" --
#   "Usuarios, roles, configuracion de la org": owner si, admin no),
#   organization:configure, landlord:set-commission (issue #51: cambio de
#   `commission_pct` es exclusivo de owner -- sdd_03 v1.5 §"Catalogo de
#   Permisos"; reemplaza el chequeo previo por `payload.role` en
#   `LandlordService.update`).
# - maintenance: work-order:read/quote/close + attachment:manage (scoped
#   a work orders, RN-A01) + notification:read (issue #31, fix: sdd_03
#   §"Resumen de Autorizacion por Recurso" fila "Notificaciones propias"
#   dice owner/admin/maintenance = "✅" los 3 -- este catalogo tenia el
#   permiso listado en ALL_PERMISSIONS pero faltaba en MAINTENANCE_PERMISSIONS,
#   lo que hubiera devuelto 403 FORBIDDEN a un usuario `maintenance`
#   pidiendo sus propias notificaciones `work_order_created`/`quote_approved`).
_ADMIN_EXCLUDED_PERMISSIONS = frozenset(
    {"user:manage", "role:read", "organization:configure", "landlord:set-commission"}
)

OWNER_PERMISSIONS: tuple[str, ...] = ALL_PERMISSIONS
ADMIN_PERMISSIONS: tuple[str, ...] = tuple(
    p for p in ALL_PERMISSIONS if p not in _ADMIN_EXCLUDED_PERMISSIONS
)
MAINTENANCE_PERMISSIONS: tuple[str, ...] = (
    "work-order:read",
    "work-order:quote",
    "work-order:close",
    "attachment:manage",
    "notification:read",
)

# Orden estable: se sirve tal cual a `INSERT INTO roles`.
ROLE_DEFINITIONS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("owner", OWNER_PERMISSIONS),
    ("admin", ADMIN_PERMISSIONS),
    ("maintenance", MAINTENANCE_PERMISSIONS),
)

# spec_data_model.md §"Estrategia de Seed Data": settings default de toda
# organizacion nueva.
DEFAULT_ORGANIZATION_SETTINGS: dict[str, int] = {
    "grace_day": 10,
    "contract_expiry_notice_days": 60,
}

_SLUG_INVALID_CHARS = re.compile(r"[^a-z0-9]+")


def slugify(name: str) -> str:
    """Kebab-case desde `name` (RF-02: `^[a-z0-9-]+$`).

    La unicidad global (sufijos `-2`, `-3`, ...) se resuelve en el
    repository consultando la tabla `organizations` -- esta funcion es
    pura (sin I/O) para poder testearla sin DB.
    """
    base = _SLUG_INVALID_CHARS.sub("-", name.strip().lower()).strip("-")
    return base or "org"
