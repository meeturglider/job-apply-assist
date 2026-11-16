from __future__ import annotations

import hashlib
import re
from pathlib import Path

from app.models import (
    CandidateProfile,
    Certification,
    Evidence,
    ExperienceEntry,
    Identity,
    ProjectEntry,
)
from app.profile.master_resume import parse_master_resume
from app.profile.portfolio import PortfolioSnapshot
from app.skills import lexicon


def _key(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", value.casefold())


def _tokens(value: str) -> set[str]:
    return {token for token in re.findall(r"[a-z0-9]+", value.casefold()) if len(token) > 1}


def _similar(left: str, right: str, threshold: float = 0.55) -> bool:
    left_key, right_key = _key(left), _key(right)
    if not left_key or not right_key:
        return False
    if left_key == right_key or left_key in right_key or right_key in left_key:
        return True
    left_tokens, right_tokens = _tokens(left), _tokens(right)
    if not left_tokens or not right_tokens:
        return False
    return len(left_tokens & right_tokens) / len(left_tokens | right_tokens) >= threshold


def _merge_text(*values: str) -> str:
    seen: set[str] = set()
    parts: list[str] = []
    for value in values:
        cleaned = re.sub(r"\s+", " ", value).strip()
        if not cleaned:
            continue
        key = _key(cleaned)
        if key and key not in seen:
            seen.add(key)
            parts.append(cleaned)
    return " ".join(parts)


def _merge_evidence(
    left: dict[str, list[Evidence]], right: dict[str, list[Evidence]]
) -> dict[str, list[Evidence]]:
    merged: dict[str, list[Evidence]] = {}
    for source in (left, right):
        for skill, items in source.items():
            canonical = lexicon.canonicalize(skill) or skill
            bucket = merged.setdefault(canonical, [])
            existing = {(_key(item.source), _key(item.text)) for item in bucket}
            for item in items:
                marker = (_key(item.source), _key(item.text))
                if marker not in existing:
                    bucket.append(item)
                    existing.add(marker)
    return merged


def _merge_experience(
    master: list[ExperienceEntry], portfolio: list[ExperienceEntry]
) -> list[ExperienceEntry]:
    merged = [entry.model_copy(deep=True) for entry in master]
    for incoming in portfolio:
        match = next(
            (
                entry
                for entry in merged
                if _key(entry.company) == _key(incoming.company)
                and _similar(entry.role, incoming.role, threshold=0.7)
            ),
            None,
        )
        if not match:
            merged.append(incoming)
            continue
        match.location = match.location or incoming.location
        match.start = match.start or incoming.start
        match.end = match.end or incoming.end
        match.current = match.current or incoming.current
        match.summary = _merge_text(match.summary, incoming.summary)
        match.bullets = _merge_list(match.bullets, incoming.bullets)
        match.technologies = _merge_list(match.technologies, incoming.technologies)
        match.projects = [*match.projects, *incoming.projects]
        match.sources = _merge_list(match.sources, incoming.sources)
    return sorted(merged, key=lambda entry: _start_sort_key(entry.start), reverse=True)


def _merge_projects(
    master: list[ProjectEntry], portfolio: list[ProjectEntry]
) -> list[ProjectEntry]:
    merged = [project.model_copy(deep=True) for project in master]
    for incoming in portfolio:
        match = next(
            (project for project in merged if _similar(project.name, incoming.name)),
            None,
        )
        if not match:
            merged.append(incoming)
            continue
        match.name = match.name if len(match.name) >= len(incoming.name) else incoming.name
        match.context = match.context or incoming.context
        match.period = match.period or incoming.period
        match.status = match.status or incoming.status
        match.summary = _merge_text(match.summary, incoming.summary)
        match.bullets = _merge_list(match.bullets, incoming.bullets)
        match.technologies = _merge_list(match.technologies, incoming.technologies)
        match.sources = _merge_list(match.sources, incoming.sources)
    return merged


def _merge_certifications(
    master: list[Certification], portfolio: list[Certification]
) -> list[Certification]:
    merged = [cert.model_copy(deep=True) for cert in master]
    for incoming in portfolio:
        match = next((cert for cert in merged if _similar(cert.name, incoming.name, 0.55)), None)
        if not match:
            merged.append(incoming)
            continue
        if len(incoming.name) > len(match.name):
            match.name = incoming.name
        match.issuer = match.issuer or incoming.issuer
        match.year = match.year or incoming.year
    return merged


def _merge_list(left: list[str], right: list[str]) -> list[str]:
    seen = {_key(value) for value in left}
    result = list(left)
    for value in right:
        marker = _key(value)
        if marker and marker not in seen:
            seen.add(marker)
            result.append(value)
    return result


def _merge_awards(left: list[str], right: list[str]) -> list[str]:
    result = list(left)
    for value in right:
        marker = _key(value)
        if not marker or marker in {_key(item) for item in result}:
            continue
        match = next((item for item in result if _similar(item, value, 0.7)), None)
        if match is None:
            result.append(value)
        elif len(value) > len(match):
            result[result.index(match)] = value
    return result


def _start_sort_key(value: str) -> tuple[int, int]:
    match = re.match(r"([A-Z][a-z]{2})\s+(\d{4})", value)
    if not match:
        return (0, 0)
    from app.profile.master_resume import MONTHS

    return (int(match.group(2)), MONTHS.get(match.group(1)[:3].casefold(), 0))


def _merge_identity(master: Identity, portfolio: Identity, portfolio_url: str) -> Identity:
    return Identity(
        name=master.name or portfolio.name,
        headline=master.headline or portfolio.headline,
        location=master.location or portfolio.location,
        email=master.email or portfolio.email,
        phone=master.phone or portfolio.phone,
        linkedin=master.linkedin or portfolio.linkedin,
        github=master.github or portfolio.github,
        portfolio=portfolio.portfolio or portfolio_url,
    )


def build_profile(master_path: Path, portfolio: PortfolioSnapshot | None) -> CandidateProfile:
    master = parse_master_resume(master_path)
    if portfolio is None:
        master.warnings.append("Portfolio data was unavailable; using MASTER_RESUME.md only.")
        return master

    portfolio_profile = portfolio.to_profile()
    warnings = [*master.warnings, *portfolio_profile.warnings]

    identity = _merge_identity(
        master.identity,
        portfolio_profile.identity,
        master.identity.portfolio or str(portfolio.payload.get("base_url", "")),
    )
    experience = _merge_experience(master.experience, portfolio_profile.experience)
    projects = _merge_projects(master.projects, portfolio_profile.projects)
    certifications = _merge_certifications(master.certifications, portfolio_profile.certifications)
    skills = _merge_evidence(master.skills, portfolio_profile.skills)

    for entry in experience:
        for text in [entry.summary, *entry.bullets]:
            for canonical, _ in lexicon.find(text):
                evidence = Evidence(source=f"{entry.company} / {entry.role}", text=text)
                bucket = skills.setdefault(canonical, [])
                if all(item.text != evidence.text for item in bucket):
                    bucket.append(evidence)
    for project in projects:
        for text in [project.summary, *project.bullets]:
            for canonical, _ in lexicon.find(text):
                evidence = Evidence(source=project.name, text=text)
                bucket = skills.setdefault(canonical, [])
                if all(item.text != evidence.text for item in bucket):
                    bucket.append(evidence)

    _add_duration_warnings(warnings, master, portfolio)

    return CandidateProfile(
        identity=identity,
        summary=master.summary,
        experience_years=master.experience_years,
        experience=experience,
        projects=projects,
        certifications=certifications,
        education=master.education,
        awards=_merge_awards(master.awards, portfolio_profile.awards),
        skills=skills,
        skill_categories={key: value for key, value in lexicon.categories.items()},
        sources={**master.sources, **portfolio_profile.sources},
        warnings=_merge_list([], warnings),
    )


def _add_duration_warnings(
    warnings: list[str], master: CandidateProfile, portfolio: PortfolioSnapshot
) -> None:
    import json

    raw = json.dumps(portfolio.payload)
    matches = set(re.findall(r"\b1[4-9]\+?\s*years\b", raw, re.IGNORECASE))
    for value in matches:
        number = int(re.search(r"\d+", value).group(0))  # type: ignore[union-attr]
        if number > round(master.experience_years):
            warnings.append(
                f"Portfolio contains a hardcoded duration ({value}) above the computed "
                f"{master.experience_years:g} years. The computed value will be used."
            )


def profile_hash(profile: CandidateProfile) -> str:
    payload = profile.model_dump_json(exclude={"generated_at"})
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]
