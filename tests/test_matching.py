from __future__ import annotations

from app.matching.matcher import keyword_presence, match_requirements
from app.matching.scorer import analyze_match
from app.models import CandidateProfile, JobDescription, Requirement


def _job(*skills: str) -> JobDescription:
    return JobDescription(
        title="Senior Platform Engineer",
        must_have=[
            Requirement(skill=skill, category="", importance="must_have") for skill in skills
        ],
    )


def test_matches_direct_alias_and_related_skills(profile: CandidateProfile) -> None:
    matches = match_requirements(_job("Kubernetes", "GCP", "Argo CD"), profile)
    statuses = {match.skill: match.status for match in matches}
    assert statuses["Kubernetes"] == "matched"
    assert statuses["GCP"] == "matched"
    assert statuses["Argo CD"] in {"partial", "matched"}


def test_reports_missing_requirement(profile: CandidateProfile) -> None:
    matches = match_requirements(_job("COBOL"), profile)
    assert matches[0].status == "missing"
    assert "No supporting evidence" in matches[0].note


def test_scoring_rewards_matches_and_penalizes_gaps(profile: CandidateProfile) -> None:
    strong = analyze_match(_job("Kubernetes", "Terraform", "Python"), profile)
    weak = analyze_match(_job("COBOL", "Fortran"), profile)
    assert strong.score.total > weak.score.total
    assert strong.matched_skills
    assert weak.missing_must_have == ["COBOL", "Fortran"]


def test_keyword_presence_uses_alias_evidence(profile: CandidateProfile) -> None:
    assert keyword_presence("GCP", profile) is True
    assert keyword_presence("COBOL", profile) is False
