from __future__ import annotations

import re

from app.matching.matcher import (
    entity_evidence_matches,
    entity_for_keyword,
    keyword_presence,
    match_requirements,
)
from app.models import (
    AnalysisResult,
    CandidateProfile,
    EvidenceCoverage,
    JobDescription,
    KeywordSuggestion,
    Requirement,
    ScoreBreakdown,
    ScoreComponent,
    ScoreReport,
    SkillMatch,
)
from app.skills import lexicon

MUST_HAVE_WEIGHT = 50.0
NICE_TO_HAVE_WEIGHT = 15.0
RESPONSIBILITY_WEIGHT = 15.0
EXPERIENCE_WEIGHT = 5.0
EDUCATION_WEIGHT = 5.0
CERTIFICATION_WEIGHT = 5.0
SENIORITY_WEIGHT = 5.0
SUITABILITY_WEIGHT = (
    MUST_HAVE_WEIGHT
    + NICE_TO_HAVE_WEIGHT
    + RESPONSIBILITY_WEIGHT
    + EXPERIENCE_WEIGHT
    + EDUCATION_WEIGHT
    + CERTIFICATION_WEIGHT
    + SENIORITY_WEIGHT
)
NON_SKILL_CATEGORIES = {"Education", "Experience", "Responsibility", "Certification"}


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


def _status_credit(match: SkillMatch | None) -> float:
    if match is None:
        return 0.0
    if match.evidence_class == "contradicted":
        return -0.25
    if match.status == "matched":
        return 1.0
    if match.status == "partial":
        return 0.5
    return 0.0


def _coverage_for_requirements(
    matches: list[SkillMatch],
    requirements: list[Requirement],
) -> tuple[float, int, int]:
    if not requirements:
        return 1.0, 0, 0
    by_key = {lexicon.normalize(item.skill): item for item in matches}
    total = 0.0
    earned = 0.0
    covered = 0
    for requirement in requirements:
        weight = max(requirement.weight, 0.5)
        match = by_key.get(lexicon.normalize(requirement.skill))
        credit = _status_credit(match)
        total += weight
        earned += weight * credit
        if credit > 0:
            covered += 1
    return (earned / total if total else 0.0), covered, len(requirements)


def _category_coverage(
    matches: list[SkillMatch],
    category: str,
) -> tuple[float, int, int]:
    relevant = [item for item in matches if item.category == category]
    if not relevant:
        return 1.0, 0, 0
    covered = sum(1 for item in relevant if _status_credit(item) > 0)
    return sum(_status_credit(item) for item in relevant) / len(relevant), covered, len(relevant)


def _responsibility_score(matches: list[SkillMatch]) -> tuple[float, int, int]:
    relevant = [item for item in matches if item.category == "Responsibility"]
    if not relevant:
        return 1.0, 0, 0
    covered = sum(1 for item in relevant if _status_credit(item) > 0)
    return sum(_status_credit(item) for item in relevant) / len(relevant), covered, len(relevant)


def _certification_score(job: JobDescription, profile: CandidateProfile) -> tuple[float, int, int]:
    if not job.certifications:
        return 1.0, 0, 0
    candidate_names = [cert.name for cert in profile.certifications]
    covered = 0
    for requirement in job.certifications:
        if any(_similar(requirement.skill, name) for name in candidate_names):
            covered += 1
    return covered / len(job.certifications), covered, len(job.certifications)


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
    return ratio, present, round(max(0.0, min(simple, 100.0)), 1)


def _keyword_suggestions(job: JobDescription, profile: CandidateProfile) -> list[KeywordSuggestion]:
    suggestions: list[KeywordSuggestion] = []
    seen: set[tuple[str, str]] = set()
    for keyword in job.keywords:
        matches = entity_evidence_matches(keyword, profile)
        if not matches:
            continue
        canonical = entity_for_keyword(keyword) or keyword
        source = matches[0].source
        marker = (canonical.casefold(), source.casefold())
        if marker in seen:
            continue
        seen.add(marker)
        suggestions.append(
            KeywordSuggestion(
                keyword=canonical,
                source=source,
                suggestion=(
                    f'Make the "{canonical}" connection explicit in your profile, for example '
                    f'"{source} ({canonical})" in the employer line or "{canonical}" in the '
                    "role summary."
                ),
                reason=f'"{source}" is a known {canonical} entity in the profile alias map.',
            )
        )
    return suggestions


def _component(
    ratio: float,
    maximum: float,
    explanation: str,
) -> ScoreComponent:
    bounded = max(0.0, min(ratio, 1.0))
    return ScoreComponent(
        value=round(bounded * maximum, 1),
        maximum=maximum,
        percentage=round(bounded * 100, 1),
        explanation=explanation,
    )


def _coverage_explanation(covered: int, total: int, label: str) -> str:
    if total == 0:
        return f"No explicit {label} requirement; neutral credit applied."
    return (
        f"{covered} of {total} {label} requirements have supporting evidence. "
        "Direct evidence counts fully; adjacent evidence counts half."
    )


def _evidence_coverage(matches: list[SkillMatch]) -> tuple[EvidenceCoverage, ScoreComponent]:
    counts = {
        "direct": 0,
        "adjacent": 0,
        "unverified": 0,
        "contradicted": 0,
    }
    for match in matches:
        counts[match.evidence_class] += 1
    total = len(matches)
    if total:
        coverage = (counts["direct"] + counts["adjacent"]) / total
        quality = (counts["direct"] + counts["adjacent"] * 0.5) / total
    else:
        coverage = 1.0
        quality = 1.0
    coverage_pct = round(coverage * 100, 1)
    quality_pct = round(quality * 100, 1)
    explanation = (
        f"{counts['direct']} direct, {counts['adjacent']} adjacent, "
        f"{counts['unverified']} unverified, {counts['contradicted']} contradicted. "
        "Unverified means no evidence was found, not that the candidate lacks the skill."
        if total
        else "No requirements were available for evidence coverage."
    )
    evidence = EvidenceCoverage(
        total=total,
        direct=counts["direct"],
        adjacent=counts["adjacent"],
        unverified=counts["unverified"],
        contradicted=counts["contradicted"],
        coverage_pct=coverage_pct,
        quality_pct=quality_pct,
        explanation=explanation,
    )
    component = ScoreComponent(
        value=coverage_pct,
        maximum=100,
        percentage=coverage_pct,
        explanation=explanation,
    )
    return evidence, component


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
        if item.status == "matched" and item.category not in NON_SKILL_CATEGORIES
    ]
    partial = [
        item.skill
        for item in matches
        if item.status == "partial" and item.category not in NON_SKILL_CATEGORIES
    ]
    missing_must = [
        item.skill
        for item in matches
        if item.importance == "must_have"
        and item.status == "missing"
        and item.category not in NON_SKILL_CATEGORIES
    ]
    missing_nice = [
        item.skill
        for item in matches
        if item.importance == "nice_to_have"
        and item.status == "missing"
        and item.category not in NON_SKILL_CATEGORIES
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
            "No evidence found for these must-haves: "
            + ", ".join(missing_must[:8])
            + ". Do not claim them unless you can add real evidence."
        )
    if missing_nice:
        recommendations.append(
            "Optional gaps without evidence: "
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

    must_ratio, must_covered, must_total = _coverage_for_requirements(matches, job.must_have)
    nice_ratio, nice_covered, nice_total = _coverage_for_requirements(matches, job.nice_to_have)
    responsibility_ratio, responsibility_covered, responsibility_total = _responsibility_score(
        matches
    )
    experience_ratio, experience_covered, experience_total = _category_coverage(
        matches, "Experience"
    )
    education_ratio, education_covered, education_total = _category_coverage(matches, "Education")
    certification_ratio, certification_covered, certification_total = _certification_score(
        job, profile
    )
    seniority_ratio = _seniority_score(job, profile)
    keywords_ratio, keyword_coverage, keyword_pct = _keyword_score(job, profile)

    components = {
        "must_have": _component(
            must_ratio,
            MUST_HAVE_WEIGHT,
            _coverage_explanation(must_covered, must_total, "must-have"),
        ),
        "nice_to_have": _component(
            nice_ratio,
            NICE_TO_HAVE_WEIGHT,
            _coverage_explanation(nice_covered, nice_total, "nice-to-have"),
        ),
        "responsibilities": _component(
            responsibility_ratio,
            RESPONSIBILITY_WEIGHT,
            _coverage_explanation(responsibility_covered, responsibility_total, "responsibility"),
        ),
        "experience": _component(
            experience_ratio,
            EXPERIENCE_WEIGHT,
            _coverage_explanation(experience_covered, experience_total, "experience"),
        ),
        "education": _component(
            education_ratio,
            EDUCATION_WEIGHT,
            _coverage_explanation(education_covered, education_total, "education"),
        ),
        "certifications": _component(
            certification_ratio,
            CERTIFICATION_WEIGHT,
            _coverage_explanation(certification_covered, certification_total, "certification"),
        ),
        "seniority": _component(
            seniority_ratio,
            SENIORITY_WEIGHT,
            (
                f"Computed experience supports the detected {job.seniority or 'unknown'} "
                "seniority level."
            ),
        ),
    }
    suitability_total = round(sum(item.value for item in components.values()), 1)
    suitability_percentage = round(suitability_total / SUITABILITY_WEIGHT * 100, 1)
    suitability = ScoreComponent(
        value=suitability_total,
        maximum=SUITABILITY_WEIGHT,
        percentage=suitability_percentage,
        explanation=(
            "Candidate suitability scores only verified requirement coverage. "
            "Keyword presence and evidence quality are reported separately."
        ),
    )
    evidence, evidence_component = _evidence_coverage(matches)
    keyword_component = ScoreComponent(
        value=keyword_pct,
        maximum=100,
        percentage=keyword_pct,
        explanation=(
            f"{keyword_pct}% of JD keywords appear in profile evidence. "
            "Generated-resume keyword coverage is measured separately after rendering."
        ),
    )
    score_report = ScoreReport(
        candidate_suitability=suitability,
        evidence_coverage=evidence_component,
        keyword_coverage=keyword_component,
        components=components,
        evidence=evidence,
        methodology=(
            "Suitability weights: must-have 50, nice-to-have 15, responsibilities 15, "
            "experience 5, education 5, certifications 5, seniority 5. "
            "Evidence coverage and keyword coverage are independent metrics."
        ),
    )

    breakdown = ScoreBreakdown(
        must_have=components["must_have"].value,
        nice_to_have=components["nice_to_have"].value,
        responsibilities=components["responsibilities"].value,
        certifications=components["certifications"].value,
        seniority=components["seniority"].value,
        keywords=round(keywords_ratio * 5, 1),
        total=suitability_total,
    )

    matched = [
        item.skill
        for item in matches
        if item.status == "matched" and item.category not in NON_SKILL_CATEGORIES
    ]
    partial = [
        item.skill
        for item in matches
        if item.status == "partial" and item.category not in NON_SKILL_CATEGORIES
    ]
    missing_must = [
        item.skill
        for item in matches
        if item.importance == "must_have"
        and item.status == "missing"
        and item.category not in NON_SKILL_CATEGORIES
    ]
    missing_nice = [
        item.skill
        for item in matches
        if item.importance == "nice_to_have"
        and item.status == "missing"
        and item.category not in NON_SKILL_CATEGORIES
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
        score_report=score_report,
        score_label=score_label(suitability_total),
        matched_skills=matched,
        partial_skills=partial,
        missing_must_have=missing_must,
        missing_nice_to_have=missing_nice,
        keyword_coverage=keyword_coverage,
        keyword_coverage_pct=keyword_pct,
        keyword_suggestions=_keyword_suggestions(job, profile),
        recommendations=_recommendations(matches, job, profile, suitability_total),
        warnings=warnings,
    )
