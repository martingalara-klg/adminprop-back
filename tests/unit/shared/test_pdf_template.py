"""tests/unit/shared/test_pdf_template.py -- issue #24.

Cobertura pura (sin WeasyPrint) del armado de HTML de
`shared/pdf/template.py` -- escaping, encabezado de la administradora
(billing_header ausente/parcial/completo) y pie opcional.
"""

from __future__ import annotations

from adminprop.shared.pdf.template import document_html


def test_document_html_includes_title_and_rows():
    html = document_html(
        title="Recibo de cobro",
        billing_header={"name": "Acme SRL", "cuit": "20111111112", "contact": "hola@acme.com"},
        body_rows=[("Inquilino", "Juan Perez"), ("Capital", "1000.00 ARS")],
    )

    assert "Recibo de cobro" in html
    assert "Acme SRL" in html
    assert "CUIT 20111111112" in html
    assert "hola@acme.com" in html
    assert "Juan Perez" in html
    assert "1000.00 ARS" in html


def test_document_html_defaults_when_billing_header_is_empty():
    """RF-04: `billing_header` ausente en settings persistidos -- el
    documento igual se emite, con un nombre generico."""
    html = document_html(
        title="Certificado de libre deuda",
        billing_header={},
        body_rows=[("Inquilino", "Juan Perez")],
    )

    assert "Administracion de Propiedades" in html
    assert "Certificado de libre deuda" in html


def test_document_html_escapes_untrusted_values():
    html = document_html(
        title="Recibo de cobro",
        billing_header={"name": "<script>alert(1)</script>", "cuit": None, "contact": None},
        body_rows=[("Inquilino", "<b>Juan</b>")],
    )

    assert "<script>" not in html
    assert "&lt;script&gt;" in html
    assert "<b>Juan</b>" not in html
    assert "&lt;b&gt;Juan&lt;/b&gt;" in html


def test_document_html_includes_footer_when_provided():
    html = document_html(
        title="Certificado de libre deuda",
        billing_header={"name": "Acme SRL", "cuit": None, "contact": None},
        body_rows=[("Inquilino", "Juan Perez")],
        footer="Emitido el 01/01/2026.",
    )

    assert "Emitido el 01/01/2026." in html


def test_document_html_omits_org_meta_when_no_cuit_or_contact():
    html = document_html(
        title="Recibo de cobro",
        billing_header={"name": "Acme SRL", "cuit": None, "contact": None},
        body_rows=[],
    )

    assert '<div class="org-meta"></div>' in html
