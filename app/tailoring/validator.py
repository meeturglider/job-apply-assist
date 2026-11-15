from __future__ import annotations

import re

from app.models import (
    AnalysisResult,
    CandidateProfile,
    JobDescription,
    ResumeExperience,
    ResumeProject,
    SkillGroup,
    TailoredResume,
    ValidationIssue,
    ValidationReport,
)
from app.skills import lexicon
from app.tailoring.selection import (
    deterministic_education,
    deterministic_projects,
    deterministic_summary,
    experience_bullet_limit,
    find_certification,
    find_experience,
    find_project,
    header_for,
    select_bullets,
)

NUMBER_RE = re.compile(r"\b\d[\d,]*(?:\.\d+)?%?\+?")


def _numbers(value: str) -> set[str]:
    result: set[str] = set()
    for match in NUMBER_RE.finditer(value):
        token = match.group(0).replace(",", "").replace(" ", "")
        if token:
            result.add(token)
    return result


def _source_texts(profile: CandidateProfile) -> list[str]:
    texts = [profile.summary]
    for entry in profile.experience:
        texts.extend([entry.summary, *entry.bullets])
    for project in profile.projects:
        texts.extend([project.summary, *project.bullets])
    for cert in profile.certifications:
        texts.append(cert.name)
    return [text for text in texts if text]


def _allowed_skills(profile: CandidateProfile) -> set[str]:
    return {lexicon.normalize(name) for name in profile.skills}


def _skill_supported(name: str, allowed: set[str]) -> bool:
    canonical = lexicon.canonicalize(name)
    return lexicon.normalize(canonical) in allowed or lexicon.normalize(name) in allowed


def _unsupported_skills(text: str, allowed: set[str]) -> list[str]:
    found = [canonical for canonical, _ in lexicon.find(text)]
    return [name for name in found if not _skill_supported(name, allowed)]


def _numbers_supported(text: str, source: str, extra: set[str] | None = None) -> set[str]:
    source_numbers = _numbers(source) | (extra or set())
    return {number for number in _numbers(text) if number not in source_numbers}


def validate_resume(
    resume: TailoredResume,
    profile: CandidateProfile,
    job: JobDescription,
) -> ValidationReport:
    issues: list[ValidationIssue] = []
    allowed = _allowed_skills(profile)
    all_source = " ".join(_source_texts(profile))
    year_tokens = {
        str(int(profile.experience_years)),
        f"{int(profile.experience_years)}+",
        f"{profile.experience_years:g}",
    }

    if not resume.summary.strip():
        issues.append(ValidationIssue(location="summary", message="Summary is empty."))
    unsupported_summary = _unsupported_skills(resume.summary, allowed)
    if unsupported_summary:
        issues.append(
            ValidationIssue(
                location="summary",
                message="Summary mentions unsupported skills: " + ", ".join(unsupported_summary),
            )
        )
    unsupported_numbers = _numbers_supported(resume.summary, all_source, extra=year_tokens)
    if unsupported_numbers:
        issues.append(
            ValidationIssue(
                location="summary",
                message="Summary contains numbers not supported by the profile: "
                + ", ".join(sorted(unsupported_numbers)),
            )
        )
    if re.search(r"\b(i|me|my|we|our)\b", resume.summary, re.IGNORECASE):
        issues.append(
            ValidationIssue(
                severity="warning",
                location="summary",
                message="Summary uses first-person language; ATS resumes usually avoid it.",
            )
        )

    for group in resume.skills:
        for item in group.items:
            if not _skill_supported(item, allowed):
                issues.append(
                    ValidationIssue(
                        location=f"skills/{group.category}",
                        message=f"Unsupported skill: {item}",
                    )
                )

    if not resume.experience:
        issues.append(
            ValidationIssue(location="experience", message="Resume has no experience entries.")
        )
    for entry in resume.experience:
        source = find_experience(profile, entry.company, entry.role)
        if source is None:
            issues.append(
                ValidationIssue(
                    location=f"experience/{entry.company}",
                    message="Company or role does not match the profile.",
                )
            )
            continue
        if entry.start and source.start and entry.start != source.start:
            issues.append(
                ValidationIssue(
                    location=f"experience/{entry.company}",
                    message=f"Start date {entry.start} differs from profile {source.start}.",
                )
            )
        if entry.end and source.end and entry.end != source.end:
            issues.append(
                ValidationIssue(
                    location=f"experience/{entry.company}",
                    message=f"End date {entry.end} differs from profile {source.end}.",
                )
            )
        source_text = " ".join([source.summary, *source.bullets])
        for bullet in entry.bullets:
            unsupported = _unsupported_skills(bullet, allowed)
            if unsupported:
                issues.append(
                    ValidationIssue(
                        location=f"experience/{entry.company}",
                        message="Bullet mentions unsupported skills: " + ", ".join(unsupported),
                    )
                )
            numbers = _numbers_supported(bullet, source_text)
            if numbers:
                issues.append(
                    ValidationIssue(
                        location=f"experience/{entry.company}",
                        message="Bullet contains unsupported numbers: "
                        + ", ".join(sorted(numbers)),
                    )
                )

    for project in resume.projects:
        project_source = find_project(profile, project.name)
        if project_source is None:
            issues.append(
                ValidationIssue(
                    location=f"project/{project.name}",
                    message="Project does not match the portfolio profile.",
                )
            )
            continue
        source_text = " ".join([project_source.summary, *project_source.bullets])
        for bullet in project.bullets:
            unsupported = _unsupported_skills(bullet, allowed)
            if unsupported:
                issues.append(
                    ValidationIssue(
                        location=f"project/{project.name}",
                        message="Bullet mentions unsupported skills: " + ", ".join(unsupported),
                    )
                )
            numbers = _numbers_supported(bullet, source_text)
            if numbers:
                issues.append(
                    ValidationIssue(
                        location=f"project/{project.name}",
                        message="Bullet contains unsupported numbers: "
                        + ", ".join(sorted(numbers)),
                    )
                )

    for cert in resume.certifications:
        if find_certification(profile, cert) is None:
            issues.append(
                ValidationIssue(
                    location="certifications",
                    message=f"Certification is not in the profile: {cert}",
                )
            )

    allowed_awards = " ".join(profile.awards).casefold()
    for award in resume.awards:
        if profile.awards and award.casefold() not in allowed_awards:
            issues.append(
                ValidationIssue(
                    severity="warning",
                    location="awards",
                    message=f"Award may not match the profile: {award}",
                )
            )

    return ValidationReport(
        valid=not any(issue.severity == "error" for issue in issues),
        issues=issues,
    )


def sanitize_resume(
    resume: TailoredResume,
    profile: CandidateProfile,
    job: JobDescription,
    analysis: AnalysisResult,
) -> TailoredResume:
    allowed = _allowed_skills(profile)
    skills = [
        SkillGroup(
            category=group.category,
            items=[item for item in group.items if _skill_supported(item, allowed)],
        )
        for group in resume.skills
    ]
    skills = [group for group in skills if group.items]
    if not skills:
        from app.tailoring.selection import deterministic_skills

        skills = deterministic_skills(profile, job)

    summary = resume.summary
    if _unsupported_skills(summary, allowed) or _numbers_supported(
        summary,
        " ".join(_source_texts(profile)),
        extra={str(int(profile.experience_years)), f"{int(profile.experience_years)}+"},
    ):
        summary = deterministic_summary(profile, job, analysis)

    experience: list[ResumeExperience] = []
    for index, source in enumerate(profile.experience):
        generated_experience = next(
            (
                entry
                for entry in resume.experience
                if find_experience(profile, entry.company, entry.role) is source
            ),
            None,
        )
        source_text = " ".join([source.summary, *source.bullets])
        bullets = []
        if generated_experience:
            for bullet in generated_experience.bullets:
                if _unsupported_skills(bullet, allowed):
                    continue
                if _numbers_supported(bullet, source_text):
                    continue
                bullets.append(bullet)
            bullets = bullets[: experience_bullet_limit(index)]
        if not bullets:
            bullets = select_bullets(source.bullets, job, experience_bullet_limit(index))
        if not bullets and source.summary:
            bullets = [source.summary]
        experience.append(
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

    projects: list[ResumeProject] = []
    for project_index, generated_project in enumerate(resume.projects):
        project_source = find_project(profile, generated_project.name)
        if project_source is None:
            continue
        source_text = " ".join([project_source.summary, *project_source.bullets])
        bullets = [
            bullet
            for bullet in generated_project.bullets
            if not _unsupported_skills(bullet, allowed)
            and not _numbers_supported(bullet, source_text)
        ]
        bullets = bullets[: 3 if project_index == 0 else 2]
        if not bullets:
            bullets = select_bullets(project_source.bullets, job, 2)
        if not bullets and project_source.summary:
            bullets = [project_source.summary]
        projects.append(
            ResumeProject(
                name=project_source.name,
                context=project_source.context,
                period=project_source.period,
                bullets=bullets,
                technologies=project_source.technologies[:8],
            )
        )
    if len(projects) < 2:
        projects = deterministic_projects(profile, job)

    certifications = [
        cert.name
        for cert in profile.certifications
        if any(find_certification(profile, value) is cert for value in resume.certifications)
    ]
    if not certifications:
        certifications = [cert.name for cert in profile.certifications]

    awards = [
        award
        for award in resume.awards
        if not profile.awards or award.casefold() in " ".join(profile.awards).casefold()
    ]
    if not awards:
        awards = profile.awards[:3]

    return TailoredResume(
        header=header_for(profile, job),
        summary=summary,
        skills=skills,
        experience=experience,
        projects=projects,
        certifications=certifications,
        education=deterministic_education(profile),
        awards=awards,
    )
