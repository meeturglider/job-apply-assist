from __future__ import annotations

import re

from app.models import (
    AnalysisResult,
    CandidateProfile,
    JobDescription,
    ResumeExperience,
    ResumeProject,
    ReviewItem,
    SkillGroup,
    TailoredResume,
    ValidationIssue,
    ValidationReport,
)
from app.skills import lexicon
from app.tailoring.claims import check_claims
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


def _source_texts(profile: CandidateProfile) -> list[str]:
    texts = [profile.summary]
    texts.extend(evidence.text for evidence in profile.all_evidence())
    for entry in profile.education:
        texts.extend([entry.degree, entry.institution, entry.year])
    return [text for text in texts if text]


def _experience_sources(entry: object) -> list[str]:
    from app.models import ExperienceEntry

    if not isinstance(entry, ExperienceEntry):
        return []
    texts = [entry.summary, *entry.bullets, " ".join(entry.technologies)]
    return [text for text in texts if text]


def _project_sources(entry: object) -> list[str]:
    from app.models import ProjectEntry

    if not isinstance(entry, ProjectEntry):
        return []
    texts = [entry.summary, *entry.bullets, " ".join(entry.technologies)]
    return [text for text in texts if text]


def _allowed_skills(profile: CandidateProfile) -> set[str]:
    return {lexicon.normalize(name) for name in profile.skills}


def _skill_supported(name: str, allowed: set[str]) -> bool:
    canonical = lexicon.canonicalize(name)
    return lexicon.normalize(canonical) in allowed or lexicon.normalize(name) in allowed


def _experience_year_tokens(profile: CandidateProfile) -> set[str]:
    values = {int(profile.experience_years), round(profile.experience_years)}
    return {
        *(f"{value}{suffix}" for value in values for suffix in ("", "+")),
        f"{profile.experience_years:g}",
    }


def _claim_issues(
    text: str,
    sources: list[str],
    *,
    location: str,
    allowed_numbers: set[str] | None = None,
    include_title: bool = False,
    require_trace: bool = True,
) -> tuple[list[ValidationIssue], int, int, list[ReviewItem]]:
    checks, best, score = check_claims(
        text,
        sources,
        allowed_numbers=allowed_numbers,
        include_title=include_title,
    )
    issues: list[ValidationIssue] = []
    review: list[ReviewItem] = []
    supported = 0
    for check in checks:
        if check.supported:
            supported += 1
            continue
        issues.append(
            ValidationIssue(
                location=location,
                message=(
                    f"Unsupported {check.claim.claim_type.replace('_', ' ')} claim: "
                    f"{check.claim.text}"
                ),
                claim_type=check.claim.claim_type,
                source=best,
                evidence_text=check.claim.text,
            )
        )
    if require_trace and text.strip() and not issues:
        if score < 0.35:
            review.append(
                ReviewItem(
                    location=location,
                    claim=text,
                    claim_type="traceability",
                    reason=(
                        "This generated text cannot be traced closely to a single source "
                        "bullet. Individual claims are supported, but a human should review it."
                    ),
                    source=best,
                )
            )
        elif score < 0.55:
            review.append(
                ReviewItem(
                    location=location,
                    claim=text,
                    claim_type="paraphrase",
                    reason=(
                        "This is a paraphrase of source evidence. Claims are supported, "
                        "but the wording should be reviewed."
                    ),
                    source=best,
                )
            )
    return issues, len(checks), supported, review


def _technologies_issues(
    values: list[str],
    sources: list[str],
    *,
    location: str,
) -> list[ValidationIssue]:
    if not values:
        return []
    issues, _, _, _ = _claim_issues(
        " ".join(values),
        sources,
        location=location,
        require_trace=False,
    )
    return issues


def validate_resume(
    resume: TailoredResume,
    profile: CandidateProfile,
    job: JobDescription,
) -> ValidationReport:
    issues: list[ValidationIssue] = []
    review_items: list[ReviewItem] = []
    checked_claims = 0
    supported_claims = 0
    allowed = _allowed_skills(profile)
    year_tokens = _experience_year_tokens(profile)

    if not resume.summary.strip():
        issues.append(ValidationIssue(location="summary", message="Summary is empty."))
    else:
        summary_issues, checked, supported, review = _claim_issues(
            resume.summary,
            _source_texts(profile),
            location="summary",
            allowed_numbers=year_tokens,
            require_trace=False,
        )
        issues.extend(summary_issues)
        review_items.extend(review)
        checked_claims += checked
        supported_claims += supported
    if re.search(r"\b(i|me|my|we|our)\b", resume.summary, re.IGNORECASE):
        issues.append(
            ValidationIssue(
                severity="warning",
                location="summary",
                message="Summary uses first-person language; ATS resumes usually avoid it.",
            )
        )

    if resume.header.title:
        title_issues, checked, supported, review = _claim_issues(
            resume.header.title,
            [
                profile.identity.headline,
                *(entry.role for entry in profile.experience),
            ],
            location="header/title",
            include_title=True,
            require_trace=False,
        )
        issues.extend(title_issues)
        review_items.extend(review)
        checked_claims += checked
        supported_claims += supported

    for group in resume.skills:
        for item in group.items:
            if not _skill_supported(item, allowed):
                issues.append(
                    ValidationIssue(
                        location=f"skills/{group.category}",
                        message=f"Unsupported skill: {item}",
                        claim_type="technology",
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
                    claim_type="title",
                )
            )
            continue
        if entry.start and source.start and entry.start != source.start:
            issues.append(
                ValidationIssue(
                    location=f"experience/{entry.company}",
                    message=f"Start date {entry.start} differs from profile {source.start}.",
                    claim_type="date",
                    source=source.company,
                )
            )
        if entry.end and source.end and entry.end != source.end:
            issues.append(
                ValidationIssue(
                    location=f"experience/{entry.company}",
                    message=f"End date {entry.end} differs from profile {source.end}.",
                    claim_type="date",
                    source=source.company,
                )
            )
        sources = _experience_sources(source)
        issues.extend(
            _technologies_issues(
                entry.technologies,
                sources,
                location=f"experience/{entry.company}/technologies",
            )
        )
        for bullet in entry.bullets:
            bullet_issues, checked, supported, review = _claim_issues(
                bullet,
                sources,
                location=f"experience/{entry.company}",
            )
            issues.extend(bullet_issues)
            review_items.extend(review)
            checked_claims += checked
            supported_claims += supported

    for project in resume.projects:
        project_source = find_project(profile, project.name)
        if project_source is None:
            issues.append(
                ValidationIssue(
                    location=f"project/{project.name}",
                    message="Project does not match the portfolio profile.",
                    claim_type="title",
                )
            )
            continue
        sources = _project_sources(project_source)
        issues.extend(
            _technologies_issues(
                project.technologies,
                sources,
                location=f"project/{project.name}/technologies",
            )
        )
        for bullet in project.bullets:
            bullet_issues, checked, supported, review = _claim_issues(
                bullet,
                sources,
                location=f"project/{project.name}",
            )
            issues.extend(bullet_issues)
            review_items.extend(review)
            checked_claims += checked
            supported_claims += supported

    for cert in resume.certifications:
        if find_certification(profile, cert) is None:
            issues.append(
                ValidationIssue(
                    location="certifications",
                    message=f"Certification is not in the profile: {cert}",
                    claim_type="certification",
                )
            )

    allowed_awards = " ".join(profile.awards).casefold()
    for award in resume.awards:
        if award.casefold() not in allowed_awards:
            issues.append(
                ValidationIssue(
                    location="awards",
                    message=f"Unsupported award: {award}",
                    claim_type="award",
                    source="Awards",
                )
            )

    return ValidationReport(
        valid=not any(issue.severity == "error" for issue in issues),
        issues=issues,
        review_required=bool(review_items),
        review_items=review_items,
        checked_claims=checked_claims,
        supported_claims=supported_claims,
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
    summary_issues, _, _, _ = _claim_issues(
        summary,
        _source_texts(profile),
        location="summary",
        allowed_numbers=_experience_year_tokens(profile),
        require_trace=False,
    )
    if summary_issues:
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
        sources = _experience_sources(source)
        bullets = []
        if generated_experience:
            for bullet in generated_experience.bullets:
                bullet_issues, _, _, _ = _claim_issues(
                    bullet,
                    sources,
                    location=f"experience/{source.company}",
                )
                if bullet_issues:
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
        sources = _project_sources(project_source)
        bullets = []
        for bullet in generated_project.bullets:
            bullet_issues, _, _, _ = _claim_issues(
                bullet,
                sources,
                location=f"project/{project_source.name}",
            )
            if bullet_issues:
                continue
            bullets.append(bullet)
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
