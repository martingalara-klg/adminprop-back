"""Layout HTML compartido de los documentos PDF de una pagina (issue #24).

SDD: features/spec_module_04_cobranzas.md §RF-07/RF-08 + features/
spec_module_07_administracion.md §RF-04 ("Encabezado de liquidaciones:
nombre de la administradora, CUIT, contacto"). El mismo `billing_header`
que usan los exports de liquidaciones (Modulo 5, todavia inexistente)
encabeza tambien el recibo de cobro y el libre deuda -- una sola fuente
de verdad (`AdministracionRepository.get_organization_settings`).

Deliberadamente sin Jinja2 (no es una dependencia declarada en los SDDs,
CLAUDE.md §3 -- "Nunca hacer sin preguntar: Agregar dependencias no
mencionadas en los SDDs"): `string.Template` + `html.escape` alcanzan
para un documento de una pagina sin loops/condicionales complejos.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from html import escape
from string import Template

# CSS inline minimo: una pagina A4, sin dependencias externas (fuentes
# del sistema) -- WeasyPrint no tiene acceso a red durante el render.
_PAGE_STYLE = """
@page { size: A4; margin: 2cm; }
body { font-family: sans-serif; color: #1a1a1a; font-size: 12pt; }
h1 { font-size: 16pt; margin-bottom: 0.2em; }
.header { border-bottom: 2px solid #1a1a1a; padding-bottom: 0.5em; margin-bottom: 1em; }
.header .org-name { font-size: 14pt; font-weight: bold; }
.header .org-meta { font-size: 10pt; color: #444; }
table.detail { width: 100%; border-collapse: collapse; margin-top: 1em; }
table.detail td { padding: 0.4em 0; border-bottom: 1px solid #ddd; }
table.detail td.label { color: #444; width: 40%; }
.footer { margin-top: 2em; font-size: 9pt; color: #666; }
"""

_DOCUMENT_TEMPLATE = Template(
    """<!doctype html>
<html lang="es">
<head>
<meta charset="utf-8">
<title>$title</title>
<style>$style</style>
</head>
<body>
<div class="header">
  <div class="org-name">$org_name</div>
  <div class="org-meta">$org_meta</div>
</div>
<h1>$title</h1>
$body
</body>
</html>"""
)


def _billing_header_lines(*, name: str | None, cuit: str | None, contact: str | None) -> tuple:
    # RF-04: "billing_header no existe todavia... su ausencia se
    # serializa como todos los campos en None" -- el documento igual se
    # emite (RF-07/08 no exigen encabezado completo), solo con los datos
    # disponibles.
    org_name = escape(name) if name else "Administracion de Propiedades"
    meta_parts = [part for part in (cuit and f"CUIT {cuit}", contact) if part]
    org_meta = escape(" · ".join(meta_parts)) if meta_parts else ""
    return org_name, org_meta


def document_html(
    *,
    title: str,
    billing_header: dict,
    body_rows: list[tuple[str, str]],
    footer: str | None = None,
) -> str:
    """Arma el HTML de una pagina: encabezado de la administradora
    (RF-04) + `title` + una tabla `label: value` con los datos del
    documento (capital/interes/TC para el recibo, contratos/propiedades
    para el libre deuda) + pie opcional (fecha de emision, etc.).

    `body_rows` ya viene con los valores formateados como texto (moneda,
    fechas) por el caller -- este modulo solo escapa HTML y arma el
    layout, no conoce reglas de negocio (RN-P/RN-L) ni formato de datos
    especifico del dominio.
    """
    org_name, org_meta = _billing_header_lines(
        name=billing_header.get("name"),
        cuit=billing_header.get("cuit"),
        contact=billing_header.get("contact"),
    )
    rows_html = "\n".join(
        f'<tr><td class="label">{escape(label)}</td><td>{escape(value)}</td></tr>'
        for label, value in body_rows
    )
    body = f'<table class="detail">{rows_html}</table>'
    if footer:
        body += f'<div class="footer">{escape(footer)}</div>'
    return _DOCUMENT_TEMPLATE.substitute(
        title=escape(title),
        style=_PAGE_STYLE,
        org_name=org_name,
        org_meta=org_meta,
        body=body,
    )


# ─── Documento multi-seccion (issue #30) ────────────────────────────────
#
# RF-04 (spec_module_05_liquidaciones.md): el export de una liquidacion
# agrupa por propiedad (una seccion por propiedad, con subtotal) y cierra
# con el consolidado del propietario -- `document_html` (arriba) solo arma
# una unica tabla label:value, insuficiente para varias secciones con
# encabezado propio. Extension del generador compartido (CLAUDE.md §3
# "generador compartido en shared/pdf/ -- extendelo si hace falta
# (multi-pagina)") en vez de un template ad-hoc en `modules/settlements/`,
# mismo criterio de reuso que `document_html`.

_SECTION_STYLE_EXTRA = """
h2.section-heading { font-size: 12pt; margin-top: 1.2em; margin-bottom: 0.3em; border-bottom: 1px solid #999; }
table.detail tr.subtotal td { font-weight: bold; border-top: 1px solid #1a1a1a; }
"""


@dataclass(frozen=True)
class DocumentSection:
    """Una seccion del documento (una propiedad, o el consolidado final).

    `rows`: pares `(descripcion, monto_formateado)` ya formateados por el
    caller (moneda, fecha) -- este modulo solo escapa HTML. `subtotal`:
    fila final destacada de la seccion (`None` si no aplica, ej. una
    seccion sin subtotal propio)."""

    heading: str
    rows: list[tuple[str, str]] = field(default_factory=list)
    subtotal: tuple[str, str] | None = None


def document_html_multi_section(
    *,
    title: str,
    billing_header: dict,
    sections: list[DocumentSection],
    footer: str | None = None,
) -> str:
    """RF-04: encabezado de la organizacion (RF-04 Modulo 7) + una tabla
    por `DocumentSection` (propiedad) + seccion final de consolidado (el
    caller la agrega como una `DocumentSection` mas, con `heading`
    "Consolidado" o similar) -- sigue siendo una unica pagina HTML
    (WeasyPrint pagina automaticamente si el contenido no entra, "una
    pagina" del RF-07/RF-08 no aplica a liquidaciones con muchas
    propiedades)."""
    org_name, org_meta = _billing_header_lines(
        name=billing_header.get("name"),
        cuit=billing_header.get("cuit"),
        contact=billing_header.get("contact"),
    )

    sections_html_parts: list[str] = []
    for section in sections:
        rows_html = "\n".join(
            f'<tr><td class="label">{escape(label)}</td><td>{escape(value)}</td></tr>'
            for label, value in section.rows
        )
        subtotal_html = ""
        if section.subtotal is not None:
            sub_label, sub_value = section.subtotal
            subtotal_html = (
                f'<tr class="subtotal"><td class="label">{escape(sub_label)}</td>'
                f"<td>{escape(sub_value)}</td></tr>"
            )
        sections_html_parts.append(
            f'<h2 class="section-heading">{escape(section.heading)}</h2>'
            f'<table class="detail">{rows_html}{subtotal_html}</table>'
        )

    body = "\n".join(sections_html_parts)
    if footer:
        body += f'<div class="footer">{escape(footer)}</div>'

    return _DOCUMENT_TEMPLATE.substitute(
        title=escape(title),
        style=_PAGE_STYLE + _SECTION_STYLE_EXTRA,
        org_name=org_name,
        org_meta=org_meta,
        body=body,
    )
