"""Wrapper minimo sobre WeasyPrint (issue #24).

Aislado en su propio modulo para que `payments`/`people` no importen
`weasyprint` directamente (un solo punto de contacto con la libreria,
mismo criterio que `shared/encryption/pgcrypto.py` aisla pgcrypto) y
para que los tests unitarios puedan mockear `render_pdf_from_html` sin
requerir las librerias de sistema de Pango/cairo (esas si son necesarias
en runtime real -- ver `docker/Dockerfile.api`).
"""

from __future__ import annotations

from weasyprint import HTML


def render_pdf_from_html(html: str) -> bytes:
    """RF-07/RF-08: renderiza `html` (una pagina, sin recursos externos)
    a PDF de forma SINCRONICA -- ambos RF declaran "< 5s, no va a
    Celery". Sin `base_url`: el HTML generado por
    `shared/pdf/template.py` es autocontenido (sin `<img>`/`<link>`
    externos), no necesita resolver rutas relativas."""
    return HTML(string=html).write_pdf()
