import os
from pathlib import Path
from types import SimpleNamespace

from filemind.paperless.client import PaperlessClient


class DummyResponse:
    def __init__(self, status_code=200, payload=None, text="OK"):
        self.status_code = status_code
        self._payload = payload or {}
        self.text = text

    def json(self):
        return self._payload


def test_paperless_client_loads_configuration_from_env(monkeypatch: "pytest.MonkeyPatch", tmp_path: Path) -> None:
    monkeypatch.setenv("PAPERLESS_URL", "http://localhost:8000")
    monkeypatch.setenv("PAPERLESS_TOKEN", "test-token")

    client = PaperlessClient()

    assert client.config.base_url == "http://localhost:8000"
    assert client.config.api_token == "test-token"


def test_upload_document_posts_to_paperless(monkeypatch: "pytest.MonkeyPatch", tmp_path: Path) -> None:
    monkeypatch.setenv("PAPERLESS_URL", "http://localhost:8000")
    monkeypatch.setenv("PAPERLESS_TOKEN", "test-token")

    document_path = tmp_path / "invoice.pdf"
    document_path.write_bytes(b"invoice content")

    client = PaperlessClient()

    def fake_post(url, files, data, timeout):
        assert url == "http://localhost:8000/api/documents/post_document/"
        assert data["title"] == "Invoice 2026"
        assert data["tags"] == "invoice,2026"
        assert files["document"].name == str(document_path)
        return DummyResponse(status_code=200, payload={"document": 123})

    monkeypatch.setattr(client.session, "post", fake_post)

    response = client.upload_document(
        document_path,
        title="Invoice 2026",
        tags=["invoice", "2026"],
        correspondent="Supplier Inc.",
        doc_type="invoice",
    )

    assert response["document"] == 123


def test_upload_document_raises_on_missing_file(monkeypatch: "pytest.MonkeyPatch") -> None:
    monkeypatch.setenv("PAPERLESS_URL", "http://localhost:8000")
    monkeypatch.setenv("PAPERLESS_TOKEN", "test-token")

    client = PaperlessClient()
    missing_path = Path("does_not_exist.pdf")

    try:
        client.upload_document(missing_path, title="Missing", tags=None, correspondent=None, doc_type=None)
        assert False, "Expected ValueError for missing file"
    except ValueError as exc:
        assert "Datei nicht gefunden" in str(exc)
