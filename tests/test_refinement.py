from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from app.config import Settings
from app.matching.scorer import analyze_match
from app.models import JobDescription, Requirement
from app.profile.master_resume import parse_master_resume_text
from app.rendering.markdown import resume_markdown
from app.service import ApplicationService
from app.tailoring.selection import deterministic_resume
from app.tailoring.validator import sanitize_resume, validate_resume

JD = """
Senior Platform Engineer
We need a platform engineer with strong Google Cloud Platform, Kubernetes, Terraform,
Jenkins, CI/CD, Linux, and Python experience. Experience with IAM and cloud security helps.
"""


@pytest.mark.asyncio
async def test_refinement_end_to_end_produces_grounded_artifacts(
    tmp_path: Path, master_text: str
) -> None:
    master = tmp_path / "MASTER_RESUME.md"
    master.write_text(master_text, encoding="utf-8")
    settings = Settings(
        llm_provider="none",
        master_resume_path=master,
        portfolio_base_url="http://127.0.0.1:9",
        data_dir=tmp_path / "data",
        ollama_timeout_seconds=1,
    )
    service = ApplicationService(settings)
    try:
        record = service.start_analysis(
            text=JD,
            company="Acme",
            role="Senior Platform Engineer",
        )
        assert await _wait(service, record.id, {"analyzed"}) == "analyzed"
        service.start_generation(record.id)
        assert await _wait(service, record.id, {"generated"}) == "generated"

        generated = service.get(record.id)
        assert generated is not None
        payload = service.analysis_payload(record.id)
        analysis = payload["analysis"]
        report = analysis["score_report"]
        assert report["candidate_suitability"]["value"] == analysis["score"]["total"]
        assert report["evidence"]["total"] == len(analysis["matches"])
        assert {
            "candidate_suitability",
            "evidence_coverage",
            "keyword_coverage",
        } <= report.keys()

        validation = payload["validation"]
        assert validation["valid"] is True
        assert validation["review_required"] is False
        assert validation["checked_claims"] > 0
        assert validation["supported_claims"] == validation["checked_claims"]

        assert payload["metadata"]["method"] == "deterministic"
        assert validation["fallback_used"] is True
        assert "disabled" in validation["fallback_reason"]

        artifacts = {artifact.kind: artifact for artifact in generated.artifacts}
        assert {"markdown", "html", "docx"} <= artifacts.keys()
        assert all(artifact.size > 0 for artifact in artifacts.values())
        assert all("/" not in artifact.filename for artifact in artifacts.values())

        checks = {check["name"]: check["passed"] for check in payload["ats_report"]["checks"]}
        assert checks["DOCX text extraction"] is True
        assert checks["DOCX experience coverage"] is True
        if "pdf" in artifacts:
            assert checks["Selectable text"] is True
            assert checks["PDF experience coverage"] is True
    finally:
        await service.shutdown()


def test_awards_round_trip_and_unsupported_awards_are_rejected(master_text: str) -> None:
    text = master_text.replace(
        "# Education",
        "# Awards\n\n- Example Innovation Award 2024\n\n# Education",
    )
    profile = parse_master_resume_text(text, source="tests/test_refinement.py")
    assert profile.awards == ["Example Innovation Award 2024"]

    job = JobDescription(must_have=[Requirement(skill="Kubernetes")])
    analysis = analyze_match(job, profile)
    resume = deterministic_resume(profile, job, analysis)
    assert resume.awards == profile.awards
    assert "Example Innovation Award 2024" in resume_markdown(resume)
    assert validate_resume(resume, profile, job).valid is True

    resume.awards = ["Nobel Prize in Physics"]
    report = validate_resume(resume, profile, job)
    assert report.valid is False
    assert any(
        issue.message == "Unsupported award: Nobel Prize in Physics" for issue in report.issues
    )
    cleaned = sanitize_resume(resume, profile, job, analysis)
    assert cleaned.awards == profile.awards
    assert validate_resume(cleaned, profile, job).valid is True


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
