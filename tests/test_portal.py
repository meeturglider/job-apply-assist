from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from app.config import Settings
from app.jd.contacts import extract_contacts
from app.main import create_app
from app.models import AnalysisResult, ContactCandidate, JobDescription


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


def test_portal_sessions_are_isolated_and_do_not_write_candidate_data(
    tmp_path: Path, master_text: str
) -> None:
    app = create_app(_portal_settings(tmp_path, master_text))
    with TestClient(app) as first, TestClient(app) as second:
        first_headers = _register(first, "first@example.com")
        second_headers = _register(second, "second@example.com")
        assert first_headers != second_headers
        assert first.get("/api/auth/me").json()["account"]["email"] == "first@example.com"
        assert second.get("/api/auth/me").json()["account"]["email"] == "second@example.com"

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
        assert not (data_dir / "applications").exists()
        assert not (data_dir / "profile").exists()
        assert sorted(item.name for item in data_dir.iterdir()) == ["accounts.db"]


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
        workspace._write(
            record.id,
            "profile.json",
            workspace.profiles.profile.model_dump_json(),
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
