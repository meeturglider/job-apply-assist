from __future__ import annotations

from app.config import Settings
from app.jd.analyze import analyze_job
from app.llm import OllamaClient
from app.matching.dimensions import classify_dimension
from app.matching.matcher import keyword_presence, match_requirements
from app.matching.scorer import analyze_match
from app.models import CandidateProfile, Evidence, ExperienceEntry, JobDescription, Requirement


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


def test_keyword_presence_matches_parent_company_entity(profile: CandidateProfile) -> None:
    candidate = profile.model_copy(
        update={
            "experience": [
                ExperienceEntry(company="Google Operations Center", role="Operations Analyst"),
                *profile.experience,
            ]
        }
    )
    unrelated = profile.model_copy(
        update={
            "experience": [
                ExperienceEntry(company="Microsoft Operations Center", role="Operations Analyst"),
                *profile.experience,
            ]
        }
    )
    assert keyword_presence("Alphabet", candidate) is True
    assert keyword_presence("alphabet", candidate) is True
    assert keyword_presence("Alphabet", unrelated) is False


def test_entity_keyword_adds_grounded_profile_suggestion(profile: CandidateProfile) -> None:
    candidate = profile.model_copy(
        update={
            "experience": [
                ExperienceEntry(company="Google Operations Center", role="Operations Analyst"),
                *profile.experience,
            ]
        }
    )
    job = _job()
    job.keywords = {"alphabet": 5.0, "COBOL": 1.0}
    result = analyze_match(job, candidate)

    assert result.keyword_coverage["alphabet"] is True
    assert result.keyword_coverage["COBOL"] is False
    assert result.keyword_coverage_pct == 50.0
    assert len(result.keyword_suggestions) == 1
    suggestion = result.keyword_suggestions[0]
    assert suggestion.keyword == "Alphabet"
    assert suggestion.source == "Google Operations Center"
    assert "Alphabet" in suggestion.suggestion


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


def test_contradicting_evidence_is_distinguished_from_missing(
    profile: CandidateProfile,
) -> None:
    candidate = profile.model_copy(
        update={
            "skills": {
                **profile.skills,
                "COBOL": [
                    Evidence(
                        source="Resume",
                        text="No COBOL experience; this profile never used COBOL.",
                    )
                ],
            }
        }
    )
    result = analyze_match(_job("COBOL"), candidate)
    match = result.matches[0]
    assert match.status == "missing"
    assert match.evidence_class == "contradicted"
    assert match.evidence
    assert result.score_report.evidence.contradicted == 1


def test_experience_shortfall_is_contradicted(profile: CandidateProfile) -> None:
    candidate = profile.model_copy(update={"experience_years": 3.0})
    job = JobDescription(
        experience_requirements=[
            Requirement(skill="10+ years of technical experience", category="Experience")
        ]
    )
    match = analyze_match(job, candidate).matches[0]
    assert match.status == "missing"
    assert match.evidence_class == "contradicted"


def test_responsibility_matching_retains_source_and_explanation(
    profile: CandidateProfile,
) -> None:
    lead_bullet = "Led and mentored a team of 6 platform engineers."
    candidate = profile.model_copy(
        update={
            "experience": [
                profile.experience[0].model_copy(
                    update={"bullets": [lead_bullet, *profile.experience[0].bullets]}
                ),
                *profile.experience[1:],
            ]
        }
    )
    job = JobDescription(responsibilities=["Lead and mentor a team of platform engineers."])
    match = match_requirements(job, candidate)[0]
    assert match.category == "Responsibility"
    assert match.status == "matched"
    assert match.evidence_class == "direct"
    assert match.explanation
    assert match.evidence[0].source == candidate.experience[0].company
    assert match.evidence[0].text == lead_bullet


def test_dimension_classification_and_job_propagation() -> None:
    assert classify_dimension("Lead and mentor platform engineers") == "leadership"
    assert classify_dimension("Define the multi-cloud strategy and roadmap") == "strategic_impact"
    assert classify_dimension("Design scalable landing zones") == "architecture"
    assert classify_dimension("Deliver migrations end-to-end") == "delivery"
    assert classify_dimension("Healthcare domain compliance") == "domain"

    job = JobDescription(responsibilities=["Lead and mentor platform engineers"])
    match = match_requirements(job, CandidateProfile()).pop()
    assert match.category == "Responsibility"
    assert match.requirement == "Lead and mentor platform engineers"


async def test_analyze_job_builds_dimensioned_responsibilities() -> None:
    client = OllamaClient(Settings(llm_provider="none"))
    job = await analyze_job(
        "Lead and mentor platform engineers.\nDesign scalable landing zones.",
        client,
    )
    dimensions = {item.dimension for item in job.responsibility_requirements}
    assert {"leadership", "architecture"} <= dimensions
    assert all(item.category == "Responsibility" for item in job.responsibility_requirements)
