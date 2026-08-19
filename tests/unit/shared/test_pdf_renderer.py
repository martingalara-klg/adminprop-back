"""tests/unit/shared/test_pdf_renderer.py -- issue #24.

Cobertura del wrapper de WeasyPrint (`shared/pdf/renderer.py`). Requiere
las librerias de sistema de WeasyPrint (Pango/cairo) -- disponibles en
el contenedor `api` (ver `docker/Dockerfile.api`), no en el host.
"""

from __future__ import annotations

from adminprop.shared.pdf.renderer import render_pdf_from_html


def test_render_pdf_from_html_returns_valid_pdf_bytes():
    html = "<html><body><h1>Hola</h1></body></html>"

    pdf_bytes = render_pdf_from_html(html)

    assert isinstance(pdf_bytes, bytes)
    assert pdf_bytes.startswith(b"%PDF-")
