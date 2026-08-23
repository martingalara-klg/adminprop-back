"""Generacion sincronica de documentos PDF (issue #24).

SDD: features/spec_module_04_cobranzas.md §RF-07 (recibo de cobro) +
§RF-08 (certificado de libre deuda). CLAUDE.md §3: "Documentos: openpyxl
(Excel) + WeasyPrint (PDF) para liquidaciones" -- este modulo es el
primer consumidor real de WeasyPrint, compartido por `payments`
(recibo) y `people` (libre deuda) para no duplicar el armado de HTML +
encabezado de la administradora (RF-04 de Modulo 7).

Ambos documentos son de una sola pagina y se generan SINCRONICAMENTE
dentro del request HTTP (RF-07/RF-08: "< 5s, no va a Celery") -- no
encolan nada en Celery, a diferencia del patron async-worker.md que
aplica a operaciones > 5s.
"""

from __future__ import annotations

from adminprop.shared.pdf.renderer import render_pdf_from_html
from adminprop.shared.pdf.template import (
    DocumentSection,
    document_html,
    document_html_multi_section,
)

__all__ = [
    "DocumentSection",
    "document_html",
    "document_html_multi_section",
    "render_pdf_from_html",
]
