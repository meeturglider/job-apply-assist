from __future__ import annotations

import re
from typing import Any
from urllib.parse import unquote

from app.models import ContactCandidate

_EMAIL_RE = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.IGNORECASE)
_MAILTO_RE = re.compile(r"mailto:([^\s\"'<>]+)", re.IGNORECASE)
_NAME_RE = re.compile(
    r"(?:contact|recruiter|hiring manager|point of contact)[:\s-]+([A-Z][A-Za-z.'-]{1,60})",
    re.IGNORECASE,
)


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _add(
    candidates: dict[str, ContactCandidate],
    email: str,
    *,
    source: str,
    confidence: float,
    name: str = "",
) -> None:
    cleaned = email.strip().strip(".,;:()[]<>")
    if not _EMAIL_RE.fullmatch(cleaned):
        return
    key = cleaned.casefold()
    existing = candidates.get(key)
    if existing is None or confidence > existing.confidence:
        candidates[key] = ContactCandidate(
            email=cleaned,
            name=_clean(name)[:120],
            source=source,
            confidence=confidence,
        )


def _metadata_contacts(
    posting: dict[str, Any] | None, candidates: dict[str, ContactCandidate]
) -> None:
    if not posting:
        return
    values = posting.get("applicationContact") or []
    if isinstance(values, dict):
        values = [values]
    if not isinstance(values, list):
        return
    for value in values:
        if not isinstance(value, dict):
            continue
        email = _clean(value.get("email"))
        name = _clean(value.get("name"))
        if email:
            _add(candidates, email, source="Job posting metadata", confidence=0.95, name=name)
    organization = posting.get("hiringOrganization")
    if isinstance(organization, dict):
        email = _clean(organization.get("email"))
        if email:
            _add(
                candidates,
                email,
                source="Job posting metadata",
                confidence=0.85,
                name=_clean(organization.get("name")),
            )


def extract_contacts(
    text: str,
    *,
    job_posting: dict[str, Any] | None = None,
    source_url: str = "",
) -> list[ContactCandidate]:
    candidates: dict[str, ContactCandidate] = {}
    _metadata_contacts(job_posting, candidates)

    for match in _MAILTO_RE.finditer(text):
        email = unquote(match.group(1).split("?", 1)[0])
        _add(candidates, email, source="Job description link", confidence=0.8)

    name_match = _NAME_RE.search(text)
    name = name_match.group(1) if name_match else ""
    for match in _EMAIL_RE.finditer(text):
        _add(
            candidates,
            match.group(0),
            source="Job description text",
            confidence=0.65,
            name=name,
        )

    return sorted(
        candidates.values(),
        key=lambda item: (-item.confidence, item.email.casefold()),
    )[:10]
