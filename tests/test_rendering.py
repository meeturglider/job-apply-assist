from __future__ import annotations

from pathlib import Path

from docx import Document as DocxDocument

from app.matching.scorer import analyze_match
from app.models import (
    CandidateProfile,
    JobDescription,
    Requirement,
    ResumeExperience,
    SkillGroup,
    TailoredResume,
)
from app.rendering.ats import check_docx
from app.rendering.docx import render_docx
from app.rendering.html import resume_html
from app.rendering.markdown import resume_markdown
from app.tailoring.selection import deterministic_education, deterministic_summary, header_for


def _resume(profile: CandidateProfile) -> TailoredResume:
    job = JobDescription(must_have=[Requirement(skill="Kubernetes")])
    return TailoredResume(
        header=header_for(profile, job),
        summary=deterministic_summary(profile, job, analyze_match(job, profile)),
        skills=[SkillGroup(category="Cloud", items=["Kubernetes", "Terraform"])],
        experience=[
            ResumeExperience(
                company="Example Corp",
                role="Senior Platform Engineer",
                start="Jan 2020",
                end="Present",
                bullets=["Built GCP landing zones with Terraform and Kubernetes."],
            )
        ],
        education=deterministic_education(profile),
    )


def test_renders_markdown_html_and_docx(profile: CandidateProfile, tmp_path: Path) -> None:
    resume = _resume(profile)
    markdown = resume_markdown(resume)
    html = resume_html(resume)
    target = tmp_path / "resume.docx"
    render_docx(resume, target)
    assert "# Test Candidate" in markdown
    assert "Kubernetes" in markdown
    assert "<html" in html.casefold()
    assert "single" not in html.casefold() or "table" not in html.casefold()
    assert '<a href="mailto:test@example.com">test@example.com</a>' in html
    assert "&lt;span" not in html
    assert 'class="contact"' in html
    assert "size: A4" in html
    assert "page-break-inside: auto" in html
    assert target.exists() and target.stat().st_size > 1000
    document = DocxDocument(str(target))
    assert round(document.sections[0].page_width.mm) == 210
    assert round(document.sections[0].page_height.mm) == 297
    checks = check_docx(resume, target)
    assert checks
    assert all(check.passed for check in checks)


def test_renders_extractable_role_and_contact_separators(profile: CandidateProfile) -> None:
    html = resume_html(_resume(profile))
    assert '<span class="inline-sep"> | </span>' in html
    assert 'content: " | "' in html
