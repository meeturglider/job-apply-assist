from __future__ import annotations

from app.config import Settings
from app.jd.analyze import analyze_job
from app.llm import OllamaClient
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


def test_related_engineering_degree_satisfies_cs_requirement(profile: CandidateProfile) -> None:
    candidate = profile.model_copy(
        update={
            "education": profile.education
            and [
                profile.education[0].model_copy(
                    update={"degree": "B.E. in Electronics & Communication"}
                )
            ]
        }
    )
    job = JobDescription(
        title="Cloud Architect",
        education_requirements=[
            Requirement(
                skill="Bachelor's degree in Computer Science or related field",
                category="Education",
            )
        ],
    )
    result = analyze_match(job, candidate)
    match = result.matches[0]
    assert match.status == "matched"
    assert "related-field" in match.note
    assert "Electronics & Communication" in match.evidence[0].text


def test_experience_years_are_matched_from_computed_duration(profile: CandidateProfile) -> None:
    candidate = profile.model_copy(update={"experience_years": 15.8})
    job = JobDescription(
        title="Cloud Architect",
        experience_requirements=[
            Requirement(skill="15+ years of technical experience", category="Experience"),
            Requirement(skill="16+ years of technical experience", category="Experience"),
        ],
    )
    result = analyze_match(job, candidate)
    assert [match.status for match in result.matches] == ["matched", "matched"]
    assert not result.missing_must_have


async def test_jd_extraction_separates_education_and_experience() -> None:
    client = OllamaClient(Settings(llm_provider="none"))
    job = await analyze_job(
        "Bachelor's degree in Computer Science or related field. "
        "15+ years of technical experience. Kubernetes and Terraform required.",
        client,
    )
    assert job.education_requirements
    assert job.experience_requirements
    assert all("Bachelor" not in item.skill for item in job.must_have)
    assert all("15+ years" not in item.skill for item in job.must_have)


async def test_llm_special_requirements_keep_full_text() -> None:
    class FakeLLM:
        enabled = True

        async def json(self, *args: object, **kwargs: object) -> dict[str, object]:
            return {
                "education_requirements": [
                    {
                        "skill": "Bachelor's degree in Computer Science or related field",
                        "category": "Education",
                    }
                ],
                "experience_requirements": [
                    {
                        "skill": "15+ years of technical experience with GCP and Kubernetes",
                        "category": "Experience",
                    }
                ],
                "must_have": [],
                "nice_to_have": [],
            }

    job = await analyze_job("Senior Cloud Architect", FakeLLM())  # type: ignore[arg-type]
    assert (
        job.education_requirements[0].skill
        == "Bachelor's degree in Computer Science or related field"
    )
    assert (
        job.experience_requirements[0].skill
        == "15+ years of technical experience with GCP and Kubernetes"
    )


def test_scoring_separates_suitability_evidence_and_keywords(
    profile: CandidateProfile,
) -> None:
    job = JobDescription(
        title="Platform Engineer",
        must_have=[
            Requirement(skill="Kubernetes", importance="must_have"),
            Requirement(skill="COBOL", importance="must_have"),
        ],
        keywords={"Kubernetes": 1.0, "COBOL": 1.0},
    )
    result = analyze_match(job, profile)
    report = result.score_report

    assert report.candidate_suitability.value < 100
    assert report.evidence_coverage.value == 50.0
    assert report.keyword_coverage.value == 50.0
    assert report.evidence.direct == 1
    assert report.evidence.unverified == 1
    assert report.evidence.contradicted == 0

    keyword_only_job = job.model_copy(
        update={"keywords": {"Kubernetes": 5.0, "COBOL": 5.0, "Fortran": 5.0}}
    )
    keyword_only = analyze_match(keyword_only_job, profile)
    assert (
        keyword_only.score_report.candidate_suitability.value == report.candidate_suitability.value
    )
    assert keyword_only.score_report.keyword_coverage.value != report.keyword_coverage.value


def test_unverified_is_not_counted_as_contradicted(profile: CandidateProfile) -> None:
    result = analyze_match(_job("COBOL"), profile)
    match = result.matches[0]
    assert match.status == "missing"
    assert match.evidence_class == "unverified"
    assert result.score_report.evidence.unverified == 1
    assert result.score_report.evidence.contradicted == 0


def test_legacy_score_remains_populated(profile: CandidateProfile) -> None:
    result = analyze_match(_job("Kubernetes"), profile)
    assert result.score.total == result.score_report.candidate_suitability.value
    assert result.score.must_have > 0
