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


def match_requirements(job: JobDescription, profile: CandidateProfile) -> list[SkillMatch]:
    matches: list[SkillMatch] = []
    seen: set[str] = set()
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
