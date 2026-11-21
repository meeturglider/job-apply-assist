from __future__ import annotations

import re
from datetime import date, datetime
from pathlib import Path
from typing import Any, cast

from app.models import (
    CandidateProfile,
    Certification,
    EducationEntry,
    ExperienceEntry,
    Identity,
    ProfileProvenance,
    ProjectEntry,
)
from app.skills import lexicon

MONTHS = {
    "jan": 1,
    "feb": 2,
    "mar": 3,
    "apr": 4,
    "may": 5,
    "jun": 6,
    "jul": 7,
    "aug": 8,
    "sep": 9,
    "oct": 10,
    "nov": 11,
    "dec": 12,
}

DATE_RE = re.compile(
    r"(?P<start_month>[A-Z][a-z]{2})\s+(?P<start_year>\d{4})\s*[–-]\s*"
    r"(?P<end>Present|[A-Z][a-z]{2}\s+\d{4})"
)
EMAIL_RE = re.compile(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}")
PHONE_RE = re.compile(r"\+?\d[\d\s-]{7,}\d")
LINKEDIN_RE = re.compile(r"https?://(?:www\.)?linkedin\.com/in/[^\s)\]]+", re.IGNORECASE)
URL_RE = re.compile(r"https?://[^\s)\]>]+", re.IGNORECASE)
BULLET_RE = re.compile(r"^\s*[*-]\s+(?P<text>.+?)\s*$")


def _parse_month(value: str) -> date | None:
    value = value.strip()
    if value.casefold() == "present":
        return date.today()
    match = re.match(r"(?P<month>[A-Z][a-z]{2})\s+(?P<year>\d{4})", value)
    if not match:
        return None
    month = MONTHS.get(match.group("month")[:3].casefold())
    if not month:
        return None
    return date(int(match.group("year")), month, 1)


def _parse_date_range(value: str) -> tuple[str, str, bool]:
    match = DATE_RE.search(value)
    if not match:
        return "", "", False
    end = match.group("end").strip()
    return (
        f"{match.group('start_month')} {match.group('start_year')}",
        "Present" if end.casefold() == "present" else end,
        end.casefold() == "present",
    )


def _clean_bullet(value: str) -> str:
    value = re.sub(r"\s+", " ", value).strip()
    return value.removeprefix("- ").removeprefix("* ").strip()


def _extract_identity(lines: list[str]) -> Identity:
    identity = Identity()
    if lines:
        identity.name = lines[0].lstrip("# ").strip()
    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("## ") and not identity.headline:
            identity.headline = stripped[3:].strip()
        if stripped.startswith("📍"):
            identity.location = stripped.lstrip("📍").strip()
        email = EMAIL_RE.search(stripped)
        if email and not identity.email:
            identity.email = email.group(0)
        if "linkedin" in stripped.casefold():
            match = LINKEDIN_RE.search(stripped)
            if match:
                identity.linkedin = match.group(0)
        for url in URL_RE.findall(stripped):
            normalized = url.rstrip(".,")
            lowered = normalized.casefold()
            if "linkedin.com" in lowered:
                if not identity.linkedin:
                    identity.linkedin = normalized
            elif "github.com" in lowered:
                if not identity.github:
                    identity.github = normalized
            elif not identity.portfolio:
                identity.portfolio = normalized
        if not identity.phone and ("📞" in stripped or PHONE_RE.search(stripped)):
            match = PHONE_RE.search(stripped)
            if match:
                identity.phone = match.group(0).strip()
    return identity


def _split_sections(text: str) -> dict[str, list[str]]:
    sections: dict[str, list[str]] = {}
    current = "_preamble"
    sections[current] = []
    name_heading = False
    for line in text.splitlines():
        if line.startswith("# "):
            if not name_heading:
                name_heading = True
                sections[current].append(line)
                continue
            current = line[2:].strip().casefold()
            sections.setdefault(current, [])
            continue
        sections.setdefault(current, []).append(line)
    return sections


def _parse_skills(lines: list[str]) -> dict[str, list[str]]:
    grouped: dict[str, list[str]] = {}
    current = "Other"
    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("## "):
            current = stripped[3:].strip()
            grouped.setdefault(current, [])
            continue
        match = BULLET_RE.match(line)
        if match:
            grouped.setdefault(current, []).append(_clean_bullet(match.group("text")))
    return grouped


def _parse_experience(lines: list[str]) -> list[ExperienceEntry]:
    entries: list[ExperienceEntry] = []
    current_company = ""
    block: list[str] = []

    def flush() -> None:
        nonlocal block
        if not current_company or not block:
            block = []
            return
        entry = _parse_experience_block(current_company, block)
        if entry:
            entries.append(entry)
        block = []

    for line in lines:
        if line.startswith("## "):
            flush()
            current_company = line[3:].strip()
        else:
            block.append(line)
    flush()
    return entries


def _parse_experience_block(company: str, lines: list[str]) -> ExperienceEntry | None:
    role = ""
    location = ""
    start = ""
    end = ""
    current = False
    summary_parts: list[str] = []
    bullets: list[str] = []
    technologies: list[str] = []

    for index, line in enumerate(lines):
        stripped = line.strip()
        if not stripped or stripped == "---":
            continue
        if stripped.startswith("### "):
            heading = stripped[4:].strip()
            follow = ""
            for next_line in lines[index + 1 :]:
                if next_line.strip():
                    follow = next_line.strip()
                    break
            if DATE_RE.search(follow) or "📅" in follow:
                role = heading
            continue
        if "📅" in stripped or DATE_RE.search(stripped):
            start, end, current = _parse_date_range(stripped)
            location = stripped.split("|", 1)[1].strip() if "|" in stripped else location
            continue
        bullet = BULLET_RE.match(line)
        if bullet:
            text = _clean_bullet(bullet.group("text"))
            if text:
                bullets.append(text)
            continue
        if stripped.startswith("Technologies"):
            technologies.extend(
                part.strip()
                for part in re.split(r"[,;]", stripped.split(":", 1)[-1])
                if part.strip()
            )
            continue
        if len(stripped) > 40:
            summary_parts.append(stripped)

    if not role:
        return None

    if not technologies:
        technologies = [canonical for canonical, _ in lexicon.find(" ".join(bullets))]

    return ExperienceEntry(
        company=company,
        role=role,
        location=location,
        start=start,
        end=end,
        current=current,
        summary=" ".join(summary_parts[:2]).strip(),
        bullets=_dedupe(bullets),
        technologies=_dedupe(technologies),
        sources=["MASTER_RESUME.md"],
    )


def _dedupe(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        key = re.sub(r"\W+", "", value.casefold())
        if key and key not in seen:
            seen.add(key)
            result.append(value)
    return result


def _parse_certifications(lines: list[str]) -> list[Certification]:
    result: list[Certification] = []
    for line in lines:
        match = BULLET_RE.match(line)
        if match:
            name = _clean_bullet(match.group("text"))
            if name:
                result.append(Certification(name=name))
    return result


def _parse_awards(lines: list[str]) -> list[str]:
    result: list[str] = []
    for line in lines:
        match = BULLET_RE.match(line)
        if match:
            value = _clean_bullet(match.group("text"))
            if value:
                result.append(value)
    return _dedupe(result)


def _parse_education(lines: list[str]) -> list[EducationEntry]:
    degree = ""
    institution = ""
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("## "):
            degree = stripped[3:].strip()
        elif stripped and not stripped.startswith("#") and not degree.startswith("Education"):
            institution = stripped
            break
    if degree or institution:
        return [EducationEntry(degree=degree, institution=institution)]
    return []


def _parse_projects(lines: list[str], *, default_context: str = "") -> list[ProjectEntry]:
    projects: list[ProjectEntry] = []
    current: dict[str, Any] | None = None
    in_tech = False

    def flush() -> None:
        nonlocal current
        if current is None:
            return
        name = str(current.get("name", "")).strip()
        if name:
            summary_parts = cast(list[str], current.get("summary_parts", []))
            projects.append(
                ProjectEntry(
                    name=name,
                    context=str(current.get("context") or default_context).strip(),
                    period=str(current.get("period") or "").strip(),
                    status=str(current.get("status") or "").strip(),
                    summary=" ".join(summary_parts).strip(),
                    bullets=_dedupe(cast(list[str], current.get("bullets", []))),
                    technologies=_dedupe(cast(list[str], current.get("technologies", []))),
                    sources=["MASTER_RESUME.md"],
                )
            )
        current = None

    for line in lines:
        stripped = line.strip()
        if not stripped or stripped == "---":
            continue
        if stripped.startswith("## "):
            flush()
            current = {
                "name": stripped[3:].strip(),
                "bullets": [],
                "technologies": [],
                "summary_parts": [],
            }
            in_tech = False
            continue
        if current is None:
            continue
        if stripped.startswith("### "):
            in_tech = stripped[4:].strip().casefold().startswith("technolog")
            continue
        if ":" in stripped and not stripped.startswith(("*", "-")):
            key, value = stripped.split(":", 1)
            if key.strip().casefold() in {"status", "period", "context"}:
                current[key.strip().casefold()] = value.strip()
                continue
        bullet = BULLET_RE.match(line)
        if bullet:
            text = _clean_bullet(bullet.group("text"))
            if text:
                target = "technologies" if in_tech else "bullets"
                cast(list[str], current.setdefault(target, [])).append(text)
            continue
        cast(list[str], current.setdefault("summary_parts", [])).append(stripped)
    flush()
    return projects


def parse_master_resume(path: Path) -> CandidateProfile:
    text = path.read_text(encoding="utf-8")
    sections = _split_sections(text)
    preamble = sections.get("_preamble", [])
    identity = _extract_identity(preamble)

    experience = _parse_experience(sections.get("professional experience", []))
    skill_groups = _parse_skills(sections.get("core skills", []))
    certifications = _parse_certifications(sections.get("certifications", []))
    education = _parse_education(sections.get("education", []))
    awards = _parse_awards(sections.get("awards", []))

    projects = _parse_projects(sections.get("selected projects", []))
    projects.extend(
        _parse_projects(
            sections.get("additional ai infrastructure experience", []),
            default_context="Personal lab",
        )
    )

    summary = " ".join(
        line.strip()
        for line in sections.get("professional summary", [])
        if line.strip() and line.strip() != "---"
    )

    skills: dict[str, list] = {}
    for category, items in skill_groups.items():
        for item in items:
            canonical = lexicon.canonicalize(item)
            if not canonical:
                continue
            from app.models import Evidence

            skills.setdefault(canonical, []).append(
                Evidence(
                    source="MASTER_RESUME.md",
                    text=f"{category}: {item}",
                    origin="resume",
                    origin_label="Default resume",
                )
            )
    for exp in experience:
        for tech in exp.technologies:
            canonical = lexicon.canonicalize(tech)
            if canonical:
                from app.models import Evidence

                skills.setdefault(canonical, []).append(
                    Evidence(
                        source=f"{exp.company} / {exp.role}",
                        text=tech,
                        origin="resume",
                        origin_label="Default resume",
                    )
                )

    years = _years_of_experience(experience)
    warnings: list[str] = []
    hardcoded = re.findall(r"\b1[4-9]\+?\s*years?\b", text, re.IGNORECASE)
    if hardcoded and not any(_close_years(value, years) for value in hardcoded):
        warnings.append(
            "MASTER_RESUME.md contains a hardcoded experience duration "
            f"({', '.join(sorted(set(hardcoded)))}) that differs from the computed "
            f"{years:g} years. The computed value will be used."
        )

    return CandidateProfile(
        identity=identity,
        summary=summary,
        experience_years=years,
        experience=experience,
        projects=projects,
        certifications=certifications,
        education=education,
        awards=awards,
        skills=skills,
        skill_categories={key: value for key, value in lexicon.categories.items()},
        sources={"master_resume": str(path)},
        warnings=warnings,
        provenance=ProfileProvenance(
            resume_kind="default",
            resume_name=path.name,
        ),
    )


def _close_years(value: str, computed: float) -> bool:
    match = re.search(r"\d+", value)
    if not match:
        return False
    return abs(int(match.group(0)) - round(computed)) <= 1


def years_of_experience(experience: list[ExperienceEntry]) -> float:
    return _years_of_experience(experience)


def _years_of_experience(experience: list[ExperienceEntry]) -> float:
    starts: list[date] = []
    ends: list[date] = []
    for entry in experience:
        start = _parse_month(entry.start)
        end = _parse_month(entry.end) if entry.end else None
        if start:
            starts.append(start)
        if end:
            ends.append(end)
    if not starts:
        return 0
    start = min(starts)
    end = max(ends) if ends else date.today()
    return round((end - start).days / 365.25, 1)


def tag_resume_origin(
    profile: CandidateProfile, source: str, *, uploaded: bool
) -> CandidateProfile:
    profile.sources = {"master_resume": source}
    profile.provenance.resume_kind = "uploaded" if uploaded else "default"
    profile.provenance.resume_name = source
    if not uploaded:
        return profile
    for items in profile.skills.values():
        for item in items:
            item.origin = "uploaded_resume"
            item.origin_label = "Uploaded resume"
            item.source = source
    for exp in profile.experience:
        exp.sources = [source]
    for project in profile.projects:
        project.sources = [source]
    return profile


def parse_master_resume_text(text: str, source: str = "MASTER_RESUME.md") -> CandidateProfile:
    import tempfile

    with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False, encoding="utf-8") as handle:
        handle.write(text)
        temp_path = Path(handle.name)
    try:
        profile = parse_master_resume(temp_path)
        return tag_resume_origin(profile, source, uploaded=True)
    finally:
        temp_path.unlink(missing_ok=True)


def parse_portfolio_date(value: str) -> tuple[str, str, bool]:
    """Parse portfolio ranges such as 'Jul 2025 - Present'."""
    normalized = value.replace(" - ", " – ").replace("-", " – ")
    return _parse_date_range(normalized)


def date_from_month(value: str) -> date | None:
    return _parse_month(value)


def format_today() -> str:
    return datetime.now().strftime("%b %Y")
