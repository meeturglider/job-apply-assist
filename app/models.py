from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


def utc_now() -> datetime:
    return datetime.now(UTC)


EvidenceOrigin = Literal[
    "resume", "uploaded_resume", "portfolio", "additional_url", "user_note", "derived"
]


class Evidence(BaseModel):
    model_config = ConfigDict(extra="ignore")

    source: str
    text: str
    origin: EvidenceOrigin = "derived"
    origin_label: str = ""
    source_url: str = ""


class ProfileProvenance(BaseModel):
    model_config = ConfigDict(extra="ignore")

    resume_kind: str = "default"
    resume_name: str = ""
    portfolio_url: str = ""
    portfolio_cached: bool = False
    portfolio_fetched_at: str = ""
    extra_urls: list[str] = Field(default_factory=list)
    evidence_counts: dict[str, int] = Field(default_factory=dict)
    skill_counts: dict[str, int] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)


def origin_for_source(source: str, default: EvidenceOrigin = "derived") -> EvidenceOrigin:
    value = source.casefold()
    if "user note" in value:
        return "user_note"
    if "|" in source or "additional:" in value:
        return "additional_url"
    if "portfolio" in value:
        return "portfolio"
    if "master_resume" in value:
        return "resume"
    if value.endswith(".md") or value.endswith(".txt") or value.endswith(".pdf"):
        return "uploaded_resume"
    return default


class Identity(BaseModel):
    model_config = ConfigDict(extra="ignore")

    name: str = ""
    headline: str = ""
    location: str = ""
    email: str = ""
    phone: str = ""
    linkedin: str = ""
    github: str = ""
    portfolio: str = ""


class ExperienceEntry(BaseModel):
    model_config = ConfigDict(extra="ignore")

    company: str
    role: str
    location: str = ""
    start: str = ""
    end: str = ""
    current: bool = False
    summary: str = ""
    bullets: list[str] = Field(default_factory=list)
    technologies: list[str] = Field(default_factory=list)
    projects: list[dict[str, object]] = Field(default_factory=list)
    sources: list[str] = Field(default_factory=list)


class ProjectEntry(BaseModel):
    model_config = ConfigDict(extra="ignore")

    name: str
    context: str = ""
    period: str = ""
    status: str = ""
    summary: str = ""
    bullets: list[str] = Field(default_factory=list)
    technologies: list[str] = Field(default_factory=list)
    url: str = ""
    sources: list[str] = Field(default_factory=list)


class Certification(BaseModel):
    model_config = ConfigDict(extra="ignore")

    name: str
    issuer: str = ""
    year: str = ""


class EducationEntry(BaseModel):
    model_config = ConfigDict(extra="ignore")

    degree: str
    institution: str
    year: str = ""


class CandidateProfile(BaseModel):
    model_config = ConfigDict(extra="ignore")

    identity: Identity = Field(default_factory=Identity)
    summary: str = ""
    experience_years: float = 0
    experience: list[ExperienceEntry] = Field(default_factory=list)
    projects: list[ProjectEntry] = Field(default_factory=list)
    certifications: list[Certification] = Field(default_factory=list)
    education: list[EducationEntry] = Field(default_factory=list)
    awards: list[str] = Field(default_factory=list)
    skills: dict[str, list[Evidence]] = Field(default_factory=dict)
    skill_categories: dict[str, list[str]] = Field(default_factory=dict)
    sources: dict[str, str] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)
    provenance: ProfileProvenance = Field(default_factory=ProfileProvenance)
    generated_at: datetime = Field(default_factory=utc_now)

    def all_evidence(self) -> list[Evidence]:
        evidence: list[Evidence] = []
        for items in self.skills.values():
            evidence.extend(items)
        for exp in self.experience:
            origin = origin_for_source(exp.sources[0] if exp.sources else exp.company, "derived")
            if exp.company:
                evidence.append(Evidence(source=exp.company, text=exp.company, origin=origin))
            if exp.summary:
                evidence.append(Evidence(source=exp.company, text=exp.summary, origin=origin))
            evidence.extend(
                Evidence(source=exp.company, text=bullet, origin=origin) for bullet in exp.bullets
            )
        for project in self.projects:
            origin = origin_for_source(
                project.sources[0] if project.sources else project.name, "derived"
            )
            if project.summary:
                evidence.append(Evidence(source=project.name, text=project.summary, origin=origin))
            evidence.extend(
                Evidence(source=project.name, text=bullet, origin=origin)
                for bullet in project.bullets
            )
        evidence.extend(
            Evidence(source="certification", text=c.name, origin="derived")
            for c in self.certifications
        )
        evidence.extend(
            Evidence(source="award", text=award, origin="derived") for award in self.awards
        )
        return evidence


class Requirement(BaseModel):
    model_config = ConfigDict(extra="ignore")

    skill: str
    category: str = ""
    importance: Literal["must_have", "nice_to_have"] = "must_have"
    weight: float = 1.0
    context: str = ""
    dimension: str = "technical"


class ContactCandidate(BaseModel):
    model_config = ConfigDict(extra="ignore")

    email: str
    name: str = ""
    source: str
    confidence: float = 0
    confirmed: bool = False


class EmailDraft(BaseModel):
    model_config = ConfigDict(extra="ignore")

    to: str
    subject: str
    body: str
    generated_at: datetime = Field(default_factory=utc_now)
    source: str = "deterministic"


class PrefillFields(BaseModel):
    model_config = ConfigDict(extra="ignore")

    name: str = ""
    email: str = ""
    phone: str = ""
    linkedin: str = ""
    github: str = ""
    portfolio: str = ""
    role: str = ""
    company: str = ""
    location: str = ""
    experience_years: float = 0
    skills: list[str] = Field(default_factory=list)


class JobDescription(BaseModel):
    model_config = ConfigDict(extra="ignore")

    title: str = ""
    company: str = ""
    location: str = ""
    seniority: str = ""
    role_family: str = "other"
    summary: str = ""
    must_have: list[Requirement] = Field(default_factory=list)
    nice_to_have: list[Requirement] = Field(default_factory=list)
    education_requirements: list[Requirement] = Field(default_factory=list)
    experience_requirements: list[Requirement] = Field(default_factory=list)
    responsibility_requirements: list[Requirement] = Field(default_factory=list)
    responsibilities: list[str] = Field(default_factory=list)
    certifications: list[Requirement] = Field(default_factory=list)
    keywords: dict[str, float] = Field(default_factory=dict)
    raw_text: str = ""
    source_url: str = ""
    fetched_at: datetime = Field(default_factory=utc_now)
    extraction_method: str = "heuristic"
    extraction_stages: list[str] = Field(default_factory=list)
    ner_candidate_count: int = 0
    extraction_fallback_reason: str = ""
    llm_attempts: int = 0
    warnings: list[str] = Field(default_factory=list)
    contacts: list[ContactCandidate] = Field(default_factory=list)


class SkillMatch(BaseModel):
    model_config = ConfigDict(extra="ignore")

    skill: str
    category: str = ""
    importance: Literal["must_have", "nice_to_have"] = "must_have"
    status: Literal["matched", "partial", "missing"] = "missing"
    evidence_class: Literal["direct", "adjacent", "unverified", "contradicted"] = "unverified"
    evidence: list[Evidence] = Field(default_factory=list)
    confidence: float = 0.0
    note: str = ""
    requirement: str = ""
    explanation: str = ""
    evidence_origins: dict[str, int] = Field(default_factory=dict)


class ScoreBreakdown(BaseModel):
    model_config = ConfigDict(extra="ignore")

    must_have: float = 0
    nice_to_have: float = 0
    responsibilities: float = 0
    certifications: float = 0
    seniority: float = 0
    keywords: float = 0
    total: float = 0


class ScoreComponent(BaseModel):
    model_config = ConfigDict(extra="ignore")

    value: float = 0
    maximum: float = 0
    percentage: float = 0
    explanation: str = ""


class EvidenceCoverage(BaseModel):
    model_config = ConfigDict(extra="ignore")

    total: int = 0
    direct: int = 0
    adjacent: int = 0
    unverified: int = 0
    contradicted: int = 0
    coverage_pct: float = 0
    quality_pct: float = 0
    explanation: str = ""


class ScoreReport(BaseModel):
    model_config = ConfigDict(extra="ignore")

    candidate_suitability: ScoreComponent = Field(default_factory=ScoreComponent)
    evidence_coverage: ScoreComponent = Field(default_factory=ScoreComponent)
    keyword_coverage: ScoreComponent = Field(default_factory=ScoreComponent)
    components: dict[str, ScoreComponent] = Field(default_factory=dict)
    evidence: EvidenceCoverage = Field(default_factory=EvidenceCoverage)
    methodology: str = ""


class KeywordSuggestion(BaseModel):
    model_config = ConfigDict(extra="ignore")

    keyword: str
    source: str
    suggestion: str
    reason: str = ""
    evidence: str = ""
    origin: Literal["deterministic", "semantic"] = "deterministic"


class AnalysisResult(BaseModel):
    model_config = ConfigDict(extra="ignore")

    job: JobDescription
    matches: list[SkillMatch] = Field(default_factory=list)
    score: ScoreBreakdown = Field(default_factory=ScoreBreakdown)
    score_report: ScoreReport = Field(default_factory=ScoreReport)
    score_label: str = ""
    matched_skills: list[str] = Field(default_factory=list)
    partial_skills: list[str] = Field(default_factory=list)
    missing_must_have: list[str] = Field(default_factory=list)
    missing_nice_to_have: list[str] = Field(default_factory=list)
    keyword_coverage: dict[str, bool] = Field(default_factory=dict)
    keyword_coverage_pct: float = 0
    keyword_suggestions: list[KeywordSuggestion] = Field(default_factory=list)
    recommendations: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    provenance: ProfileProvenance = Field(default_factory=ProfileProvenance)
    keyword_sources: dict[str, list[str]] = Field(default_factory=dict)
    keyword_origins: dict[str, list[str]] = Field(default_factory=dict)


class ResumeHeader(BaseModel):
    model_config = ConfigDict(extra="ignore")

    name: str
    title: str
    location: str = ""
    email: str = ""
    phone: str = ""
    linkedin: str = ""
    github: str = ""
    portfolio: str = ""


class SkillGroup(BaseModel):
    model_config = ConfigDict(extra="ignore")

    category: str
    items: list[str] = Field(default_factory=list)


class ResumeRole(BaseModel):
    model_config = ConfigDict(extra="ignore")

    role: str
    start: str = ""
    end: str = ""


class ResumeExperience(BaseModel):
    model_config = ConfigDict(extra="ignore")

    company: str
    role: str
    location: str = ""
    start: str = ""
    end: str = ""
    roles: list[ResumeRole] = Field(default_factory=list)
    bullets: list[str] = Field(default_factory=list)
    technologies: list[str] = Field(default_factory=list)


class ResumeProject(BaseModel):
    model_config = ConfigDict(extra="ignore")

    name: str
    context: str = ""
    period: str = ""
    bullets: list[str] = Field(default_factory=list)
    technologies: list[str] = Field(default_factory=list)


class TailoredResume(BaseModel):
    model_config = ConfigDict(extra="ignore")

    header: ResumeHeader
    summary: str
    skills: list[SkillGroup] = Field(default_factory=list)
    experience: list[ResumeExperience] = Field(default_factory=list)
    projects: list[ResumeProject] = Field(default_factory=list)
    certifications: list[str] = Field(default_factory=list)
    education: list[str] = Field(default_factory=list)
    awards: list[str] = Field(default_factory=list)


class ValidationIssue(BaseModel):
    model_config = ConfigDict(extra="ignore")

    severity: Literal["error", "warning"] = "error"
    location: str
    message: str
    claim_type: str = ""
    source: str = ""
    evidence_text: str = ""
    requires_review: bool = False


class ReviewItem(BaseModel):
    model_config = ConfigDict(extra="ignore")

    location: str
    claim: str
    claim_type: str
    reason: str
    source: str = ""


class ValidationReport(BaseModel):
    model_config = ConfigDict(extra="ignore")

    valid: bool = True
    issues: list[ValidationIssue] = Field(default_factory=list)
    review_required: bool = False
    review_items: list[ReviewItem] = Field(default_factory=list)
    checked_claims: int = 0
    supported_claims: int = 0
    repaired: bool = False
    fallback_used: bool = False
    fallback_reason: str = ""
    llm_attempts: int = 0


class AtsCheck(BaseModel):
    model_config = ConfigDict(extra="ignore")

    name: str
    passed: bool
    detail: str = ""


class AtsReport(BaseModel):
    model_config = ConfigDict(extra="ignore")

    checks: list[AtsCheck] = Field(default_factory=list)
    keyword_coverage: dict[str, bool] = Field(default_factory=dict)
    keyword_coverage_pct: float = 0
    page_count: int = 0
    text_length: int = 0


class ArtifactInfo(BaseModel):
    model_config = ConfigDict(extra="ignore")

    kind: str
    filename: str
    size: int = 0


class AnalysisNote(BaseModel):
    model_config = ConfigDict(extra="ignore")

    target: str
    text: str
    created_at: datetime = Field(default_factory=utc_now)
    status: Literal["pending", "accepted", "rejected"] = "pending"
    reason: str = ""
    claim_flags: list[str] = Field(default_factory=list)


TrackerStatus = Literal["draft", "analyzed", "email_drafted", "prefilled", "submitted"]


class ApplicationRecord(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)
    company: str = ""
    role: str = ""
    source_url: str = ""
    profile_urls: list[str] = Field(default_factory=list)
    status: str = "analyzed"
    stage: str = "done"
    message: str = ""
    score: float = 0
    profile_hash: str = ""
    model: str = ""
    artifacts: list[ArtifactInfo] = Field(default_factory=list)
    error: str = ""
    notes: list[AnalysisNote] = Field(default_factory=list)
    notes_revision: str = ""
    notes_validated_revision: str = ""
    contacts: list[ContactCandidate] = Field(default_factory=list)
    selected_contact: str = ""
    email_draft: EmailDraft | None = None
    tracker_status: TrackerStatus = "draft"
    prefill_count: int = 0

    @property
    def progress_pct(self) -> int:
        if self.status in {"analyzed", "generated"}:
            return 100
        if self.status in {"error", "needs_paste"}:
            return 0
        if self.status == "generating":
            return {"tailoring": 35, "rendering": 75}.get(self.stage, 15)
        if self.status == "draft":
            return 5
        return {
            "queued": 5,
            "fetching": 15,
            "extracting": 40,
            "reviewing": 55,
            "matching": 65,
            "notes": 60,
            "scoring": 85,
            "suggestions": 95,
            "analyzing": 70,
        }.get(self.stage, 5)
