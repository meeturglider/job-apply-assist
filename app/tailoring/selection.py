from __future__ import annotations

import re

from app.models import (
    AnalysisResult,
    CandidateProfile,
    Certification,
    ExperienceEntry,
    JobDescription,
    ProjectEntry,
    ResumeExperience,
    ResumeHeader,
    ResumeProject,
    SkillGroup,
    TailoredResume,
)
from app.skills import lexicon


def _job_weights(job: JobDescription) -> dict[str, float]:
    weights: dict[str, float] = {}
    for requirement in [*job.must_have, *job.nice_to_have]:
        canonical = lexicon.canonicalize(requirement.skill)
        weights[canonical] = max(weights.get(canonical, 0), requirement.weight)
    for keyword, weight in job.keywords.items():
        canonical = lexicon.canonicalize(keyword)
        weights[canonical] = max(weights.get(canonical, 0), weight)
    return weights


def relevance_score(text: str, job: JobDescription) -> float:
    weights = _job_weights(job)
    score = 0.0
    for canonical, _ in lexicon.find(text):
        score += weights.get(canonical, 0.3)
    lowered = text.casefold()
    for keyword, weight in job.keywords.items():
        if keyword.casefold() in lowered:
            score += weight * 0.3
    if job.title and job.title.casefold() in lowered:
        score += 2.0
    return score


def select_bullets(bullets: list[str], job: JobDescription, limit: int) -> list[str]:
    ranked = [
        (relevance_score(bullet, job), index, bullet)
        for index, bullet in enumerate(bullets)
        if bullet.strip()
    ]
    ranked.sort(key=lambda item: (-item[0], item[1]))
    selected: list[str] = []
    selected_tokens: list[set[str]] = []
    for _, _, bullet in ranked:
        tokens = {token for token in re.findall(r"[a-z0-9]+", bullet.casefold()) if len(token) > 2}
        if any(
            tokens and previous and len(tokens & previous) / len(tokens | previous) >= 0.6
            for previous in selected_tokens
        ):
            continue
        selected.append(bullet)
        selected_tokens.append(tokens)
        if len(selected) >= limit:
            break
    return selected


def select_projects(
    projects: list[ProjectEntry], job: JobDescription, limit: int = 3
) -> list[ProjectEntry]:
    ranked = [
        (relevance_score(" ".join([project.name, project.summary, *project.bullets]), job), project)
        for project in projects
    ]
    ranked.sort(key=lambda item: -item[0])
    return [project for score, project in ranked if score > 0][:limit] or [
        project for _, project in ranked[:limit]
    ]


def experience_bullet_limit(index: int) -> int:
    if index == 0:
        return 4
    if index == 1:
        return 3
    if index <= 3:
        return 2
    return 1


def deterministic_skills(
    profile: CandidateProfile, job: JobDescription, limit: int = 30
) -> list[SkillGroup]:
    weights = _job_weights(job)
    relevant = [name for name in profile.skills if weights.get(name, 0) > 0]
    if len(relevant) < 6:
        relevant = list(dict.fromkeys([*relevant, *profile.skills]))
    items = sorted(
        relevant,
        key=lambda name: (-weights.get(name, 0), lexicon.category_for(name), name),
    )[:limit]
    groups: dict[str, list[str]] = {}
    for item in items:
        category = lexicon.category_for(item) or "Additional"
        groups.setdefault(category, []).append(item)
    return [
        SkillGroup(category=category, items=values)
        for category, values in sorted(
            groups.items(),
            key=lambda item: (
                -sum(weights.get(skill, 0) for skill in item[1]),
                item[0],
            ),
        )
    ]


def header_for(profile: CandidateProfile, job: JobDescription) -> ResumeHeader:
    identity = profile.identity
    return ResumeHeader(
        name=identity.name,
        title=job.title or identity.headline,
        location=identity.location,
        email=identity.email,
        phone=identity.phone,
        linkedin=identity.linkedin,
        github=identity.github,
        portfolio=identity.portfolio,
    )


def _summary_title(profile: CandidateProfile, job: JobDescription) -> str:
    title = job.title or profile.identity.headline or "Technology professional"
    unsupported = [
        canonical
        for canonical, _ in lexicon.find(title)
        if lexicon.canonicalize(canonical) not in profile.skills
    ]
    return "Experienced technology professional" if unsupported else title


def deterministic_summary(
    profile: CandidateProfile, job: JobDescription, analysis: AnalysisResult
) -> str:
    years = profile.experience_years
    years_text = f"{int(years)}+" if years >= 1 else ""
    title = _summary_title(profile, job)
    companies: list[str] = []
    for entry in profile.experience[:3]:
        if entry.company and entry.company not in companies:
            companies.append(entry.company)
    top_skills = analysis.matched_skills[:6]
    if not top_skills:
        top_skills = [requirement.skill for requirement in job.must_have[:5]]
    parts = [
        title
        + (f" with {years_text} years of experience" if years_text else "")
        + (f" across {', '.join(companies[:2])}" if companies else "")
        + "."
    ]
    if top_skills:
        parts.append(f"Hands-on across {', '.join(top_skills[:6])}.")
    if profile.summary:
        summary_sentence = re.split(r"(?<=[.!?])\s+", profile.summary)[0]
        unsupported = [
            canonical
            for canonical, _ in lexicon.find(summary_sentence)
            if lexicon.canonicalize(canonical) not in profile.skills
        ]
        if summary_sentence and "year" not in summary_sentence.casefold() and not unsupported:
            parts.append(summary_sentence)
    return " ".join(parts)[:900]


def deterministic_experience(
    profile: CandidateProfile, job: JobDescription
) -> list[ResumeExperience]:
    result: list[ResumeExperience] = []
    for index, entry in enumerate(profile.experience):
        bullets = select_bullets(entry.bullets, job, experience_bullet_limit(index))
        if not bullets and entry.summary:
            bullets = [entry.summary]
        result.append(
            ResumeExperience(
                company=entry.company,
                role=entry.role,
                location=entry.location,
                start=entry.start,
                end=entry.end,
                bullets=bullets,
                technologies=entry.technologies[:8],
            )
        )
    return result


def deterministic_projects(profile: CandidateProfile, job: JobDescription) -> list[ResumeProject]:
    result: list[ResumeProject] = []
    for project in select_projects(profile.projects, job, limit=2):
        bullets = select_bullets(project.bullets, job, 2)
        if not bullets and project.summary:
            bullets = [project.summary]
        result.append(
            ResumeProject(
                name=project.name,
                context=project.context,
                period=project.period,
                bullets=bullets,
                technologies=project.technologies[:8],
            )
        )
    return result


def deterministic_resume(
    profile: CandidateProfile, job: JobDescription, analysis: AnalysisResult
) -> TailoredResume:
    return TailoredResume(
        header=header_for(profile, job),
        summary=deterministic_summary(profile, job, analysis),
        skills=deterministic_skills(profile, job),
        experience=deterministic_experience(profile, job),
        projects=deterministic_projects(profile, job),
        certifications=[cert.name for cert in profile.certifications],
        education=[
            ", ".join(
                value
                for value in [education.degree, education.institution, education.year]
                if value
            )
            for education in profile.education
        ],
        awards=profile.awards[:2],
    )


def compact_experience(entry: ExperienceEntry, job: JobDescription) -> dict[str, object]:
    return {
        "company": entry.company,
        "role": entry.role,
        "location": entry.location,
        "start": entry.start,
        "end": entry.end,
        "summary": entry.summary,
        "bullets": select_bullets(entry.bullets, job, 5),
        "technologies": entry.technologies[:12],
    }


def _norm(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", value.casefold())


def _similar(left: str, right: str, threshold: float = 0.62) -> bool:
    left_key, right_key = _norm(left), _norm(right)
    if not left_key or not right_key:
        return False
    if left_key == right_key or left_key in right_key or right_key in left_key:
        return True
    left_tokens = set(re.findall(r"[a-z0-9]+", left.casefold()))
    right_tokens = set(re.findall(r"[a-z0-9]+", right.casefold()))
    if not left_tokens or not right_tokens:
        return False
    return len(left_tokens & right_tokens) / len(left_tokens | right_tokens) >= threshold


def find_experience(profile: CandidateProfile, company: str, role: str) -> ExperienceEntry | None:
    for entry in profile.experience:
        if _similar(entry.company, company, 0.7) and _similar(entry.role, role, 0.55):
            return entry
    company_matches = [
        entry for entry in profile.experience if _similar(entry.company, company, 0.75)
    ]
    if len(company_matches) == 1:
        return company_matches[0]
    return None


def find_project(profile: CandidateProfile, name: str) -> ProjectEntry | None:
    return next(
        (project for project in profile.projects if _similar(project.name, name, 0.55)), None
    )


def find_certification(profile: CandidateProfile, name: str) -> Certification | None:
    return next(
        (cert for cert in profile.certifications if _similar(cert.name, name, 0.55)),
        None,
    )


def deterministic_education(profile: CandidateProfile) -> list[str]:
    return [
        ", ".join(
            value for value in [education.degree, education.institution, education.year] if value
        )
        for education in profile.education
    ]
