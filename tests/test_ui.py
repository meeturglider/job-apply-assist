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
    assert 'id="resume-dialog"' in html
    assert "Resume your work" in html
    assert "Start new session" in html
    assert "New user? Create account" in html
    assert 'id="status-strip"' in html
    assert "setAuthMode" in script
    assert "validateAuth" in script
    assert "function showResumeDialog" in script
    assert "function startNewSession" in script
    assert "function historyButton" in script
    assert "error.status = response.status" in script
    assert '$("status-strip").hidden = true' in script
    assert "updateProfileStatus" in script
    assert '"Awaiting resume upload"' in script
    assert '"Resume source missing"' not in script
    assert '$("resume-file").addEventListener("change"' in script
    assert "Upload a resume before analyzing a job." in script
    assert 'button.textContent = "Analyzing..."' in script
    assert 'showProgress("analysis", "Analyzing job...")' in script
    assert "tracker-status" in html
    assert "prefill-form" in html
    assert "X-CSRF-Token" in script
    assert "pruneResumeDrafts" in script
    assert "JOB_ASSIST_PREFILL" in script
    assert "chrome.action.onClicked" in (root / "extension/background.js").read_text()
    assert "job-assist-prefill-toast" in target
    assert "Never submits forms" in manifest


def test_ui_exposes_structured_resume_editor() -> None:
    root = Path(__file__).parents[1]
    html = (root / "app/web/index.html").read_text(encoding="utf-8")
    script = (root / "app/web/static/app.js").read_text(encoding="utf-8")
    styles = (root / "app/web/static/styles.css").read_text(encoding="utf-8")
    assert 'id="edit-resume-button"' in html
    assert 'id="resume-editor"' in html
    assert "function renderResumeEditor" in script
    assert 'el("button", "icon-button", "+")' in script
    assert 'roleField: "role"' in script
    assert 'experienceField: "company"' in script
    assert 'method: "PUT"' in script
    assert "/resume`, {" in script
    assert "Manually edited" in script
    assert 'const RESUME_DRAFT_PREFIX = "job-assist:resume-draft:"' in script
    assert "localStorage.setItem" in script
    assert "Restored your unsaved resume edits." in script
    assert "clearAllResumeDrafts();" in script
    assert ".icon-button" in styles


def test_progress_steps_mark_done_and_active_states() -> None:
    root = Path(__file__).parents[1]
    html = (root / "app/web/index.html").read_text(encoding="utf-8")
    script = (root / "app/web/static/app.js").read_text(encoding="utf-8")
    styles = (root / "app/web/static/styles.css").read_text(encoding="utf-8")
    assert "const stageMaps = {" in script
    assert "extracting: 1," in script
    assert "reviewing: 2," in script
    assert ">Review with local model</li>" in html
    assert "notes: 0," in script
    assert "scoring: 1," in script
    assert "suggestions: 2," in script
    assert 'step.classList.toggle("done", index < active)' in script
    assert 'step.classList.toggle("active", index === active)' in script
    assert "@keyframes step-blink" in styles
    assert "animation: step-blink 1s ease-in-out infinite;" in styles
    assert ".progress-steps li.done" in styles
    assert "opacity: 0.5;" in styles


def test_saving_notes_requires_manual_reanalysis() -> None:
    root = Path(__file__).parents[1]
    html = (root / "app/web/index.html").read_text(encoding="utf-8")
    script = (root / "app/web/static/app.js").read_text(encoding="utf-8")
    save_function = script.split("async function saveApplicationNotes", 1)[1].split("\n}\n", 1)[0]
    assert "revalidating: false," in script
    assert "revalidateNotes" not in save_function
    assert "Evidence notes saved. Reanalyze when you're ready." in save_function
    assert ">Reanalyze notes</button>" in html
    assert '"Reanalyze notes to generate"' in script
    assert 'button.textContent = "Reanalyze notes";' in script
    assert "if (!state.currentId || state.revalidating) return;" in script
    assert "stopPolling();\n    poll();" in script
