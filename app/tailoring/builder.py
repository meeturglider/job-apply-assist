from __future__ import annotations

import json
from typing import Any

from app.llm import OllamaClient
from app.models import (
    AnalysisResult,
    CandidateProfile,
    JobDescription,
    ResumeExperience,
    ResumeProject,
    SkillGroup,
    TailoredResume,
    ValidationReport,
)
from app.prompts import load_prompt
from app.tailoring.selection import (
    compact_experience,
    deterministic_education,
    deterministic_projects,
    deterministic_resume,
    deterministic_skills,
    deterministic_summary,
    experience_bullet_limit,
    find_certification,
    find_experience,
    find_project,
    header_for,
    select_bullets,
    select_projects,
)
from app.tailoring.validator import sanitize_resume, validate_resume


def _profile_payload(
    profile: CandidateProfile, job: JobDescription, analysis: AnalysisResult
) -> dict[str, Any]:
    projects = select_projects(profile.projects, job, limit=3)
    return {
        "identity": profile.identity.model_dump(),
        "summary": profile.summary,
        "experience_years": profile.experience_years,
        "experience": [compact_experience(entry, job) for entry in profile.experience],
        "projects": [
            {
                "name": project.name,
                "context": project.context,
                "period": project.period,
                "summary": project.summary,
                "bullets": select_bullets(project.bullets, job, 5),
                "technologies": project.technologies[:12],
            }
            for project in projects
        ],
        "skills": sorted(profile.skills),
        "certifications": [cert.name for cert in profile.certifications],
        "education": deterministic_education(profile),
        "awards": profile.awards,
        "matched_skills": analysis.matched_skills,
        "partial_skills": analysis.partial_skills,
        "missing_must_have": analysis.missing_must_have,
        "missing_nice_to_have": analysis.missing_nice_to_have,
        "score": analysis.score.model_dump(),
    }


def _parse_skill_groups(values: Any) -> list[SkillGroup]:
    groups: list[SkillGroup] = []
    if not isinstance(values, list):
        return groups
    for item in values:
        if not isinstance(item, dict):
            continue
        category = str(item.get("category", "Additional")).strip() or "Additional"
        raw_items = item.get("items", [])
        if not isinstance(raw_items, list):
            continue
        items = [str(value).strip() for value in raw_items if str(value).strip()]
        if items:
            groups.append(SkillGroup(category=category, items=items))
    return groups


def _llm_experience(
    values: Any, profile: CandidateProfile, job: JobDescription
) -> list[ResumeExperience]:
    generated: dict[int, dict[str, Any]] = {}
    if isinstance(values, list):
        for item in values:
            if not isinstance(item, dict):
                continue
            source = find_experience(
                profile,
                str(item.get("company", "")),
                str(item.get("role", "")),
            )
            if source is None:
                continue
            raw_bullets = item.get("bullets", [])
            bullets = (
                [str(bullet).strip() for bullet in raw_bullets if str(bullet).strip()]
                if isinstance(raw_bullets, list)
                else []
            )
            generated[id(source)] = {"bullets": bullets}

    result: list[ResumeExperience] = []
    for index, source in enumerate(profile.experience):
        item = generated.get(id(source), {})
        bullets = list(item.get("bullets", []))
        if not bullets:
            bullets = select_bullets(source.bullets, job, experience_bullet_limit(index))
        if not bullets and source.summary:
            bullets = [source.summary]
        result.append(
            ResumeExperience(
                company=source.company,
                role=source.role,
                location=source.location,
                start=source.start,
                end=source.end,
                bullets=bullets,
                technologies=source.technologies[:8],
            )
        )
    return result


def _llm_projects(
    values: Any, profile: CandidateProfile, job: JobDescription
) -> list[ResumeProject]:
    result: list[ResumeProject] = []
    seen: set[int] = set()
    if isinstance(values, list):
        for item in values:
            if not isinstance(item, dict):
                continue
            source = find_project(profile, str(item.get("name", "")))
            if source is None or id(source) in seen:
                continue
            seen.add(id(source))
            raw_bullets = item.get("bullets", [])
            bullets = (
                [str(bullet).strip() for bullet in raw_bullets if str(bullet).strip()]
                if isinstance(raw_bullets, list)
                else []
            )
            if not bullets:
                bullets = select_bullets(source.bullets, job, 2)
            if not bullets and source.summary:
                bullets = [source.summary]
            result.append(
                ResumeProject(
                    name=source.name,
                    context=source.context,
                    period=source.period,
                    bullets=bullets,
                    technologies=source.technologies[:8],
                )
            )
            if len(result) >= 3:
                break
    if len(result) < 2:
        return deterministic_projects(profile, job)
    return result


def _filter_certifications(values: Any, profile: CandidateProfile) -> list[str]:
    result: list[str] = []
    if isinstance(values, list):
        for value in values:
            name = str(value).strip()
            cert = find_certification(profile, name)
            if cert and cert.name not in result:
                result.append(cert.name)
    return result or [cert.name for cert in profile.certifications]


def _filter_awards(values: Any, profile: CandidateProfile) -> list[str]:
    if not isinstance(values, list):
        return profile.awards[:3]
    haystack = " ".join(profile.awards).casefold()
    result = [
        str(value).strip()
        for value in values
        if str(value).strip() and str(value).strip().casefold() in haystack
    ]
    return result or profile.awards[:3]


def _resume_from_llm(
    data: dict[str, Any],
    profile: CandidateProfile,
    job: JobDescription,
    analysis: AnalysisResult,
) -> TailoredResume:
    skills = _parse_skill_groups(data.get("skills"))
    if not skills:
        skills = deterministic_skills(profile, job)
    summary = str(data.get("summary", "")).strip() or deterministic_summary(profile, job, analysis)
    return TailoredResume(
        header=header_for(profile, job),
        summary=summary,
        skills=skills,
        experience=_llm_experience(data.get("experience"), profile, job),
        projects=_llm_projects(data.get("projects"), profile, job),
        certifications=_filter_certifications(data.get("certifications"), profile),
        education=deterministic_education(profile),
        awards=_filter_awards(data.get("awards"), profile),
    )


def _fallback(
    profile: CandidateProfile,
    job: JobDescription,
    analysis: AnalysisResult,
    method: str,
    reason: str = "",
) -> tuple[TailoredResume, ValidationReport, str]:
    resume = deterministic_resume(profile, job, analysis)
    report = validate_resume(resume, profile, job)
    report.fallback_used = True
    if reason:
        from app.models import ValidationIssue

        report.issues.append(
            ValidationIssue(severity="warning", location="generation", message=reason)
        )
    return resume, report, method


async def build_resume(
    profile: CandidateProfile,
    job: JobDescription,
    analysis: AnalysisResult,
    llm: OllamaClient,
) -> tuple[TailoredResume, ValidationReport, str]:
    if not llm.enabled:
        return _fallback(profile, job, analysis, "deterministic")

    prompt = (
        "Build the tailored resume from this JSON payload. Return only the JSON object.\n\n"
        + json.dumps(_profile_payload(profile, job, analysis), indent=2, ensure_ascii=False)
    )
    try:
        data = await llm.json(load_prompt("resume_tailor.md"), prompt, temperature=0.15)
        resume = _resume_from_llm(data, profile, job, analysis)
    except Exception as exc:
        return _fallback(
            profile,
            job,
            analysis,
            "deterministic-fallback",
            f"LLM tailoring failed; deterministic resume used. {exc}",
        )
    report = validate_resume(resume, profile, job)
    if report.valid:
        return resume, report, "llm"

    sanitized = sanitize_resume(resume, profile, job, analysis)
    sanitized_report = validate_resume(sanitized, profile, job)
    sanitized_report.repaired = True
    if sanitized_report.valid:
        return sanitized, sanitized_report, "llm-sanitized"

    report.issues.extend(sanitized_report.issues)
    return _fallback(
        profile,
        job,
        analysis,
        "deterministic-fallback",
        "Generated resume failed truthfulness checks; deterministic resume used.",
    )
