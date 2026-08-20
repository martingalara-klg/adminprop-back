"""tests/unit/shared/test_storage_local.py -- issue #26.

Unit tests puros (sin DB) de `shared/storage/local.py`: guardado/lectura
de adjuntos, sanitizacion de nombres (UUID + extension) y validaciones de
tipo/tamano. Mismo criterio que `tests/unit/shared/test_jwt.py` (usa
`tmp_path` + `monkeypatch` para aislar `Settings.attachments_dir`).
"""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest

from adminprop.config import get_settings
from adminprop.shared.storage import local as storage


@pytest.fixture(autouse=True)
def _isolated_attachments_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("ATTACHMENTS_DIR", str(tmp_path))
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


class TestSaveAttachment:
    def test_saves_content_under_organization_and_entity_type_directory(self, tmp_path):
        org_id = uuid.uuid4()

        file_path, file_name = storage.save_attachment(
            organization_id=org_id,
            entity_type="work_order",
            content=b"fake-image-bytes",
            content_type="image/jpeg",
        )

        assert file_name.endswith(".jpg")
        resolved = Path(file_path).resolve()
        assert resolved.is_relative_to(tmp_path.resolve())
        assert str(org_id) in file_path
        assert "work_order" in file_path

    def test_generated_file_name_is_never_the_client_provided_name(self):
        org_id = uuid.uuid4()

        _file_path, file_name = storage.save_attachment(
            organization_id=org_id,
            entity_type="work_order",
            content=b"data",
            content_type="image/png",
        )

        # RN de sanitizacion: el nombre NUNCA lo decide el cliente -- se
        # genera server-side (UUID hex + extension).
        assert file_name != "../../etc/passwd"
        assert len(file_name.split(".")[0]) == 32  # uuid4().hex

    def test_rejects_unsupported_content_type(self):
        with pytest.raises(storage.UnsupportedAttachmentTypeError):
            storage.save_attachment(
                organization_id=uuid.uuid4(),
                entity_type="work_order",
                content=b"data",
                content_type="application/zip",
            )

    def test_rejects_file_over_max_size(self):
        oversized = b"0" * (storage.MAX_ATTACHMENT_SIZE_BYTES + 1)

        with pytest.raises(storage.AttachmentTooLargeError):
            storage.save_attachment(
                organization_id=uuid.uuid4(),
                entity_type="work_order",
                content=oversized,
                content_type="image/jpeg",
            )

    def test_sanitizes_entity_type_path_segment_preventing_traversal(self, tmp_path):
        """Un `entity_type` con separadores de path (nunca deberia pasar,
        viene de un enum cerrado -- CHECK de DB) no debe poder escribir
        fuera de `<root>/<organization_id>/` (defensa en profundidad)."""
        org_id = uuid.uuid4()

        file_path, _file_name = storage.save_attachment(
            organization_id=org_id,
            entity_type="../../etc",
            content=b"data",
            content_type="application/pdf",
        )

        resolved = Path(file_path).resolve()
        org_root = (tmp_path / str(org_id)).resolve()
        assert resolved.is_relative_to(org_root)


class TestReadAttachment:
    def test_reads_back_exact_bytes_written(self):
        org_id = uuid.uuid4()
        content = b"contenido de prueba"
        file_path, _file_name = storage.save_attachment(
            organization_id=org_id,
            entity_type="work_order_quote",
            content=content,
            content_type="image/webp",
        )

        result = storage.read_attachment(file_path)

        assert result == content
