from __future__ import annotations

import asyncio
from pathlib import Path

from app.llm import LLMError
from app.matching.scorer import analyze_match
from app.models import (
    CandidateProfile,
    JobDescription,
    Requirement,
    ResumeExperience,
    ResumeHeader,
    ResumeRole,
    SkillGroup,
    TailoredResume,
)
from app.rendering.html import resume_html
from app.rendering.markdown import resume_markdown
from app.tailoring.builder import build_resume
from app.tailoring.selection import (
    deterministic_projects,
    deterministic_skills,
    deterministic_summary,
    header_for,
    merge_experience_entries,
)
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


def test_tailoring_prompt_locks_google_style() -> None:
    prompt = (Path(__file__).parents[1] / "prompts/resume_tailor.md").read_text(encoding="utf-8")
    assert "X, Y, Z" in prompt
    assert "Never invent" in prompt
    assert "simple single-column structure" in prompt
    assert "without inventing one" in prompt
    assert "Merge multiple roles at the same company" in prompt
    assert "Preserve the skill group names" in prompt
    assert "Keep every certification" in prompt


def test_same_company_roles_merge_into_one_rendered_entry(profile: CandidateProfile) -> None:
    newer = ResumeExperience(
        company="Example Corp",
        role="Senior Platform Engineer",
        start="Jan 2020",
        end="Present",
        bullets=["Built GCP landing zones with Terraform and Kubernetes."],
        technologies=["Kubernetes"],
        roles=[ResumeRole(role="Senior Platform Engineer", start="Jan 2020", end="Present")],
    )
    older = ResumeExperience(
        company="Example Corp",
        role="Systems Engineer",
        start="Jan 2018",
        end="Dec 2019",
        bullets=["Managed Linux servers and cloud networking."],
        technologies=["Linux"],
    )
    merged = merge_experience_entries([newer, older])

    assert len(merged) == 1
    assert [role.role for role in merged[0].roles] == [
        "Senior Platform Engineer",
        "Systems Engineer",
    ]
    assert merged[0].start == "Jan 2018"
    assert merged[0].end == "Present"
    assert len(merged[0].bullets) == 2

    merged_profile = profile.model_copy(deep=True)
    merged_profile.experience[1].company = "Example Corp"
    resume = _resume(merged_profile)
    resume.summary = merged_profile.summary
    resume.skills[0].items = ["Kubernetes", "Terraform", "Python"]
    resume.experience = merged
    report = validate_resume(resume, merged_profile, _job())

    assert report.valid is True, report.issues
    html = resume_html(resume)
    markdown = resume_markdown(resume)
    assert html.count("Example Corp") == 1
    assert "Senior Platform Engineer" in html
    assert "Systems Engineer" in html
    assert "### Example Corp" in markdown


def test_validator_warns_when_recent_bullets_lack_metrics(profile: CandidateProfile) -> None:
    resume = _resume(profile)
    resume.skills[0].items = ["Kubernetes", "Terraform"]
    resume.experience[0].bullets = [
        "Built GCP landing zones with Terraform and Kubernetes.",
        "Automated CI/CD pipelines with Jenkins and Python.",
    ]
    report = validate_resume(resume, profile, _job())
    assert report.valid is True
    assert any(
        issue.severity == "warning" and "No quantified impact" in issue.message
        for issue in report.issues
    )


def test_sanitizer_removes_unsupported_claims(profile: CandidateProfile) -> None:
    job = _job()
    analysis = analyze_match(job, profile)
    cleaned = sanitize_resume(_resume(profile), profile, job, analysis)
    report = validate_resume(cleaned, profile, job)
    assert report.valid is True
    assert all("COBOL" not in item for group in cleaned.skills for item in group.items)
    assert all("99%" not in bullet for entry in cleaned.experience for bullet in entry.bullets)


def test_validator_requires_review_for_untraceable_supported_bullet(
    profile: CandidateProfile,
) -> None:
    job = _job()
    resume = _resume(profile)
    resume.summary = "Platform engineer focused on reliable cloud platforms."
    resume.skills[0].items = ["Kubernetes", "Terraform"]
    resume.experience[0].bullets = ["Kubernetes supports this system."]
    report = validate_resume(resume, profile, job)
    assert report.valid is True
    assert report.review_required is True
    assert report.review_items[0].claim == "Kubernetes supports this system."
    assert report.review_items[0].claim_type == "traceability"


def test_sanitizer_removes_unsupported_leadership_and_financial_claims(
    profile: CandidateProfile,
) -> None:
    job = _job()
    analysis = analyze_match(job, profile)
    resume = _resume(profile)
    resume.summary = "Platform engineer with 20 years of experience."
    resume.skills[0].items = ["Kubernetes", "Terraform"]
    resume.experience[0].bullets = [
        "Built GCP landing zones with Terraform and Kubernetes.",
        "Led a team of 20 and generated $50M revenue.",
    ]
    cleaned = sanitize_resume(resume, profile, job, analysis)
    bullets = [bullet for entry in cleaned.experience for bullet in entry.bullets]
    assert "20 years" not in cleaned.summary
    assert all("team of 20" not in bullet for bullet in bullets)
    assert all("$50M" not in bullet for bullet in bullets)
    assert validate_resume(cleaned, profile, job).valid is True


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
    assert report.fallback_reason.startswith("LLM tailoring failed")
    assert resume.header.name == profile.identity.name


def test_deterministic_selection_stays_grounded(profile: CandidateProfile) -> None:
    job = _job()
    skills = deterministic_skills(profile, job)
    projects = deterministic_projects(profile, job)
    items = [item for group in skills for item in group.items]
    assert "Kubernetes" in items
    assert "Terraform" in items
    assert all(project.name for project in projects)


def test_deterministic_summary_uses_rounded_experience(profile: CandidateProfile) -> None:
    candidate = profile.model_copy(update={"experience_years": 15.8})
    job = _job()
    summary = deterministic_summary(candidate, job, analyze_match(job, candidate))
    assert "16+ years" in summary


def test_title_and_summary_do_not_add_unsupported_jd_data(profile: CandidateProfile) -> None:
    job = JobDescription(
        title="Director of Sales",
        must_have=[Requirement(skill="COBOL"), Requirement(skill="Fortran")],
    )
    analysis = analyze_match(job, profile)
    summary = deterministic_summary(profile, job, analysis)
    assert header_for(profile, job).title == "Senior Platform Engineer"
    assert "Director of Sales" not in summary
    assert "COBOL" not in summary
    assert "Fortran" not in summary
