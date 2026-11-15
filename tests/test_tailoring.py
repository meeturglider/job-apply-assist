from __future__ import annotations

import asyncio

from app.llm import LLMError
from app.matching.scorer import analyze_match
from app.models import (
    CandidateProfile,
    JobDescription,
    Requirement,
    ResumeExperience,
    ResumeHeader,
    SkillGroup,
    TailoredResume,
)
from app.tailoring.builder import build_resume
from app.tailoring.selection import deterministic_projects, deterministic_skills
from app.tailoring.validator import sanitize_resume, validate_resume


def _job() -> JobDescription:
    return JobDescription(
        title="Senior Platform Engineer",
        must_have=[
            Requirement(skill="Kubernetes"),
            Requirement(skill="Terraform"),
            Requirement(skill="Python"),
        ],
    )


def _resume(profile: CandidateProfile) -> TailoredResume:
    return TailoredResume(
        header=ResumeHeader(name=profile.identity.name, title="Senior Platform Engineer"),
        summary="Platform engineer with 8+ years of experience.",
        skills=[SkillGroup(category="Cloud", items=["Kubernetes", "Terraform", "COBOL"])],
        experience=[
            ResumeExperience(
                company="Example Corp",
                role="Senior Platform Engineer",
                start="Jan 2020",
                end="Present",
                bullets=[
                    "Built GCP landing zones with Terraform and Kubernetes.",
                    "Delivered projects worth 99% uptime.",
                ],
            )
        ],
    )


def test_validator_flags_unsupported_skills_and_numbers(profile: CandidateProfile) -> None:
    report = validate_resume(_resume(profile), profile, _job())
    messages = [issue.message for issue in report.issues]
    assert report.valid is False
    assert any("COBOL" in message for message in messages)
    assert any("99%" in message for message in messages)


def test_sanitizer_removes_unsupported_claims(profile: CandidateProfile) -> None:
    job = _job()
    analysis = analyze_match(job, profile)
    cleaned = sanitize_resume(_resume(profile), profile, job, analysis)
    report = validate_resume(cleaned, profile, job)
    assert report.valid is True
    assert all("COBOL" not in item for group in cleaned.skills for item in group.items)
    assert all("99%" not in bullet for entry in cleaned.experience for bullet in entry.bullets)


def test_sanitizer_caps_llm_bullet_volume(profile: CandidateProfile) -> None:
    job = _job()
    analysis = analyze_match(job, profile)
    resume = _resume(profile)
    resume.experience[0].bullets = [
        "Built GCP landing zones with Terraform and Kubernetes.",
        "Automated CI/CD pipelines with Jenkins and Python.",
    ] * 5
    cleaned = sanitize_resume(resume, profile, job, analysis)
    assert len(cleaned.experience[0].bullets) <= 5


def test_builder_falls_back_when_llm_fails(profile: CandidateProfile) -> None:
    class FailingLLM:
        enabled = True

        async def json(self, *args: object, **kwargs: object) -> dict[str, object]:
            raise LLMError("timeout")

    job = _job()
    analysis = analyze_match(job, profile)
    resume, report, method = asyncio.run(build_resume(profile, job, analysis, FailingLLM()))  # type: ignore[arg-type]
    assert method == "deterministic-fallback"
    assert report.fallback_used is True
    assert resume.header.name == profile.identity.name


def test_deterministic_selection_stays_grounded(profile: CandidateProfile) -> None:
    job = _job()
    skills = deterministic_skills(profile, job)
    projects = deterministic_projects(profile, job)
    items = [item for group in skills for item in group.items]
    assert "Kubernetes" in items
    assert "Terraform" in items
    assert all(project.name for project in projects)
