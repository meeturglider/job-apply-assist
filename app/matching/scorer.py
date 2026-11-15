from __future__ import annotations

import re

from app.matching.matcher import (
    keyword_presence,
    match_requirements,
)
from app.models import (
    AnalysisResult,
    CandidateProfile,
    JobDescription,
    ScoreBreakdown,
    SkillMatch,
)
from app.skills import lexicon


def _norm(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", value.casefold())


def _similar(left: str, right: str) -> bool:
    left_key, right_key = _norm(left), _norm(right)
    if not left_key or not right_key:
        return False
    if left_key in right_key or right_key in left_key:
        return True
    left_tokens = set(re.findall(r"[a-z0-9]+", left.casefold()))
    right_tokens = set(re.findall(r"[a-z0-9]+", right.casefold()))
    if not left_tokens or not right_tokens:
        return False
    return len(left_tokens & right_tokens) / len(left_tokens | right_tokens) >= 0.6


def _requirement_weights(job: JobDescription) -> dict[tuple[str, str], float]:
    weights: dict[tuple[str, str], float] = {}
    for requirement in [*job.must_have, *job.nice_to_have]:
        key = (requirement.importance, lexicon.normalize(requirement.skill))
        weights[key] = max(weights.get(key, 0.0), requirement.weight)
    return weights


def _coverage(
    matches: list[SkillMatch],
    job: JobDescription,
    importance: str,
) -> float:
    weights = _requirement_weights(job)
    relevant = [item for item in matches if item.importance == importance]
    if not relevant:
        return 1.0
    total = 0.0
    earned = 0.0
    for item in relevant:
        label = "must_have" if importance == "must_have" else "nice_to_have"
        weight = weights.get((label, lexicon.normalize(item.skill)), 1.0)
        total += weight
        earned += weight * (
            1.0 if item.status == "matched" else 0.5 if item.status == "partial" else 0.0
        )
    return earned / total if total else 0.0


def _responsibility_score(job: JobDescription, profile: CandidateProfile) -> float:
    if not job.responsibilities:
        return 0.7
    known = {lexicon.canonicalize(skill) for skill in profile.skills}
    covered = 0
    considered = 0
    for responsibility in job.responsibilities:
        found = [canonical for canonical, _ in lexicon.find(responsibility)]
        if not found:
            continue
        considered += 1
        if any(item in known for item in found):
            covered += 1
    if considered == 0:
        return 0.6
    return covered / considered


def _certification_score(job: JobDescription, profile: CandidateProfile) -> float:
    if not job.certifications:
        return 1.0
    candidate_names = [cert.name for cert in profile.certifications]
    covered = 0
    for requirement in job.certifications:
        if any(_similar(requirement.skill, name) for name in candidate_names):
            covered += 1
    return covered / len(job.certifications)


def _seniority_score(job: JobDescription, profile: CandidateProfile) -> float:
    thresholds = {
        "junior": 0,
        "mid": 2,
        "senior": 5,
        "lead": 7,
        "staff": 8,
        "principal": 10,
        "manager": 8,
        "director": 12,
        "unknown": 0,
    }
    threshold = thresholds.get(job.seniority.casefold(), 0)
    if threshold == 0:
        return 0.85
    return min(profile.experience_years / threshold, 1.0)


def _keyword_score(
    job: JobDescription, profile: CandidateProfile
) -> tuple[float, dict[str, bool], float]:
    if not job.keywords:
        return 1.0, {}, 100.0
    total = 0.0
    earned = 0.0
    present: dict[str, bool] = {}
    for keyword, weight in job.keywords.items():
        total += weight
        matched = keyword_presence(keyword, profile)
        present[keyword] = matched
        if matched:
            earned += weight
    ratio = earned / total if total else 0.0
    simple = (
        sum(1 for value in present.values() if value) / len(present) * 100 if present else 100.0
    )
    return ratio, present, round(simple, 1)


def score_label(total: float) -> str:
    if total >= 85:
        return "Strong match"
    if total >= 70:
        return "Good match"
    if total >= 55:
        return "Moderate match"
    return "Stretch / low match"


def _recommendations(
    matches: list[SkillMatch],
    job: JobDescription,
    profile: CandidateProfile,
    total: float,
) -> list[str]:
    recommendations: list[str] = []
    matched = [
        item.skill
        for item in matches
        if item.status == "matched" and item.category not in {"Education", "Experience"}
    ]
    partial = [
        item.skill
        for item in matches
        if item.status == "partial" and item.category not in {"Education", "Experience"}
    ]
    missing_must = [
        item.skill
        for item in matches
        if item.importance == "must_have" and item.status == "missing"
    ]
    missing_nice = [
        item.skill
        for item in matches
        if item.importance == "nice_to_have" and item.status == "missing"
    ]

    if total >= 85:
        recommendations.append(
            "Strong fit. Apply with a resume that leads with the matched core skills."
        )
    elif total >= 70:
        recommendations.append(
            "Good fit. Emphasize matched evidence and address partial gaps honestly."
        )
    elif total >= 55:
        recommendations.append(
            "Moderate fit. Tailor aggressively and expect the ATS filter to be competitive."
        )
    else:
        recommendations.append(
            "Low match. Apply only if the role is strategically important to you."
        )

    if matched:
        recommendations.append("Lead with: " + ", ".join(matched[:6]) + ".")
    if partial:
        recommendations.append("Position as adjacent, not expert: " + ", ".join(partial[:6]) + ".")
    if missing_must:
        recommendations.append(
            "Do not claim these missing must-haves: "
            + ", ".join(missing_must[:8])
            + ". They should stay out of the resume unless you have real evidence."
        )
    if missing_nice:
        recommendations.append(
            "Optional gaps: "
            + ", ".join(missing_nice[:6])
            + ". Use a cover letter or learning plan only if truthful."
        )
    if job.certifications:
        cert_names = [item.name for item in profile.certifications]
        recommendations.append(
            "Certifications mentioned in the JD: "
            + ", ".join(item.skill for item in job.certifications[:4])
            + ". Your profile lists: "
            + ", ".join(cert_names[:4])
            + "."
        )
    related_projects = [
        project.name
        for project in profile.projects
        if any(
            keyword_presence(item.skill, _project_profile(profile, project))
            for item in job.must_have[:6]
        )
    ]
    if related_projects:
        recommendations.append(
            "Relevant portfolio projects to surface: " + ", ".join(related_projects[:3]) + "."
        )
    return recommendations


def _project_profile(profile: CandidateProfile, project: object) -> CandidateProfile:
    from app.models import ProjectEntry

    if not isinstance(project, ProjectEntry):
        return profile
    return profile.model_copy(
        update={"skills": {"project": []}, "projects": [project], "experience": []}
    )


def analyze_match(job: JobDescription, profile: CandidateProfile) -> AnalysisResult:
    matches = match_requirements(job, profile)
    must = _coverage(matches, job, "must_have")
    nice = _coverage(matches, job, "nice_to_have")
    responsibilities = _responsibility_score(job, profile)
    certifications = _certification_score(job, profile)
    seniority = _seniority_score(job, profile)
    keywords, keyword_coverage, keyword_pct = _keyword_score(job, profile)

    breakdown = ScoreBreakdown(
        must_have=round(must * 55, 1),
        nice_to_have=round(nice * 15, 1),
        responsibilities=round(responsibilities * 15, 1),
        certifications=round(certifications * 5, 1),
        seniority=round(seniority * 5, 1),
        keywords=round(keywords * 5, 1),
    )
    breakdown.total = round(
        breakdown.must_have
        + breakdown.nice_to_have
        + breakdown.responsibilities
        + breakdown.certifications
        + breakdown.seniority
        + breakdown.keywords,
        1,
    )

    matched = [
        item.skill
        for item in matches
        if item.status == "matched" and item.category not in {"Education", "Experience"}
    ]
    partial = [
        item.skill
        for item in matches
        if item.status == "partial" and item.category not in {"Education", "Experience"}
    ]
    missing_must = [
        item.skill
        for item in matches
        if item.importance == "must_have" and item.status == "missing"
    ]
    missing_nice = [
        item.skill
        for item in matches
        if item.importance == "nice_to_have" and item.status == "missing"
    ]
    warnings = [*job.warnings, *profile.warnings]
    if job.extraction_method == "heuristic":
        warnings.append(
            "Requirements were extracted deterministically. Review the JD analysis before generating."
        )

    return AnalysisResult(
        job=job,
        matches=matches,
        score=breakdown,
        score_label=score_label(breakdown.total),
        matched_skills=matched,
        partial_skills=partial,
        missing_must_have=missing_must,
        missing_nice_to_have=missing_nice,
        keyword_coverage=keyword_coverage,
        keyword_coverage_pct=keyword_pct,
        recommendations=_recommendations(matches, job, profile, breakdown.total),
        warnings=warnings,
    )
