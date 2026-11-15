from __future__ import annotations

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
