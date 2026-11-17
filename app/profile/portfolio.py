from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
from bs4 import BeautifulSoup

from app.models import (
    CandidateProfile,
    Certification,
    Evidence,
    ExperienceEntry,
    Identity,
    ProjectEntry,
)
from app.profile.master_resume import parse_portfolio_date
from app.skills import lexicon


def _text(value: Any) -> str:
    return str(value).strip() if value is not None else ""


def _dedupe(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        key = "".join(ch for ch in value.casefold() if ch.isalnum())
        if key and key not in seen:
            seen.add(key)
            result.append(value)
    return result


def _find_json_ld(html: str) -> list[dict[str, Any]]:
    soup = BeautifulSoup(html, "html.parser")
    documents: list[dict[str, Any]] = []
    for script in soup.find_all("script", attrs={"type": "application/ld+json"}):
        if not script.string:
            continue
        try:
            payload = json.loads(script.string)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            if isinstance(payload.get("@graph"), list):
                documents.extend(item for item in payload["@graph"] if isinstance(item, dict))
            else:
                documents.append(payload)
        elif isinstance(payload, list):
            documents.extend(item for item in payload if isinstance(item, dict))
    return documents


def _person_document(documents: list[dict[str, Any]]) -> dict[str, Any]:
    for document in documents:
        if document.get("@type") == "Person":
            return document
    return {}


def _parse_experience(raw: list[dict[str, Any]]) -> list[ExperienceEntry]:
    entries: list[ExperienceEntry] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        start, end, current = parse_portfolio_date(_text(item.get("year")))
        bullets = [_text(value) for value in item.get("highlights", []) if _text(value)]
        for project in item.get("projects", []):
            if not isinstance(project, dict):
                continue
            project_name = _text(project.get("name"))
            for highlight in project.get("highlights", []):
                if _text(highlight):
                    prefix = f"{project_name}: " if project_name else ""
                    bullets.append(prefix + _text(highlight))
        technologies = [_text(value) for value in item.get("technologies", []) if _text(value)]
        entries.append(
            ExperienceEntry(
                company=_text(item.get("company")),
                role=_text(item.get("role")),
                location=_text(item.get("location")),
                start=start,
                end=end,
                current=bool(item.get("current")) or current,
                summary=_text(item.get("impact")),
                bullets=_dedupe(bullets),
                technologies=_dedupe(technologies),
                projects=[
                    project for project in item.get("projects", []) if isinstance(project, dict)
                ],
                sources=["portfolio/experience.json"],
            )
        )
    return entries


def _parse_projects(raw: dict[str, Any]) -> list[ProjectEntry]:
    projects: list[ProjectEntry] = []
    for item in raw.get("featured", []):
        if not isinstance(item, dict):
            continue
        bullets = [
            *(_text(value) for value in item.get("approach", []) if _text(value)),
            *(_text(value) for value in item.get("outcomes", []) if _text(value)),
        ]
        projects.append(
            ProjectEntry(
                name=_text(item.get("title")),
                context=_text(item.get("context")),
                period=_text(item.get("period")),
                status=_text(item.get("status")),
                summary=_text(item.get("tagline")) or _text(item.get("problem")),
                bullets=_dedupe(bullets),
                technologies=_dedupe(
                    [_text(value) for value in item.get("stack", []) if _text(value)]
                ),
                sources=["portfolio/projects.json"],
            )
        )
    for item in raw.get("lab", []):
        if not isinstance(item, dict):
            continue
        projects.append(
            ProjectEntry(
                name=_text(item.get("name")),
                context="Portfolio AI lab",
                status=_text(item.get("status")),
                summary=_text(item.get("summary")),
                technologies=_dedupe(
                    [_text(value) for value in item.get("stack", []) if _text(value)]
                ),
                sources=["portfolio/projects.json"],
            )
        )
    return projects


def _identity_from_person(person: dict[str, Any], base_url: str) -> Identity:
    address = person.get("address") or {}
    email = _text(person.get("email")).removeprefix("mailto:")
    same_as = [url for url in person.get("sameAs", []) if isinstance(url, str)]
    return Identity(
        name=_text(person.get("name")),
        headline=_text(person.get("jobTitle")),
        location=_text(address.get("addressLocality")) if isinstance(address, dict) else "",
        email=email,
        phone=_text(person.get("telephone")),
        linkedin=next((url for url in same_as if "linkedin.com" in url), ""),
        github=next((url for url in same_as if "github.com" in url), ""),
        portfolio=base_url,
    )


def _certifications_from_person(person: dict[str, Any]) -> list[Certification]:
    certifications: list[Certification] = []
    for item in person.get("hasCredential", []):
        if not isinstance(item, dict):
            continue
        issuer = item.get("issuedBy") or {}
        certifications.append(
            Certification(
                name=_text(item.get("name")),
                issuer=_text(issuer.get("name")) if isinstance(issuer, dict) else "",
            )
        )
    return certifications


def _add_skill_evidence(skills: dict[str, list[Evidence]], source: str, texts: list[str]) -> None:
    for text in texts:
        for canonical, _ in lexicon.find(text):
            skills.setdefault(canonical, []).append(Evidence(source=source, text=text))


def person_profile_from_html(html: str, url: str) -> PortfolioSnapshot:
    person = _person_document(_find_json_ld(html))
    payload: dict[str, Any] = {
        "base_url": url,
        "home_url": url,
        "source_label": "additional_url",
    }
    if person:
        payload["person"] = person
    else:
        payload["warnings"] = ["No schema.org Person record was found in the page."]
    return PortfolioSnapshot(payload)


def snapshot_from_json(payload: Any, url: str) -> PortfolioSnapshot:
    base: dict[str, Any] = {
        "base_url": url,
        "home_url": url,
        "source_label": "additional_url",
    }
    if isinstance(payload, dict) and any(
        key in payload for key in ("person", "experience", "projects", "warnings")
    ):
        return PortfolioSnapshot({**base, **payload})
    if isinstance(payload, list):
        return PortfolioSnapshot({**base, "experience": payload})
    if isinstance(payload, dict) and any(key in payload for key in ("featured", "lab")):
        return PortfolioSnapshot({**base, "projects": payload})

    documents: list[dict[str, Any]]
    if isinstance(payload, list):
        documents = [item for item in payload if isinstance(item, dict)]
    elif isinstance(payload, dict) and isinstance(payload.get("@graph"), list):
        documents = [item for item in payload["@graph"] if isinstance(item, dict)]
    elif isinstance(payload, dict):
        documents = [payload]
    else:
        documents = []
    person = _person_document(documents)
    if person:
        return PortfolioSnapshot({**base, "person": person})
    return PortfolioSnapshot(
        {**base, "warnings": ["No supported profile data was found in the JSON response."]}
    )


class PortfolioSnapshot:
    def __init__(self, payload: dict[str, Any]) -> None:
        self.payload = payload

    @property
    def fetched_at(self) -> datetime:
        raw = self.payload.get("fetched_at")
        if not raw:
            return datetime.now(UTC)
        return datetime.fromisoformat(str(raw))

    def to_profile(self) -> CandidateProfile:
        experience = _parse_experience(self.payload.get("experience", []))
        projects = _parse_projects(self.payload.get("projects", {}))
        person = self.payload.get("person", {})
        warnings = list(self.payload.get("warnings", []))

        skills: dict[str, list[Evidence]] = {}
        for exp in experience:
            _add_skill_evidence(
                skills,
                f"portfolio / {exp.company} / {exp.role}",
                [exp.summary, *exp.bullets, *exp.technologies],
            )
        for project in projects:
            _add_skill_evidence(
                skills,
                f"portfolio / {project.name}",
                [project.summary, *project.bullets, *project.technologies],
            )
        knows_about = person.get("knowsAbout", [])
        for item in knows_about:
            value = _text(item)
            canonical = lexicon.canonicalize(value)
            if canonical:
                skills.setdefault(canonical, []).append(
                    Evidence(source="portfolio / schema.org", text=value)
                )

        awards = [_text(item) for item in person.get("award", []) if _text(item)]
        identity = _identity_from_person(person, _text(self.payload.get("base_url")))
        certifications = _certifications_from_person(person)

        return CandidateProfile(
            identity=identity,
            experience=experience,
            projects=projects,
            certifications=certifications,
            awards=awards,
            skills=skills,
            sources={
                "portfolio_home": _text(self.payload.get("home_url")),
                "portfolio_experience": _text(self.payload.get("experience_url")),
                "portfolio_projects": _text(self.payload.get("projects_url")),
            },
            warnings=warnings,
        )


async def load_portfolio(
    base_url: str,
    cache_path: Path,
    max_age_seconds: int,
    user_agent: str,
) -> PortfolioSnapshot:
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    if cache_path.exists():
        try:
            cached = json.loads(cache_path.read_text(encoding="utf-8"))
            fetched = PortfolioSnapshot(cached).fetched_at
            age = (datetime.now(UTC) - fetched).total_seconds()
            if age < max_age_seconds:
                return PortfolioSnapshot(cached)
        except (json.JSONDecodeError, ValueError, OSError):
            pass

    home_url = base_url.rstrip("/") + "/"
    experience_url = base_url.rstrip("/") + "/data/experience.json"
    projects_url = base_url.rstrip("/") + "/data/projects.json"
    headers = {"User-Agent": user_agent, "Accept": "text/html,application/json"}
    warnings: list[str] = []

    try:
        async with httpx.AsyncClient(
            timeout=20.0, follow_redirects=True, headers=headers
        ) as client:
            responses = await _fetch_all(client, [home_url, experience_url, projects_url])
        html, experience, projects = responses
        person = _person_document(_find_json_ld(html))
        payload = {
            "base_url": base_url,
            "home_url": home_url,
            "experience_url": experience_url,
            "projects_url": projects_url,
            "fetched_at": datetime.now(UTC).isoformat(),
            "person": person,
            "experience": experience,
            "projects": projects,
            "warnings": warnings,
        }
        cache_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        return PortfolioSnapshot(payload)
    except Exception as exc:
        if cache_path.exists():
            try:
                cached = json.loads(cache_path.read_text(encoding="utf-8"))
                cached.setdefault("warnings", []).append(
                    f"Portfolio refresh failed; using cached snapshot. {exc}"
                )
                return PortfolioSnapshot(cached)
            except (json.JSONDecodeError, OSError):
                pass
        raise


async def _fetch_all(
    client: httpx.AsyncClient, urls: list[str]
) -> tuple[str, list[dict[str, Any]], dict[str, Any]]:
    home, experience, projects = await _gather(client, urls)
    return home.text, experience.json(), projects.json()


async def _gather(
    client: httpx.AsyncClient, urls: list[str]
) -> tuple[httpx.Response, httpx.Response, httpx.Response]:
    import asyncio

    results = await asyncio.gather(*(client.get(url) for url in urls))
    for response in results:
        response.raise_for_status()
    return results[0], results[1], results[2]
