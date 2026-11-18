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
    ResumeRole,
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


MONTH_INDEX = {
    "jan": 1,
    "feb": 2,
    "mar": 3,
    "apr": 4,
    "may": 5,
    "jun": 6,
    "jul": 7,
    "aug": 8,
    "sep": 9,
    "oct": 10,
    "nov": 11,
    "dec": 12,
}


def _date_key(value: str) -> tuple[int, int]:
    text = value.strip()
    if text.casefold() == "present":
        return (9999, 12)
    match = re.match(r"([A-Za-z]{3})\s+(\d{4})", text)
    if not match:
        return (0, 0)
    return (int(match.group(2)), MONTH_INDEX.get(match.group(1)[:3].casefold(), 0))


def _unique_values(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        key = _norm(value)
        if key and key not in seen:
            seen.add(key)
            result.append(value)
    return result


def merge_experience_entries(
    entries: list[ResumeExperience], *, bullet_limit: bool = True
) -> list[ResumeExperience]:
    groups: dict[str, list[ResumeExperience]] = {}
    order: list[str] = []
    for entry in entries:
        key = _norm(entry.company) or entry.company.strip().casefold()
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(entry)

    result: list[ResumeExperience] = []
    for group_index, key in enumerate(order):
        group = groups[key]
        if len(group) == 1 and not group[0].roles:
            result.append(group[0])
            continue

        roles: list[ResumeRole] = []
        seen_roles: set[tuple[str, str, str]] = set()
        for entry in group:
            entry_roles = entry.roles or [
                ResumeRole(role=entry.role, start=entry.start, end=entry.end)
            ]
            for role in entry_roles:
                marker = (role.role.strip().casefold(), role.start.strip(), role.end.strip())
                if not marker[0] or marker in seen_roles:
                    continue
                seen_roles.add(marker)
                roles.append(role)
        if not roles:
            continue
        roles.sort(key=lambda role: _date_key(role.start), reverse=True)
        ordered_entries = sorted(group, key=lambda entry: _date_key(entry.start), reverse=True)
        newest_entry = ordered_entries[0]
        newest_role = roles[0]
        starts = [role.start for role in roles if role.start]
        start = min(starts, key=_date_key) if starts else ""
        end = (
            "Present"
            if any(role.end.casefold() == "present" for role in roles)
            else newest_role.end
        )
        bullets = _unique_values(
            [bullet for entry in ordered_entries for bullet in entry.bullets if bullet.strip()]
        )
        if bullet_limit:
            limit = min(5, experience_bullet_limit(group_index) + len(group) - 1)
            bullets = bullets[:limit]
        technologies = _unique_values(
            [tech for entry in ordered_entries for tech in entry.technologies if tech.strip()]
        )[:8]
        result.append(
            ResumeExperience(
                company=newest_entry.company,
                role=newest_role.role,
                location=newest_entry.location
                or next((entry.location for entry in ordered_entries if entry.location), ""),
                start=start,
                end=end,
                roles=roles,
                bullets=bullets,
                technologies=technologies,
            )
        )
    return result


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


TITLE_LEVEL_WORDS = {
    "senior",
    "junior",
    "lead",
    "staff",
    "principal",
    "director",
    "vp",
    "head",
    "chief",
    "engineer",
    "engineering",
    "architect",
    "manager",
    "developer",
}


def _profile_title(profile: CandidateProfile) -> str:
    headline = re.split(r"\s*[|•·]\s*", profile.identity.headline.strip())[0].strip()
    if headline:
        return headline
    if profile.experience:
        return profile.experience[0].role
    return "Technology professional"


def _supported_title(profile: CandidateProfile, value: str) -> str:
    title = value.strip()
    if not title:
        return ""
    haystack = " ".join(
        [profile.identity.headline, *(entry.role for entry in profile.experience)]
    ).casefold()
    if title.casefold() in haystack:
        return title
    wanted = {
        token
        for token in re.findall(r"[a-z0-9+#.]+", title.casefold())
        if len(token) > 2 and token not in TITLE_LEVEL_WORDS
    }
    available = set(re.findall(r"[a-z0-9+#.]+", haystack))
    return title if wanted and wanted <= available else ""


def _resume_title(profile: CandidateProfile, job: JobDescription) -> str:
    return _supported_title(profile, job.title) or _profile_title(profile)


def header_for(profile: CandidateProfile, job: JobDescription) -> ResumeHeader:
    identity = profile.identity
    return ResumeHeader(
        name=identity.name,
        title=_resume_title(profile, job),
        location=identity.location,
        email=identity.email,
        phone=identity.phone,
        linkedin=identity.linkedin,
        github=identity.github,
        portfolio=identity.portfolio,
    )


def _summary_title(profile: CandidateProfile, job: JobDescription) -> str:
    title = _resume_title(profile, job)
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
    years_text = f"{round(years)}+" if years >= 1 else ""
    title = _summary_title(profile, job)
    companies: list[str] = []
    for entry in profile.experience[:3]:
        if entry.company and entry.company not in companies:
            companies.append(entry.company)
    top_skills = analysis.matched_skills[:6]
    if not top_skills:
        top_skills = [item for group in deterministic_skills(profile, job) for item in group.items][
            :6
        ]
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
    return merge_experience_entries(result)


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
