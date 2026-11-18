from __future__ import annotations

import json
from dataclasses import dataclass, field
from urllib.parse import urlparse

from app.config import Settings
from app.jd.fetcher import FetchError, fetch_public_text, validate_url
from app.models import CandidateProfile
from app.profile.merge import merge_profiles
from app.profile.portfolio import person_profile_from_html, snapshot_from_json

MAX_PROFILE_URLS = 5
LINKEDIN_HOSTS = {"linkedin.com", "www.linkedin.com"}


@dataclass
class ExtraProfiles:
    profiles: list[CandidateProfile] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def _tagged(profile: CandidateProfile, url: str) -> CandidateProfile:
    tagged = profile.model_copy(deep=True)
    for items in tagged.skills.values():
        for evidence in items:
            evidence.source = f"{url} | {evidence.source}" if evidence.source else url
            evidence.origin = "additional_url"
            evidence.origin_label = "Additional URL"
            evidence.source_url = url
    for entry in tagged.experience:
        entry.sources = [f"{url} | {source}" if source else url for source in entry.sources]
    for project in tagged.projects:
        project.sources = [f"{url} | {source}" if source else url for source in project.sources]
    sources = {f"additional:{url}:{key}": value for key, value in tagged.sources.items() if value}
    sources["additional_url"] = url
    tagged.sources = sources
    tagged.warnings = [f"{url}: {warning}" for warning in tagged.warnings]
    tagged.provenance.portfolio_url = url
    tagged.provenance.extra_urls = [url]
    return tagged


async def _load_json_profile(url: str, settings: Settings) -> CandidateProfile:
    result = await fetch_public_text(url, settings, accept="application/json;q=0.9,*/*;q=0.8")
    try:
        payload = json.loads(result.html)
    except json.JSONDecodeError as exc:
        raise FetchError("The URL did not return valid JSON.") from exc
    profile = snapshot_from_json(payload, result.final_url).to_profile()
    profile.warnings.extend(result.warnings)
    return profile


async def _load_url(url: str, settings: Settings) -> CandidateProfile:
    result = await fetch_public_text(url, settings)
    combined = CandidateProfile()
    if result.html.lstrip().startswith(("{", "[")):
        try:
            payload = json.loads(result.html)
        except json.JSONDecodeError:
            payload = None
        if payload is not None:
            combined = merge_profiles(
                combined, [snapshot_from_json(payload, result.final_url).to_profile()]
            )
    if not combined.experience and not combined.projects and not combined.skills:
        combined = merge_profiles(
            combined, [person_profile_from_html(result.html, result.final_url).to_profile()]
        )

    parsed = urlparse(result.final_url)
    host = (parsed.hostname or "").casefold().removeprefix("www.")
    if host not in LINKEDIN_HOSTS:
        origin = f"{parsed.scheme}://{parsed.netloc}"
        for candidate in (f"{origin}/data/experience.json", f"{origin}/data/projects.json"):
            if candidate == result.final_url:
                continue
            try:
                extra = await _load_json_profile(candidate, settings)
            except Exception:
                continue
            if extra.experience or extra.projects or extra.skills:
                combined = merge_profiles(combined, [extra])

    combined.warnings.extend(result.warnings)
    return _tagged(combined, result.final_url)


async def load_extra_profiles(urls: list[str], settings: Settings) -> ExtraProfiles:
    result = ExtraProfiles()
    seen: set[str] = set()
    for raw in urls:
        value = raw.strip()
        if not value:
            continue
        try:
            validated = validate_url(value)
        except FetchError as exc:
            result.warnings.append(f"Skipped profile URL {value}: {exc}")
            continue
        if validated in seen:
            continue
        seen.add(validated)
        if len(result.profiles) >= MAX_PROFILE_URLS:
            result.warnings.append(f"Only the first {MAX_PROFILE_URLS} profile URLs are used.")
            break
        try:
            profile = await _load_url(validated, settings)
        except FetchError as exc:
            result.warnings.append(f"Could not load profile URL {validated}: {exc}")
            continue
        if not (profile.identity.name or profile.experience or profile.projects or profile.skills):
            result.warnings.append(f"No usable profile data was found at {validated}.")
            continue
        result.profiles.append(profile)
    return result
