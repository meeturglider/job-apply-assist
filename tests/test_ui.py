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
