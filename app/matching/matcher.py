from __future__ import annotations

import re
from collections import defaultdict

from app.models import CandidateProfile, Evidence, JobDescription, Requirement, SkillMatch
from app.skills import lexicon

RELATED_SKILLS: dict[str, set[str]] = {
    "Argo CD": {"GitOps", "Kubernetes", "CI/CD"},
    "GitHub Actions": {"CI/CD", "Git", "Jenkins"},
    "GitLab CI": {"CI/CD", "Git"},
    "Cloud Build": {"CI/CD", "Google Cloud Platform"},
    "Jenkins": {"CI/CD", "Git", "CloudBees Jenkins"},
    "GitOps": {"Argo CD", "Kubernetes", "CI/CD"},
    "Splunk": {"Logging", "Observability"},
    "Prometheus": {"Monitoring", "Observability"},
    "Grafana": {"Monitoring", "Observability"},
    "GKE": {"Kubernetes", "Google Cloud Platform"},
    "EKS": {"Kubernetes", "Amazon Web Services"},
    "AKS": {"Kubernetes", "Microsoft Azure"},
    "Cloud Run": {"Serverless", "Google Cloud Platform", "Docker"},
    "Vertex AI": {"Generative AI", "Gemini", "AI Infrastructure"},
    "Gemini": {"Generative AI", "Vertex AI"},
    "RAG": {"Generative AI", "Large Language Models"},
    "Agentic AI": {"Large Language Models", "AI Infrastructure"},
    "Ollama": {"Large Language Models", "AI Infrastructure"},
    "FinOps": {"Cloud Operations", "Cloud Architecture"},
    "DevSecOps": {"Cloud Security", "CI/CD"},
    "OpenStack": {"Cloud"},
    "Spanner": {"SQL", "Databases"},
    "Pub/Sub": {"Messaging", "Distributed Systems"},
    "Envoy": {"Cloud Networking", "Microservices"},
    "Consul": {"Cloud Networking", "Microservices"},
    "Cloud Migration": {"Cloud Architecture", "Google Cloud Platform", "Amazon Web Services"},
    "AI Infrastructure": {"Generative AI", "Large Language Models", "Kubernetes", "Docker"},
}

CATEGORY_ALIASES = {
    "cloud": {"Cloud", "Architecture"},
    "security": {"Security", "Compliance"},
    "devops": {"DevOps", "Infrastructure as Code"},
    "ai": {"AI", "AI Platform"},
    "programming": {"Programming"},
    "platform engineering": {"Platform Engineering"},
    "operations": {"Operations", "Reliability"},
    "networking": {"Networking"},
}


def _search_aliases(text: str, aliases: list[str]) -> bool:
    for alias in sorted(set(aliases), key=len, reverse=True):
        pattern = re.escape(alias).replace(r"\ ", r"[\s-]+")
        if re.search(
            rf"(?<![a-z0-9]){pattern}(?![a-z0-9])",
            text,
            re.IGNORECASE,
        ):
            return True
    return False


def _explain(requirement: Requirement, source: Requirement | None = None) -> str:
    if source is None:
        return "Direct evidence found in the resume or portfolio."
    return f"Adjacent evidence found: {source.skill}."


def _profile_canonical_skills(profile: CandidateProfile) -> dict[str, list[Evidence]]:
    return profile.skills


def _find_direct(requirement: Requirement, profile: CandidateProfile) -> list[Evidence]:
    canonical = lexicon.canonicalize(requirement.skill)
    aliases = lexicon.aliases_for(canonical)
    if canonical not in profile.skills:
        aliases.extend([requirement.skill])
    matches: list[Evidence] = []
    seen: set[str] = set()
    for _canonical, items in profile.skills.items():
        for item in items:
            if _search_aliases(item.text, aliases):
                marker = f"{item.source}|{item.text}"
                if marker not in seen:
                    matches.append(item)
                    seen.add(marker)
    if not matches:
        for item in profile.all_evidence():
            if _search_aliases(item.text, aliases):
                marker = f"{item.source}|{item.text}"
                if marker not in seen:
                    matches.append(item)
                    seen.add(marker)
    return matches[:5]


def _related_candidates(
    requirement: Requirement, profile: CandidateProfile
) -> list[tuple[str, list[Evidence]]]:
    canonical = lexicon.canonicalize(requirement.skill)
    related = set(RELATED_SKILLS.get(canonical, set()))
    related.update(RELATED_SKILLS.get(requirement.skill, set()))
    category = (requirement.category or lexicon.category_for(canonical)).casefold()
    related.update(CATEGORY_ALIASES.get(category, set()))
    results: list[tuple[str, list[Evidence]]] = []
    seen: set[str] = set()
    for name in related:
        if name == canonical or name in seen:
            continue
        items = profile.skills.get(name)
        if items:
            results.append((name, items[:2]))
            seen.add(name)
    wanted_categories = CATEGORY_ALIASES.get(category, set())
    for name, items in profile.skills.items():
        if name in seen:
            continue
        if lexicon.category_for(name) in wanted_categories:
            results.append((name, items[:2]))
            seen.add(name)
    return results


def _education_evidence(profile: CandidateProfile) -> list[Evidence]:
    return [
        Evidence(
            source="Education",
            text=", ".join(part for part in (entry.degree, entry.institution) if part),
        )
        for entry in profile.education
        if entry.degree
    ]


def _has_master_degree(profile: CandidateProfile) -> bool:
    return any(
        re.search(r"\b(master|mba|ph\.?d|doctorate)\b", entry.degree, re.IGNORECASE)
        for entry in profile.education
    )


def _has_bachelor_degree(profile: CandidateProfile) -> bool:
    return any(
        re.search(
            r"\b(bachelor|b\.?\s?tech|b\.?\s?e\.?|undergraduate|degree)\b",
            entry.degree,
            re.IGNORECASE,
        )
        for entry in profile.education
    )


def _education_match(requirement: Requirement, profile: CandidateProfile) -> SkillMatch:
    text = requirement.skill
    lowered = text.casefold()
    evidence = _education_evidence(profile)
    if not evidence:
        return SkillMatch(
            skill=text,
            category="Education",
            importance=requirement.importance,
            status="missing",
            confidence=0.0,
            note="No education evidence found in the master resume.",
        )

    wants_advanced = bool(re.search(r"\b(master|mba|ph\.?d|doctorate)\b", lowered))
    wants_bachelor = bool(
        re.search(r"\b(bachelor|b\.?\s?tech|b\.?\s?e\.?|undergraduate|degree)\b", lowered)
    )
    related = bool(re.search(r"\b(related|equivalent|adjacent)\b", lowered))

    if wants_advanced and not _has_master_degree(profile):
        return SkillMatch(
            skill=text,
            category="Education",
            importance=requirement.importance,
            status="partial",
            evidence=evidence[:2],
            confidence=0.5,
            note="Bachelor's degree found; the role asks for a more advanced degree.",
        )

    if (wants_bachelor or wants_advanced) and not _has_bachelor_degree(profile):
        return SkillMatch(
            skill=text,
            category="Education",
            importance=requirement.importance,
            status="partial",
            evidence=evidence[:2],
            confidence=0.5,
            note="Education is present, but the degree level could not be confirmed.",
        )

    field_is_cs = bool(re.search(r"computer science|\bcs\b|software engineering", lowered))
    degree_text = " ".join(entry.degree for entry in profile.education).casefold()
    has_cs_field = any(
        term in degree_text
        for term in ("computer science", "software engineering", "information technology", " cs ")
    )
    if field_is_cs and has_cs_field:
        return SkillMatch(
            skill=text,
            category="Education",
            importance=requirement.importance,
            status="matched",
            evidence=evidence[:2],
            confidence=0.95,
            note="Degree field directly matches the requirement.",
        )
    if field_is_cs and not related:
        return SkillMatch(
            skill=text,
            category="Education",
            importance=requirement.importance,
            status="partial",
            evidence=evidence[:2],
            confidence=0.6,
            note="Engineering degree is adjacent, but the JD asks for an exact CS field.",
        )

    note = "Degree requirement satisfied."
    if field_is_cs and related:
        note = "Engineering degree satisfies the related-field degree requirement."
    return SkillMatch(
        skill=text,
        category="Education",
        importance=requirement.importance,
        status="matched",
        evidence=evidence[:2],
        confidence=0.9,
        note=note,
    )


def _is_education_text(value: str) -> bool:
    return bool(
        re.search(
            r"\b(bachelor|master|mba|ph\.?d|doctorate|degree|b\.?\s?tech|m\.?\s?tech)\b",
            value,
            re.IGNORECASE,
        )
    )


def _required_years(text: str) -> int | None:
    matches = re.findall(r"\b(\d{1,2})\s*\+?\s*(?:years?|yrs?)\b", text, re.IGNORECASE)
    return min(int(value) for value in matches) if matches else None


def _experience_match(requirement: Requirement, profile: CandidateProfile) -> SkillMatch:
    text = requirement.skill
    if not profile.experience:
        return SkillMatch(
            skill=text,
            category="Experience",
            importance=requirement.importance,
            status="missing",
            confidence=0.0,
            note="No professional experience found in the master resume.",
        )

    years = round(profile.experience_years)
    earliest = profile.experience[-1]
    evidence = Evidence(
        source="Experience",
        text=(
            f"{profile.experience_years:.1f} years across {len(profile.experience)} roles"
            + (f" since {earliest.start}." if earliest.start else ".")
        ),
    )
    required = _required_years(text)
    if required is None:
        return SkillMatch(
            skill=text,
            category="Experience",
            importance=requirement.importance,
            status="matched",
            evidence=[evidence],
            confidence=0.8,
            note="Professional experience is present.",
        )
    if years >= required:
        return SkillMatch(
            skill=text,
            category="Experience",
            importance=requirement.importance,
            status="matched",
            evidence=[evidence],
            confidence=0.9,
            note=f"Computed experience rounds to {years} years.",
        )
    if years + 1 >= required:
        return SkillMatch(
            skill=text,
            category="Experience",
            importance=requirement.importance,
            status="partial",
            evidence=[evidence],
            confidence=0.5,
            note=f"Computed experience is {profile.experience_years:.1f} years.",
        )
    return SkillMatch(
        skill=text,
        category="Experience",
        importance=requirement.importance,
        status="missing",
        evidence=[evidence],
        confidence=0.0,
        note=f"Computed experience is {profile.experience_years:.1f} years.",
    )


def match_requirements(job: JobDescription, profile: CandidateProfile) -> list[SkillMatch]:
    matches: list[SkillMatch] = []
    seen: set[str] = set()
    for requirement in [*job.education_requirements, *job.experience_requirements]:
        key = lexicon.normalize(requirement.skill)
        if key in seen:
            continue
        seen.add(key)
        if requirement.category == "Education" or (
            requirement.category != "Experience" and _is_education_text(requirement.skill)
        ):
            matches.append(_education_match(requirement, profile))
        else:
            matches.append(_experience_match(requirement, profile))

    for requirement in [*job.must_have, *job.nice_to_have]:
        key = lexicon.normalize(requirement.skill)
        if key in seen:
            continue
        seen.add(key)
        direct = _find_direct(requirement, profile)
        if direct:
            matches.append(
                SkillMatch(
                    skill=requirement.skill,
                    category=requirement.category,
                    importance=requirement.importance,
                    status="matched",
                    evidence=direct,
                    confidence=0.95,
                    note=_explain(requirement),
                )
            )
            continue
        related = _related_candidates(requirement, profile)
        if related:
            name, evidence = related[0]
            matches.append(
                SkillMatch(
                    skill=requirement.skill,
                    category=requirement.category,
                    importance=requirement.importance,
                    status="partial",
                    evidence=evidence,
                    confidence=0.55,
                    note=_explain(
                        requirement,
                        Requirement(
                            skill=name,
                            category=requirement.category,
                        ),
                    ),
                )
            )
            continue
        matches.append(
            SkillMatch(
                skill=requirement.skill,
                category=requirement.category,
                importance=requirement.importance,
                status="missing",
                confidence=0.0,
                note="No supporting evidence found.",
            )
        )
    return matches


def keyword_presence(keyword: str, profile: CandidateProfile) -> bool:
    canonical = lexicon.canonicalize(keyword)
    if canonical in profile.skills:
        return True
    aliases = lexicon.aliases_for(canonical) if canonical else [keyword]
    return any(_search_aliases(item.text, aliases) for item in profile.all_evidence())


def grouped_matches(matches: list[SkillMatch]) -> dict[str, list[str]]:
    grouped: dict[str, list[str]] = defaultdict(list)
    for item in matches:
        grouped[item.status].append(item.skill)
    return dict(grouped)
