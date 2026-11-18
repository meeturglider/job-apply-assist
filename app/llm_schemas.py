from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class LLMRequirement(BaseModel):
    model_config = ConfigDict(extra="ignore")

    skill: str
    category: str = ""
    weight: float = 1.0
    context: str = ""
    dimension: str = ""


class LLMJobExtraction(BaseModel):
    model_config = ConfigDict(extra="ignore")

    title: str = ""
    company: str = ""
    location: str = ""
    seniority: str = ""
    role_family: str = ""
    summary: str = ""
    must_have: list[LLMRequirement] = Field(default_factory=list)
    nice_to_have: list[LLMRequirement] = Field(default_factory=list)
    education_requirements: list[LLMRequirement] = Field(default_factory=list)
    experience_requirements: list[LLMRequirement] = Field(default_factory=list)
    responsibilities: list[str] = Field(default_factory=list)
    certifications: list[LLMRequirement] = Field(default_factory=list)
    keywords: dict[str, float] = Field(default_factory=dict)


class LLMJobReview(BaseModel):
    model_config = ConfigDict(extra="ignore")

    title: str = ""
    company: str = ""
    location: str = ""
    seniority: str = ""
    role_family: str = ""
    summary: str = ""
    must_have_ids: list[str] = Field(default_factory=list)
    nice_to_have_ids: list[str] = Field(default_factory=list)
    accepted_ids: list[str] = Field(default_factory=list)
    education_ids: list[str] = Field(default_factory=list)
    experience_ids: list[str] = Field(default_factory=list)
    certification_ids: list[str] = Field(default_factory=list)
    responsibility_ids: list[str] = Field(default_factory=list)


class LLMProfileSuggestion(BaseModel):
    model_config = ConfigDict(extra="ignore")

    keyword: str = ""
    evidence_index: int = Field(ge=0)
    suggestion: str = ""
    reason: str = ""


class LLMProfileSuggestions(BaseModel):
    model_config = ConfigDict(extra="ignore")

    suggestions: list[LLMProfileSuggestion] = Field(default_factory=list)


class LLMSkillGroup(BaseModel):
    model_config = ConfigDict(extra="ignore")

    category: str = "Additional"
    items: list[str] = Field(default_factory=list)


class LLMResumeExperience(BaseModel):
    model_config = ConfigDict(extra="ignore")

    company: str = ""
    role: str = ""
    location: str = ""
    start: str = ""
    end: str = ""
    bullets: list[str] = Field(default_factory=list)
    technologies: list[str] = Field(default_factory=list)


class LLMResumeProject(BaseModel):
    model_config = ConfigDict(extra="ignore")

    name: str = ""
    context: str = ""
    period: str = ""
    bullets: list[str] = Field(default_factory=list)
    technologies: list[str] = Field(default_factory=list)


class LLMResumePlan(BaseModel):
    model_config = ConfigDict(extra="ignore")

    summary: str = ""
    skills: list[LLMSkillGroup] = Field(default_factory=list)
    experience: list[LLMResumeExperience] = Field(default_factory=list)
    projects: list[LLMResumeProject] = Field(default_factory=list)
    certifications: list[str] = Field(default_factory=list)
    education: list[str] = Field(default_factory=list)
    awards: list[str] = Field(default_factory=list)
