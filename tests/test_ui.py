from __future__ import annotations

from pathlib import Path


def test_ui_exposes_notes_and_provenance_controls() -> None:
    root = Path(__file__).parents[1]
    html = (root / "app/web/index.html").read_text(encoding="utf-8")
    script = (root / "app/web/static/app.js").read_text(encoding="utf-8")
    styles = (root / "app/web/static/styles.css").read_text(encoding="utf-8")
    assert "provenance-panel" in html
    assert "revalidate-button" in html
    assert "function noteEditor" in script
    assert "function renderProvenance" in script
    assert "keyword_sources" in script
    assert ".origin-badge" in styles


def test_ui_exposes_portal_and_extension_controls() -> None:
    root = Path(__file__).parents[1]
    html = (root / "app/web/index.html").read_text(encoding="utf-8")
    script = (root / "app/web/static/app.js").read_text(encoding="utf-8")
    manifest = (root / "extension/manifest.json").read_text(encoding="utf-8")
    target = (root / "extension/target.js").read_text(encoding="utf-8")
    assert "auth-panel" in html
    assert "auth-form" in html
    assert "auth-mode-toggle" in html
    assert "New user? Create account" in html
    assert 'id="status-strip"' in html
    assert "setAuthMode" in script
    assert "validateAuth" in script
    assert '$("status-strip").hidden = true' in script
    assert "updateProfileStatus" in script
    assert '$("resume-file").addEventListener("change"' in script
    assert "Upload a resume before analyzing a job." in script
    assert "tracker-status" in html
    assert "prefill-form" in html
    assert "X-CSRF-Token" in script
    assert "JOB_ASSIST_PREFILL" in script
    assert "chrome.action.onClicked" in (root / "extension/background.js").read_text()
    assert "job-assist-prefill-toast" in target
    assert "Never submits forms" in manifest
