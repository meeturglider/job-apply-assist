from __future__ import annotations

import re
from collections import defaultdict

from app.matching.dimensions import classify_dimension, dimension_hits, tokens
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

NEGATION_RE = re.compile(
    r"\b(?:no|not|never|without|lack(?:s|ed|ing)?|unfamiliar with|inexperienced with)\b",
    re.IGNORECASE,
)
NOT_ONLY_RE = re.compile(r"\bnot only\b", re.IGNORECASE)


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


def _match(
    requirement: Requirement,
    *,
    status: str,
    evidence_class: str,
    evidence: list[Evidence] | None = None,
    confidence: float,
    note: str,
    category: str | None = None,
) -> SkillMatch:
    return SkillMatch(
        skill=requirement.skill,
        category=category if category is not None else requirement.category,
        importance=requirement.importance,
        status=status,  # type: ignore[arg-type]
        evidence_class=evidence_class,  # type: ignore[arg-type]
        evidence=evidence or [],
        confidence=confidence,
        note=note,
        requirement=requirement.skill,
        explanation=note,
    )


def _negative_mentions(text: str, aliases: list[str]) -> bool:
    for alias in sorted(set(aliases), key=len, reverse=True):
        pattern = re.escape(alias).replace(r"\ ", r"[\s-]+")
        for match in re.finditer(
            rf"(?<![a-z0-9]){pattern}(?![a-z0-9])",
            text,
            re.IGNORECASE,
        ):
            window = text[max(0, match.start() - 60) : min(len(text), match.end() + 30)]
            if NEGATION_RE.search(window) and not NOT_ONLY_RE.search(window):
                return True
    return False


def _find_contradiction(requirement: Requirement, profile: CandidateProfile) -> list[Evidence]:
    canonical = lexicon.canonicalize(requirement.skill)
    aliases = lexicon.aliases_for(canonical)
    if canonical not in profile.skills:
        aliases.append(requirement.skill)
    matches: list[Evidence] = []
    for item in profile.all_evidence():
        if _negative_mentions(item.text, aliases):
            matches.append(item)
    return matches[:3]


def _find_direct(requirement: Requirement, profile: CandidateProfile) -> list[Evidence]:
    canonical = lexicon.canonicalize(requirement.skill)
    aliases = lexicon.aliases_for(canonical)
    if canonical not in profile.skills:
        aliases.extend([requirement.skill])
    matches: list[Evidence] = []
    seen: set[str] = set()
    for _canonical, items in profile.skills.items():
        for item in items:
            if _search_aliases(item.text, aliases) and not _negative_mentions(item.text, aliases):
                marker = f"{item.source}|{item.text}"
                if marker not in seen:
                    matches.append(item)
                    seen.add(marker)
    if not matches:
        for item in profile.all_evidence():
            if _search_aliases(item.text, aliases) and not _negative_mentions(item.text, aliases):
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
        return _match(
            requirement,
            status="missing",
            evidence_class="unverified",
            confidence=0.0,
            note="No education evidence found in the master resume.",
            category="Education",
        )

    wants_advanced = bool(re.search(r"\b(master|mba|ph\.?d|doctorate)\b", lowered))
    wants_bachelor = bool(
        re.search(r"\b(bachelor|b\.?\s?tech|b\.?\s?e\.?|undergraduate|degree)\b", lowered)
    )
    related = bool(re.search(r"\b(related|equivalent|adjacent)\b", lowered))

    if wants_advanced and not _has_master_degree(profile):
        return _match(
            requirement,
            status="partial",
            evidence_class="adjacent",
            evidence=evidence[:2],
            confidence=0.5,
            note="Bachelor's degree found; the role asks for a more advanced degree.",
            category="Education",
        )

    if (wants_bachelor or wants_advanced) and not _has_bachelor_degree(profile):
        return _match(
            requirement,
            status="partial",
            evidence_class="adjacent",
            evidence=evidence[:2],
            confidence=0.5,
            note="Education is present, but the degree level could not be confirmed.",
            category="Education",
        )

    field_is_cs = bool(re.search(r"computer science|\bcs\b|software engineering", lowered))
    degree_text = " ".join(entry.degree for entry in profile.education).casefold()
    has_cs_field = any(
        term in degree_text
        for term in ("computer science", "software engineering", "information technology", " cs ")
    )
    if field_is_cs and has_cs_field:
        return _match(
            requirement,
            status="matched",
            evidence_class="direct",
            evidence=evidence[:2],
            confidence=0.95,
            note="Degree field directly matches the requirement.",
            category="Education",
        )
    if field_is_cs and not related:
        return _match(
            requirement,
            status="partial",
            evidence_class="adjacent",
            evidence=evidence[:2],
            confidence=0.6,
            note="Engineering degree is adjacent, but the JD asks for an exact CS field.",
            category="Education",
        )

    note = "Degree requirement satisfied."
    if field_is_cs and related:
        note = "Engineering degree satisfies the related-field degree requirement."
    return _match(
        requirement,
        status="matched",
        evidence_class="direct",
        evidence=evidence[:2],
        confidence=0.9,
        note=note,
        category="Education",
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
        return _match(
            requirement,
            status="missing",
            evidence_class="contradicted",
            confidence=0.2,
            note="The profile contains no professional experience entries.",
            category="Experience",
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
        return _match(
            requirement,
            status="matched",
            evidence_class="direct",
            evidence=[evidence],
            confidence=0.8,
            note="Professional experience is present.",
            category="Experience",
        )
    if years >= required:
        return _match(
            requirement,
            status="matched",
            evidence_class="direct",
            evidence=[evidence],
            confidence=0.9,
            note=f"Computed experience rounds to {years} years.",
            category="Experience",
        )
    if years + 1 >= required:
        return _match(
            requirement,
            status="partial",
            evidence_class="adjacent",
            evidence=[evidence],
            confidence=0.5,
            note=f"Computed experience is {profile.experience_years:.1f} years.",
            category="Experience",
        )
    return _match(
        requirement,
        status="missing",
        evidence_class="contradicted",
        evidence=[evidence],
        confidence=0.2,
        note=f"Computed experience is {profile.experience_years:.1f} years.",
        category="Experience",
    )


def _responsibility_requirements(job: JobDescription) -> list[Requirement]:
    if job.responsibility_requirements:
        return job.responsibility_requirements
    return [
        Requirement(
            skill=text,
            category="Responsibility",
            importance="must_have",
            weight=1.0,
            dimension=classify_dimension(text),
        )
        for text in job.responsibilities
        if text.strip()
    ]


def _responsibility_match(requirement: Requirement, profile: CandidateProfile) -> SkillMatch:
    dimension = requirement.dimension or classify_dimension(requirement.skill)
    requirement_tokens = tokens(requirement.skill)
    requirement_skills = {
        lexicon.canonicalize(canonical) for canonical, _ in lexicon.find(requirement.skill)
    }
    direct: list[tuple[float, Evidence]] = []
    adjacent: list[tuple[float, Evidence]] = []
    for evidence in profile.all_evidence():
        evidence_tokens = tokens(evidence.text)
        shared = requirement_tokens & evidence_tokens
        overlap = len(shared) / len(requirement_tokens) if requirement_tokens else 0.0
        evidence_skills = {
            lexicon.canonicalize(canonical) for canonical, _ in lexicon.find(evidence.text)
        }
        skill_hit = bool(requirement_skills & evidence_skills)
        hits = dimension_hits(evidence.text, dimension)
        score = overlap + min(hits, 2) * 0.15 + (0.2 if skill_hit else 0.0)
        if hits and (skill_hit or overlap >= 0.2):
            direct.append((score, evidence))
        elif hits or skill_hit or overlap >= 0.3:
            adjacent.append((score, evidence))

    if direct:
        direct.sort(key=lambda item: -item[0])
        return _match(
            requirement,
            status="matched",
            evidence_class="direct",
            evidence=[item[1] for item in direct[:3]],
            confidence=0.85,
            note=f"Direct {dimension.replace('_', ' ')} evidence found in the profile.",
            category="Responsibility",
        )
    if adjacent:
        adjacent.sort(key=lambda item: -item[0])
        return _match(
            requirement,
            status="partial",
            evidence_class="adjacent",
            evidence=[item[1] for item in adjacent[:3]],
            confidence=0.5,
            note=f"Adjacent {dimension.replace('_', ' ')} evidence found; scope may differ.",
            category="Responsibility",
        )
    return _match(
        requirement,
        status="missing",
        evidence_class="unverified",
        confidence=0.0,
        note=f"No {dimension.replace('_', ' ')} evidence found; this is not proof of absence.",
        category="Responsibility",
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
        contradiction = _find_contradiction(requirement, profile)
        direct = _find_direct(requirement, profile)
        if direct:
            matches.append(
                _match(
                    requirement,
                    status="matched",
                    evidence_class="direct",
                    evidence=direct,
                    confidence=0.95,
                    note="Direct evidence found in the resume or portfolio.",
                )
            )
            continue
        if contradiction:
            matches.append(
                _match(
                    requirement,
                    status="missing",
                    evidence_class="contradicted",
                    evidence=contradiction,
                    confidence=0.2,
                    note="Profile evidence explicitly contradicts this requirement.",
                )
            )
            continue
        related = _related_candidates(requirement, profile)
        if related:
            name, evidence = related[0]
            matches.append(
                _match(
                    requirement,
                    status="partial",
                    evidence_class="adjacent",
                    evidence=evidence,
                    confidence=0.55,
                    note=f"Adjacent evidence found: {name}.",
                )
            )
            continue
        matches.append(
            _match(
                requirement,
                status="missing",
                evidence_class="unverified",
                confidence=0.0,
                note="No supporting evidence found; this is not proof the skill is absent.",
            )
        )

    responsibility_seen: set[str] = set()
    for requirement in _responsibility_requirements(job):
        key = lexicon.normalize(requirement.skill)
        if not key or key in responsibility_seen:
            continue
        responsibility_seen.add(key)
        matches.append(_responsibility_match(requirement, profile))
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
