from __future__ import annotations

import asyncio
import time
from pathlib import Path

from fastapi.testclient import TestClient

from app.auth import AccountStore
from app.config import Settings
from app.jd.contacts import extract_contacts
from app.main import create_app
from app.models import AnalysisResult, ContactCandidate, JobDescription
from app.workspaces import build_account_workspace


def _portal_settings(tmp_path: Path, master_text: str, **overrides: object) -> Settings:
    master = tmp_path / "MASTER_RESUME.md"
    master.write_text(master_text, encoding="utf-8")
    defaults: dict[str, object] = {
        "llm_provider": "none",
        "master_resume_path": master,
        "portfolio_base_url": "http://127.0.0.1:9",
        "data_dir": tmp_path / "data",
        "shared_password": "",
        "portal_mode": True,
        "rate_limit_per_minute": 120,
    }
    defaults.update(overrides)
    return Settings(**defaults)


def _register(client: TestClient, email: str) -> dict[str, str]:
    response = client.post(
        "/api/auth/register",
        json={"email": email, "password": "long-password"},
    )
    assert response.status_code == 200
    payload = response.json()
    return {"x-csrf-token": str(payload["csrf_token"])}


def _login(client: TestClient, email: str) -> dict[str, str]:
    response = client.post(
        "/api/auth/login",
        json={"email": email, "password": "long-password"},
    )
    assert response.status_code == 200
    payload = response.json()
    return {"x-csrf-token": str(payload["csrf_token"])}


def _wait_for_status(
    client: TestClient, application_id: str, headers: dict[str, str], statuses: set[str]
) -> str:
    for _ in range(300):
        response = client.get(f"/api/applications/{application_id}/status", headers=headers)
        assert response.status_code == 200
        status = str(response.json()["application"]["status"])
        if status in statuses:
            return status
        time.sleep(0.02)
    raise AssertionError(f"status did not reach {sorted(statuses)}")


def test_portal_sessions_are_isolated_with_per_account_storage(
    tmp_path: Path, master_text: str
) -> None:
    app = create_app(_portal_settings(tmp_path, master_text))
    with TestClient(app) as first, TestClient(app) as second:
        first_headers = _register(first, "first@example.com")
        second_headers = _register(second, "second@example.com")
        assert first_headers != second_headers
        first_me = first.get("/api/auth/me").json()
        second_me = second.get("/api/auth/me").json()
        assert first_me["account"]["email"] == "first@example.com"
        assert second_me["account"]["email"] == "second@example.com"
        assert first_me["session_only"] is False
        assert second_me["session_only"] is False

        uploaded = first.post(
            "/api/profile/resume",
            json={"text": master_text, "filename": "resume.md"},
            headers=first_headers,
        )
        assert uploaded.status_code == 200
        analyzed = first.post(
            "/api/analyze",
            json={"text": "Python engineer needed", "company": "Acme", "role": "Engineer"},
            headers=first_headers,
        )
        assert analyzed.status_code == 200
        application_id = analyzed.json()["application"]["id"]

        assert len(first.get("/api/applications").json()["applications"]) == 1
        assert second.get("/api/applications").json()["applications"] == []
        assert second.get(f"/api/applications/{application_id}").status_code == 404

        data_dir = tmp_path / "data"
        first_account = app.state.accounts.authenticate("first@example.com", "long-password")
        second_account = app.state.accounts.authenticate("second@example.com", "long-password")
        assert first_account is not None and second_account is not None
        users_dir = data_dir / "users"
        assert not (data_dir / "applications").exists()
        assert not (data_dir / "profile").exists()
        assert {item.name for item in users_dir.iterdir()} == {first_account.id, second_account.id}
        assert (users_dir / first_account.id / "applications").exists()
        assert (users_dir / first_account.id / "profile" / "resume.md").exists()
        assert not list((users_dir / second_account.id / "applications").iterdir())


def test_portal_profile_status_is_session_scoped_and_analysis_requires_resume(
    tmp_path: Path, master_text: str
) -> None:
    app = create_app(_portal_settings(tmp_path, master_text))
    with TestClient(app) as first, TestClient(app) as second:
        first_headers = _register(first, "profile-first@example.com")
        second_headers = _register(second, "profile-second@example.com")
        assert first.get("/api/profile/status").json()["uploaded_resume"] is False
        assert second.get("/api/profile/status").json()["uploaded_resume"] is False

        blocked = second.post(
            "/api/analyze",
            json={"text": "Python engineer needed with cloud experience. " * 5},
            headers=second_headers,
        )
        assert blocked.status_code == 422
        assert "Upload a resume" in blocked.json()["detail"]

        uploaded = first.post(
            "/api/profile/resume",
            json={"text": master_text, "filename": "resume.md"},
            headers=first_headers,
        )
        assert uploaded.status_code == 200
        assert uploaded.json()["status"]["uploaded_resume"] is True
        assert first.get("/api/profile/status").json()["uploaded_resume"] is True
        assert second.get("/api/profile/status").json()["uploaded_resume"] is False

        analyzed = first.post(
            "/api/analyze",
            json={"text": "Python engineer needed with cloud experience. " * 5},
            headers=first_headers,
        )
        assert analyzed.status_code == 200
        application_id = analyzed.json()["application"]["id"]
        status = ""
        for _ in range(200):
            status = first.get(f"/api/applications/{application_id}/status").json()["application"][
                "status"
            ]
            if status in {"analyzed", "error", "needs_paste"}:
                break
            time.sleep(0.02)
        assert status == "analyzed"


def test_portal_requires_csrf_for_unsafe_requests(tmp_path: Path, master_text: str) -> None:
    app = create_app(_portal_settings(tmp_path, master_text))
    with TestClient(app) as client:
        headers = _register(client, "csrf@example.com")
        without = client.post("/api/analyze", json={})
        assert without.status_code == 403
        assert without.json()["code"] == "forbidden"
        with_token = client.post("/api/analyze", json={}, headers=headers)
        assert with_token.status_code == 422


def test_portal_registration_validation_has_clear_messages(
    tmp_path: Path, master_text: str
) -> None:
    app = create_app(_portal_settings(tmp_path, master_text))
    with TestClient(app) as client:
        bad_email = client.post(
            "/api/auth/register", json={"email": "ab", "password": "long-password"}
        )
        assert bad_email.status_code == 422
        assert any(
            error["msg"] == "Enter a valid email address."
            for error in bad_email.json()["detail"]["errors"]
        )

        bad_password = client.post(
            "/api/auth/register", json={"email": "user@example.com", "password": "short"}
        )
        assert bad_password.status_code == 422
        assert any(
            error["msg"] == "Use a password of at least 8 characters."
            for error in bad_password.json()["detail"]["errors"]
        )


def test_extract_contacts_from_metadata_and_links() -> None:
    contacts = extract_contacts(
        "Contact: hiring@example.com or careers@example.com",
        job_posting={"applicationContact": {"email": "jobs@example.com", "name": "Hiring Team"}},
    )
    emails = [item.email for item in contacts]
    assert emails[0] == "jobs@example.com"
    assert contacts[0].name == "Hiring Team"
    assert "hiring@example.com" in emails
    assert "careers@example.com" in emails


def test_portal_outreach_tracker_and_prefill_endpoints(tmp_path: Path, master_text: str) -> None:
    app = create_app(_portal_settings(tmp_path, master_text))
    with TestClient(app) as client:
        headers = _register(client, "workflow@example.com")
        uploaded = client.post(
            "/api/profile/resume",
            json={"text": master_text, "filename": "resume.md"},
            headers=headers,
        )
        assert uploaded.status_code == 200

        session_id = str(client.cookies.get("jobassist_session"))
        workspace = app.state.sessions.get(session_id).workspace
        record = workspace._new_record("Acme", "Platform Engineer", "")
        record.status = "analyzed"
        record.stage = "done"
        record.contacts = [
            ContactCandidate(
                email="recruiter@example.com",
                name="Recruiter",
                source="Job posting metadata",
                confidence=0.95,
            )
        ]
        workspace.store.save(record)
        asyncio.run(workspace.profiles.load())
        workspace._write(
            record.id,
            "profile.json",
            workspace.profiles.profile_path.read_text(encoding="utf-8"),
        )
        workspace._write(
            record.id,
            "job.json",
            JobDescription(title="Platform Engineer", company="Acme").model_dump_json(),
        )
        workspace._write(
            record.id,
            "analysis.json",
            AnalysisResult(
                job=JobDescription(title="Platform Engineer", company="Acme"),
                matched_skills=["Python", "Kubernetes"],
            ).model_dump_json(),
        )
        workspace._write(record.id, "resume.md", "# Tailored resume")

        contact = client.post(
            f"/api/applications/{record.id}/contacts",
            json={"email": "hiring@example.com", "name": "Hiring Manager"},
            headers=headers,
        )
        assert contact.status_code == 200
        assert len(contact.json()["application"]["contacts"]) == 2

        outreach = client.post(
            f"/api/applications/{record.id}/outreach",
            json={"contact_email": "hiring@example.com", "user_note": "I built similar systems."},
            headers=headers,
        )
        assert outreach.status_code == 200
        draft = outreach.json()["draft"]
        assert draft["to"] == "hiring@example.com"
        assert "I built similar systems." in draft["body"]
        assert outreach.json()["mailto"].startswith("mailto:")

        tracker = client.patch(
            f"/api/applications/{record.id}/tracker",
            json={"status": "submitted"},
            headers=headers,
        )
        assert tracker.status_code == 200
        assert tracker.json()["application"]["tracker_status"] == "submitted"

        prefill = client.post(f"/api/applications/{record.id}/prefill", json={}, headers=headers)
        assert prefill.status_code == 200
        assert prefill.json()["fields"]["email"] == "test@example.com"
        assert prefill.json()["fields"]["skills"] == ["Python", "Kubernetes"]
        assert prefill.json()["resume_markdown"] == "# Tailored resume"


def test_portal_workspace_survives_restart(tmp_path: Path, master_text: str) -> None:
    settings = _portal_settings(tmp_path, master_text)
    original = create_app(settings)
    with TestClient(original) as client:
        headers = _register(client, "durable@example.com")
        uploaded = client.post(
            "/api/profile/resume",
            json={"text": master_text, "filename": "resume.md"},
            headers=headers,
        )
        assert uploaded.status_code == 200
        analyzed = client.post(
            "/api/analyze",
            json={
                "text": "Python engineer needed with cloud experience. " * 5,
                "company": "Acme",
                "role": "Engineer",
            },
            headers=headers,
        )
        assert analyzed.status_code == 200
        application_id = str(analyzed.json()["application"]["id"])
        assert (
            _wait_for_status(client, application_id, headers, {"analyzed", "error"}) == "analyzed"
        )
        generated = client.post(
            f"/api/applications/{application_id}/generate", json={}, headers=headers
        )
        assert generated.status_code == 200
        assert (
            _wait_for_status(client, application_id, headers, {"generated", "error"}) == "generated"
        )

    restarted = create_app(settings)
    with TestClient(restarted) as client:
        headers = _login(client, "durable@example.com")
        history = client.get("/api/applications", headers=headers).json()
        assert any(item["id"] == application_id for item in history["applications"])
        detail = client.get(f"/api/applications/{application_id}", headers=headers)
        assert detail.status_code == 200
        saved = client.put(
            f"/api/applications/{application_id}/resume",
            json=detail.json()["resume"],
            headers=headers,
        )
        assert saved.status_code == 200
        assert saved.json()["metadata"]["method"] == "manual-edit"


def test_portal_restart_marks_interrupted_runs(tmp_path: Path, master_text: str) -> None:
    settings = _portal_settings(tmp_path, master_text)
    account = AccountStore(settings.accounts_path).create("interrupt@example.com", "long-password")
    assert account is not None
    first = build_account_workspace(settings, account.id)
    record = first._new_record("Acme", "Platform Engineer", "")
    record.status = "generating"
    record.stage = "tailoring"
    first.store.save(record)

    recovered = build_account_workspace(settings, account.id)
    updated = recovered.get(record.id)
    assert updated is not None
    assert updated.status == "error"
    assert updated.error == "interrupted"
