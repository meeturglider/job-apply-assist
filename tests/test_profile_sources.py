from __future__ import annotations

import asyncio
from pathlib import Path

import httpx
import respx
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.profile.service import ProfileService
from app.service import ApplicationService

PUBLIC = "93.184.216.34"
JD = """
Senior Platform Engineer with strong Google Cloud Platform, Kubernetes, Terraform,
Jenkins, CI/CD, Linux, Python, and cloud security experience. Lead engineers and build
reliable platforms for large teams.
"""

UPLOADED_MASTER = """# Uploaded Candidate
## Platform Engineer
- upload@example.com

# Professional Summary

Cloud engineer focused on reliable platforms.

# Core Skills

## Cloud
- Kubernetes
- Google Cloud Platform

# Professional Experience

## New Co

### Platform Engineer
Jan 2022 – Present | Remote
- Built Kubernetes platforms on Google Cloud.

# Education

## B.Tech in Computer Science
Example University
"""


def _settings(tmp_path: Path, master_text: str) -> Settings:
    master = tmp_path / "MASTER_RESUME.md"
    master.write_text(master_text, encoding="utf-8")
    return Settings(
        llm_provider="none",
        master_resume_path=master,
        portfolio_base_url="http://127.0.0.1:9",
        data_dir=tmp_path / "data",
        ollama_timeout_seconds=1,
        shared_password="",
    )


def test_resume_upload_and_reset_api(tmp_path: Path, master_text: str) -> None:
    app = create_app(_settings(tmp_path, master_text))
    with TestClient(app) as client:
        uploaded = client.post(
            "/api/profile/resume",
            json={"text": UPLOADED_MASTER, "filename": "latest.md"},
        )
        assert uploaded.status_code == 200
        assert uploaded.json()["identity"]["name"] == "Uploaded Candidate"
        assert uploaded.json()["status"]["uploaded_resume"] is True
        assert uploaded.json()["status"]["master_resume"].endswith("resume.md")

        default = client.post("/api/profile/resume/reset", json={})
        assert default.status_code == 200
        assert default.json()["identity"]["name"] == "Test Candidate"
        assert default.json()["status"]["uploaded_resume"] is False

        invalid = client.post("/api/profile/resume", json={"text": "not a resume"})
        assert invalid.status_code == 422


async def test_uploaded_resume_is_used_for_analysis_and_generation(
    tmp_path: Path, master_text: str
) -> None:
    settings = _settings(tmp_path, master_text)
    profiles = ProfileService(settings)
    profiles.upload_resume(UPLOADED_MASTER, "latest.md")
    service = ApplicationService(settings)
    try:
        record = service.start_analysis(text=JD, company="Acme", role="Platform Engineer")
        assert await _wait(service, record.id, {"analyzed"}) == "analyzed"
        profile = service.analysis_payload(record.id)["profile"]
        assert profile["identity"]["name"] == "Uploaded Candidate"
        assert profile["provenance"]["resume_kind"] == "uploaded"
        assert profile["provenance"]["evidence_counts"].get("uploaded_resume", 0) > 0
        analysis = service.analysis_payload(record.id)["analysis"]
        assert analysis["provenance"]["resume_kind"] == "uploaded"

        service.start_generation(record.id)
        assert await _wait(service, record.id, {"generated"}) == "generated"
        markdown = service.analysis_payload(record.id)["resume_markdown"]
        assert "Uploaded Candidate" in markdown
        assert "New Co" in markdown
    finally:
        await service.shutdown()


async def test_additional_profile_url_is_merged_for_analysis(
    tmp_path: Path, master_text: str
) -> None:
    settings = _settings(tmp_path, master_text)
    service = ApplicationService(settings)
    url = f"https://{PUBLIC}/portfolio.json"
    payload = [
        {
            "year": "Jan 2015 - Present",
            "company": "Extra Corp",
            "role": "Security Engineer",
            "highlights": ["Automated operations with Ansible."],
            "technologies": ["Ansible"],
        }
    ]
    try:
        with respx.mock(assert_all_called=False) as mock:
            mock.get(url).mock(
                return_value=httpx.Response(
                    200,
                    headers={"content-type": "application/json"},
                    json=payload,
                )
            )
            mock.get(f"https://{PUBLIC}/data/experience.json").mock(
                return_value=httpx.Response(404)
            )
            mock.get(f"https://{PUBLIC}/data/projects.json").mock(return_value=httpx.Response(404))
            record = service.start_analysis(
                text=JD,
                company="Acme",
                role="Platform Engineer",
                profile_urls=[url],
            )
            assert await _wait(service, record.id, {"analyzed"}) == "analyzed"

        assert record.profile_urls == [url]
        payload_after = service.analysis_payload(record.id)
        profile = payload_after["profile"]
        assert any(entry["company"] == "Extra Corp" for entry in profile["experience"])
        assert "Ansible" in profile["skills"]
        assert profile["provenance"]["extra_urls"] == [url]
        assert profile["provenance"]["evidence_counts"].get("additional_url", 0) > 0

        service.start_generation(record.id)
        assert await _wait(service, record.id, {"generated"}) == "generated"
        markdown = service.analysis_payload(record.id)["resume_markdown"]
        assert "Extra Corp" in markdown
    finally:
        await service.shutdown()


def test_invalid_profile_url_is_rejected(tmp_path: Path, master_text: str) -> None:
    app = create_app(_settings(tmp_path, master_text))
    with TestClient(app) as client:
        response = client.post(
            "/api/analyze",
            json={"text": JD, "profile_urls": ["http://127.0.0.1/admin"]},
        )
    assert response.status_code == 422
    assert response.json()["code"] == "validation_error"


async def _wait(
    service: ApplicationService,
    application_id: str,
    terminal: set[str],
) -> str:
    for _ in range(600):
        record = service.get(application_id)
        assert record is not None
        if record.status in terminal:
            return record.status
        if record.status == "error":
            raise AssertionError(record.message)
        await asyncio.sleep(0.05)
    raise AssertionError("Timed out waiting for background task.")
