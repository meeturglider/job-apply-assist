from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.models import ArtifactInfo


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        llm_provider="none",
        master_resume_path=tmp_path / "MASTER_RESUME.md",
        portfolio_base_url="http://127.0.0.1:9",
        data_dir=tmp_path / "data",
    )


def test_html_artifact_is_inline_for_preview_and_attachment_for_download(
    tmp_path: Path,
) -> None:
    app = create_app(_settings(tmp_path))
    service = app.state.service
    record = service._new_record("Acme", "Platform Engineer", "")
    directory = service._directory(record.id)
    html = "<html><body>Preview</body></html>"
    (directory / "resume.html").write_text(html, encoding="utf-8")
    record.artifacts = [
        ArtifactInfo(kind="html", filename="resume.html", size=len(html.encode("utf-8")))
    ]
    service.store.save(record)
    url = f"/api/applications/{record.id}/artifacts/resume.html"

    with TestClient(app) as client:
        preview = client.get(url)
        assert preview.status_code == 200
        assert preview.headers["content-disposition"].startswith("inline")
        assert preview.headers["x-content-type-options"] == "nosniff"
        assert "Preview" in preview.text

        download = client.get(f"{url}?download=1")
        assert download.status_code == 200
        assert download.headers["content-disposition"].startswith("attachment")
        assert "Preview" in download.text


def test_binary_artifact_always_downloads(tmp_path: Path) -> None:
    app = create_app(_settings(tmp_path))
    service = app.state.service
    record = service._new_record("Acme", "Platform Engineer", "")
    directory = service._directory(record.id)
    pdf = b"%PDF-1.4\n%%EOF\n"
    (directory / "resume.pdf").write_bytes(pdf)
    record.artifacts = [
        ArtifactInfo(kind="pdf", filename="resume.pdf", size=len(pdf)),
    ]
    service.store.save(record)

    with TestClient(app) as client:
        response = client.get(f"/api/applications/{record.id}/artifacts/resume.pdf")
    assert response.status_code == 200
    assert response.headers["content-disposition"].startswith("attachment")
    assert response.headers["cache-control"] == "no-store"
    assert response.content == pdf
