from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from app.config import Settings
from app.models import AnalysisNote
from app.profile.master_resume import parse_master_resume_text
from app.profile.notes import apply_notes
from app.service import ApplicationService

JD = """
Senior Platform Engineer
We need Ansible, Kubernetes, Terraform, CI/CD, Python, and Linux experience.
Automate configuration and deploys with Ansible across platform teams.
Build reliable delivery pipelines and cloud infrastructure for engineering teams.
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


@pytest.mark.asyncio
async def test_notes_revalidate_and_gate_generation(tmp_path: Path, master_text: str) -> None:
    service = ApplicationService(_settings(tmp_path, master_text))
    try:
        record = service.start_analysis(text=JD, company="Acme", role="Platform Engineer")
        assert await _wait(service, record.id, {"analyzed"}) == "analyzed"
        before = service.analysis_payload(record.id)
        assert before["analysis"]["keyword_coverage"].get("Ansible") is False

        updated = service.save_notes(
            record.id,
            [
                {
                    "target": "Ansible",
                    "text": "I used Ansible to automate configuration and application rollouts.",
                }
            ],
        )
        assert updated.notes_revision != updated.notes_validated_revision
        with pytest.raises(ValueError, match="Revalidate"):
            service.start_generation(record.id)

        service.start_revalidation(record.id)
        assert await _wait(service, record.id, {"analyzed"}) == "analyzed"
        after = service.analysis_payload(record.id)
        assert after["analysis"]["keyword_coverage"].get("Ansible") is True
        assert after["record"]["notes"][0]["status"] == "accepted"
        assert after["record"]["notes_revision"] == after["record"]["notes_validated_revision"]
        assert after["analysis"]["provenance"]["evidence_counts"].get("user_note", 0) >= 1
        assert any(
            "User note" in source
            for source in after["analysis"]["keyword_sources"].get("Ansible", [])
        )

        service.start_generation(record.id)
        assert await _wait(service, record.id, {"generated"}) == "generated"
        metadata = service.analysis_payload(record.id)["metadata"]
        assert metadata["evidence_sources"]["evidence_counts"].get("user_note", 0) >= 1
    finally:
        await service.shutdown()


def test_notes_are_grounded_and_application_local(master_text: str) -> None:
    profile = parse_master_resume_text(master_text, source="latest.md")
    accepted, decisions = apply_notes(
        profile,
        [
            AnalysisNote(
                target="Ansible",
                text="I used Ansible to automate configuration for platform teams.",
            ),
            AnalysisNote(target="Ansible", text="I have broad platform experience."),
        ],
    )
    assert "Ansible" in accepted.skills
    assert "Ansible" not in profile.skills
    assert decisions[0].status == "accepted"
    assert decisions[1].status == "rejected"
    assert "must mention" in decisions[1].reason


async def _wait(service: ApplicationService, application_id: str, terminal: set[str]) -> str:
    for _ in range(600):
        record = service.get(application_id)
        assert record is not None
        if record.status in terminal:
            return record.status
        await asyncio.sleep(0.05)
    raise AssertionError("Timed out waiting for background task.")
