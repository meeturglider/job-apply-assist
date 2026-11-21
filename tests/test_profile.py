from __future__ import annotations

from pathlib import Path

from app.models import CandidateProfile


def test_parses_identity_experience_and_skills(profile: CandidateProfile) -> None:
    assert profile.identity.name == "Test Candidate"
    assert profile.identity.email == "test@example.com"
    assert profile.identity.headline == "Senior Platform Engineer"
    assert len(profile.experience) == 2
    assert profile.experience[0].company == "Example Corp"
    assert profile.experience[0].role == "Senior Platform Engineer"
    assert profile.experience[0].current is True
    assert profile.experience_years >= 7
    assert "Google Cloud Platform" in profile.skills
    assert "Kubernetes" in profile.skills
    assert profile.certifications[0].name == "Google Cloud Professional Cloud Architect"
    assert profile.education[0].degree == "B.Tech in Computer Science"


def test_duration_warning_is_not_raised_for_consistent_text(master_text: str) -> None:
    from app.profile.master_resume import parse_master_resume_text

    text = master_text.replace("Jan 2018", "Jan 2010").replace(
        "Platform engineer with", "16+ years platform engineer with"
    )
    profile = parse_master_resume_text(text)
    assert int(profile.experience_years) == 16
    assert not any("hardcoded experience duration" in warning for warning in profile.warnings)


def test_duration_warning_is_raised_when_text_is_stale(master_text: str) -> None:
    from app.profile.master_resume import parse_master_resume_text

    text = master_text.replace("Platform engineer with", "16+ years platform engineer with")
    profile = parse_master_resume_text(text)
    assert any("hardcoded experience duration" in warning for warning in profile.warnings)


def test_parses_profile_links_and_project_status() -> None:
    from app.profile.master_resume import parse_master_resume_text

    text = """# Test Candidate
## Senior Cloud Architect
- test@example.com
- https://www.linkedin.com/in/test | https://github.com/test | https://example.dev

# Professional Experience

## Example Corp

### Senior Cloud Architect
Jan 2024 – Present | Remote
- Led architecture delivery.

# Selected Projects

## CORE — Assessment Platform

Status: Production
Period: 2025 – Present
Context: Example Corp

- Led delivery of CORE.

### Technologies

* Python
"""
    profile = parse_master_resume_text(text)
    assert profile.identity.github == "https://github.com/test"
    assert profile.identity.portfolio == "https://example.dev"
    assert profile.projects[0].name == "CORE — Assessment Platform"
    assert profile.projects[0].status == "Production"
    assert profile.projects[0].period == "2025 – Present"
    assert profile.projects[0].context == "Example Corp"
    assert profile.projects[0].technologies == ["Python"]


def test_portfolio_can_be_disabled(tmp_path: Path) -> None:
    import asyncio

    from app.config import Settings
    from app.profile.memory_service import MemoryProfileService

    assert Settings().portfolio_enabled is True
    service = MemoryProfileService(
        Settings(
            portfolio_enabled=False,
            data_dir=tmp_path,
            master_resume_path=tmp_path / "resume.md",
        )
    )
    assert asyncio.run(service._load_portfolio(refresh=True)) is None

    from app.profile.merge import _merge_awards

    merged = _merge_awards(
        ["Best Performer – Quarterly Award – Cisco Systems"],
        ["Best Performer — Quarterly, Cisco Systems", "Best Performer – Annual – Cisco Systems"],
    )
    assert merged == [
        "Best Performer – Quarterly Award – Cisco Systems",
        "Best Performer – Annual – Cisco Systems",
    ]
