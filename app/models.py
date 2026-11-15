from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


def utc_now() -> datetime:
    return datetime.now(UTC)


class Evidence(BaseModel):
    model_config = ConfigDict(extra="ignore")

    source: str
    text: str


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
    generated_at: datetime = Field(default_factory=utc_now)

    def all_evidence(self) -> list[Evidence]:
        evidence: list[Evidence] = []
        for items in self.skills.values():
            evidence.extend(items)
        for exp in self.experience:
            if exp.summary:
                evidence.append(Evidence(source=exp.company, text=exp.summary))
            evidence.extend(Evidence(source=exp.company, text=bullet) for bullet in exp.bullets)
        for project in self.projects:
            if project.summary:
                evidence.append(Evidence(source=project.name, text=project.summary))
            evidence.extend(
                Evidence(source=project.name, text=bullet) for bullet in project.bullets
            )
        evidence.extend(Evidence(source="certification", text=c.name) for c in self.certifications)
        evidence.extend(Evidence(source="award", text=award) for award in self.awards)
        return evidence


class Requirement(BaseModel):
    model_config = ConfigDict(extra="ignore")

    skill: str
    category: str = ""
    importance: Literal["must_have", "nice_to_have"] = "must_have"
    weight: float = 1.0
    context: str = ""


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
    responsibilities: list[str] = Field(default_factory=list)
    certifications: list[Requirement] = Field(default_factory=list)
    keywords: dict[str, float] = Field(default_factory=dict)
    raw_text: str = ""
    source_url: str = ""
    fetched_at: datetime = Field(default_factory=utc_now)
    extraction_method: str = "heuristic"
    warnings: list[str] = Field(default_factory=list)


class SkillMatch(BaseModel):
    model_config = ConfigDict(extra="ignore")

    skill: str
    category: str = ""
    importance: Literal["must_have", "nice_to_have"] = "must_have"
    status: Literal["matched", "partial", "missing"] = "missing"
    evidence: list[Evidence] = Field(default_factory=list)
    confidence: float = 0.0
    note: str = ""


class ScoreBreakdown(BaseModel):
    model_config = ConfigDict(extra="ignore")

    must_have: float = 0
    nice_to_have: float = 0
    responsibilities: float = 0
    certifications: float = 0
    seniority: float = 0
    keywords: float = 0
    total: float = 0


class AnalysisResult(BaseModel):
    model_config = ConfigDict(extra="ignore")

    job: JobDescription
    matches: list[SkillMatch] = Field(default_factory=list)
    score: ScoreBreakdown = Field(default_factory=ScoreBreakdown)
    score_label: str = ""
    matched_skills: list[str] = Field(default_factory=list)
    partial_skills: list[str] = Field(default_factory=list)
    missing_must_have: list[str] = Field(default_factory=list)
    missing_nice_to_have: list[str] = Field(default_factory=list)
    keyword_coverage: dict[str, bool] = Field(default_factory=dict)
    keyword_coverage_pct: float = 0
    recommendations: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


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


class ResumeExperience(BaseModel):
    model_config = ConfigDict(extra="ignore")

    company: str
    role: str
    location: str = ""
    start: str = ""
    end: str = ""
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


class ValidationReport(BaseModel):
    model_config = ConfigDict(extra="ignore")

    valid: bool = True
    issues: list[ValidationIssue] = Field(default_factory=list)
    repaired: bool = False
    fallback_used: bool = False


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


class ApplicationRecord(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)
    company: str = ""
    role: str = ""
    source_url: str = ""
    status: str = "analyzed"
    stage: str = "done"
    message: str = ""
    score: float = 0
    profile_hash: str = ""
    model: str = ""
    artifacts: list[ArtifactInfo] = Field(default_factory=list)
    error: str = ""
