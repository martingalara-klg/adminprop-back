"""Storage local de archivos (adjuntos) -- issue #26.

SDD: infrastructure/spec_data_model.md §Capa 5 "attachments" ("Filesystem
     local (volumen Docker) en MVP: archivos binarios ... nunca en la DB.
     Convencion de columna: file_path TEXT en attachments") +
     docs/skills/tenant-isolation.md §"Storage de archivos con
     aislamiento per-tenant" (convencion de rutas por tenant) +
     features/spec_module_06_mantenimiento.md §"Validaciones"
     ("jpg/png/webp/pdf, <= 10 MB por archivo, <= 10 por entidad").

Primer consumidor real de un volumen Docker montado (mismo criterio que
`tenant-isolation.md` describe conceptualmente para bucket per-tenant):
la ruta raiz es configurable (`Settings.attachments_dir`), el nombre de
archivo persistido en disco SIEMPRE se genera server-side (UUID +
extension derivada del `content_type` ya validado) -- nunca se usa el
nombre de archivo que manda el cliente, para no permitir path traversal
(`../../etc/passwd`) ni colisiones entre uploads concurrentes.
"""

from __future__ import annotations

import re
import uuid
from pathlib import Path

from adminprop.config import get_settings

# spec_module_06_mantenimiento.md §Validaciones: "jpg/png/webp/pdf".
ALLOWED_CONTENT_TYPES: dict[str, str] = {
    "image/jpeg": "jpg",
    "image/png": "png",
    "image/webp": "webp",
    "application/pdf": "pdf",
}

# spec_module_06_mantenimiento.md §Validaciones: "<= 10 MB por archivo".
MAX_ATTACHMENT_SIZE_BYTES = 10 * 1024 * 1024

# spec_module_06_mantenimiento.md §Validaciones: "<= 10 por entidad".
MAX_ATTACHMENTS_PER_ENTITY = 10

_SAFE_SEGMENT = re.compile(r"[^a-zA-Z0-9._-]+")


class UnsupportedAttachmentTypeError(ValueError):
    """El `content_type` recibido no esta en `ALLOWED_CONTENT_TYPES`."""


class AttachmentTooLargeError(ValueError):
    """El archivo supera `MAX_ATTACHMENT_SIZE_BYTES`."""


def _tenant_entity_dir(organization_id: uuid.UUID, entity_type: str) -> Path:
    """Convencion de aislamiento por tenant (tenant-isolation.md
    §"Storage de archivos"): `<root>/<organization_id>/<entity_type>/`.
    `entity_type` ya viene de un enum cerrado (`attachments.entity_type`
    CHECK), pero se sanitiza igual por defensa en profundidad."""
    settings = get_settings()
    root = Path(settings.attachments_dir)
    safe_entity_type = _SAFE_SEGMENT.sub("_", entity_type) or "misc"
    directory = root / str(organization_id) / safe_entity_type
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def save_attachment(
    *,
    organization_id: uuid.UUID,
    entity_type: str,
    content: bytes,
    content_type: str,
) -> tuple[str, str]:
    """Persiste `content` bajo un nombre generado server-side.

    Valida `content_type` contra `ALLOWED_CONTENT_TYPES` y el tamano
    contra `MAX_ATTACHMENT_SIZE_BYTES` ANTES de escribir a disco.
    Devuelve `(file_path, file_name)` -- `file_path` es el valor absoluto
    a persistir en `attachments.file_path`; `file_name` es el nombre
    generado (UUID + extension), NO el nombre original del cliente.
    """
    if content_type not in ALLOWED_CONTENT_TYPES:
        raise UnsupportedAttachmentTypeError(content_type)
    if len(content) > MAX_ATTACHMENT_SIZE_BYTES:
        raise AttachmentTooLargeError(len(content))

    extension = ALLOWED_CONTENT_TYPES[content_type]
    file_name = f"{uuid.uuid4().hex}.{extension}"
    full_path = _tenant_entity_dir(organization_id, entity_type) / file_name
    full_path.write_bytes(content)
    return str(full_path), file_name


def read_attachment(file_path: str) -> bytes:
    """Lee el binario persistido. `file_path` es siempre el valor ya
    validado guardado en `attachments.file_path` -- nunca se construye a
    partir de input directo del cliente (evita path traversal)."""
    return Path(file_path).read_bytes()
