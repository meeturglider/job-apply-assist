from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from typing import Any

from app.llm import OllamaClient
from app.llm_schemas import LLMJobExtraction
from app.matching.dimensions import classify_dimension
from app.models import JobDescription, Requirement
from app.prompts import load_prompt
from app.skills import lexicon

ROLE_PATTERNS: list[tuple[str, tuple[str, ...]]] = [
    ("sre", ("site reliability", " sre", "reliability engineer")),
    ("platform_engineer", ("platform engineer", "platform engineering", "developer platform")),
    ("devops", ("devops", "dev ops", "cicd engineer", "ci/cd engineer")),
    (
        "ai_infra",
        ("ai infrastructure", "ml infrastructure", "ai platform", "mlops", "llm infrastructure"),
    ),
    ("cloud_operations", ("cloud operations", "cloud support", "support operations")),
    ("engineering_manager", ("engineering manager", "manager, engineering", "head of engineering")),
    ("cloud_architect", ("cloud architect", "solution architect", "cloud architecture")),
]

PREFERRED_HEADINGS = re.compile(
    r"(preferred qualifications|preferred skills|nice[- ]to[- ]have|bonus points|"
    r"good to have|plus points|desired skills|preferred:)",
    re.IGNORECASE,
)

CERT_PATTERNS = [
    r"Google Cloud Professional [A-Za-z ]+",
    r"AWS Certified [A-Za-z \-–]+",
    r"Microsoft Certified [A-Za-z \-–]+",
    r"HashiCorp Certified [A-Za-z ]+(?:\([^)]*\))?",
    r"Certified Kubernetes Administrator(?:\s*\(CKA\))?",
    r"Certified Kubernetes Security Specialist(?:\s*\(CKS\))?",
    r"Certified Kubernetes Application Developer(?:\s*\(CKAD\))?",
    r"\bCKA\b",
    r"\bCKS\b",
    r"\bCKAD\b",
    r"Security\+",
]

EDUCATION_RE = re.compile(
    r"\b(bachelor(?:'s)?|master(?:'s)?|master of|mba|ph\.?d|doctorate|"
    r"associate(?:'s)?|degree|b\.?\s?tech|m\.?\s?tech|b\.?\s?e\.?)\b",
    re.IGNORECASE,
)
EXPERIENCE_YEARS_RE = re.compile(r"\b(?P<years>\d{1,2})\s*\+?\s*(?:years?|yrs?)\b", re.IGNORECASE)
SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+|\n+")

STOPWORDS = {
    "the",
    "and",
    "for",
    "with",
    "you",
    "your",
    "our",
    "are",
    "will",
    "that",
    "this",
    "from",
    "have",
    "has",
    "can",
    "not",
    "all",
    "who",
    "job",
    "work",
    "role",
    "team",
    "teams",
    "experience",
    "years",
    "strong",
    "using",
    "able",
    "well",
    "such",
    "into",
    "across",
    "including",
    "other",
    "more",
    "than",
    "their",
    "they",
    "them",
    "what",
    "when",
    "where",
    "which",
    "while",
    "would",
    "should",
    "could",
    "about",
    "also",
    "must",
    "need",
    "needs",
    "required",
    "requirements",
    "responsibilities",
    "skills",
    "preferred",
    "candidate",
    "candidates",
    "apply",
    "benefits",
    "company",
}


def _role_family(title: str, text: str) -> str:
    haystack = f"{title} {text[:3000]}".casefold()
    for family, patterns in ROLE_PATTERNS:
        if any(pattern in haystack for pattern in patterns):
            return family
    return "other"


def _seniority(title: str, text: str) -> str:
    haystack = f"{title} {text[:1500]}".casefold()
    for value, patterns in (
        ("director", ("director", "vp ")),
        ("principal", ("principal",)),
        ("staff", ("staff engineer", "staff ")),
        ("manager", ("manager", "head of", "lead of")),
        ("senior", ("senior", "sr.", "sr ")),
        ("lead", ("lead", "leader")),
        ("junior", ("junior", "entry level", "graduate")),
    ):
        if any(pattern in haystack for pattern in patterns):
            return value
    return "mid"


def _preferred_position(text: str) -> int | None:
    match = PREFERRED_HEADINGS.search(text)
    return match.start() if match else None


def _skill_weight(count: int, required: bool) -> float:
    weight = 1.0 + min(count - 1, 4) * 0.25
    if required:
        weight += 0.25
    return round(min(weight, 2.0), 2)


def heuristic_requirements(
    text: str,
) -> tuple[list[Requirement], list[Requirement], dict[str, float]]:
    preferred_at = _preferred_position(text)
    found = lexicon.find(text)
    must: list[Requirement] = []
    nice: list[Requirement] = []
    keywords: dict[str, float] = {}

    for canonical, count in found:
        positions = [
            match.start() for match in re.finditer(re.escape(canonical), text, re.IGNORECASE)
        ]
        first = positions[0] if positions else 0
        is_nice = preferred_at is not None and first > preferred_at
        category = lexicon.category_for(canonical)
        requirement = Requirement(
            skill=canonical,
            category=category,
            importance="nice_to_have" if is_nice else "must_have",
            weight=_skill_weight(count, not is_nice),
            context="",
            dimension=classify_dimension(canonical, category),
        )
        if is_nice:
            nice.append(requirement)
        else:
            must.append(requirement)
        keywords[canonical] = max(keywords.get(canonical, 0), float(count))

    for canonical, count in _top_phrases(text)[:8]:
        keywords.setdefault(canonical, float(count))

    return must, nice, keywords


def _top_phrases(text: str) -> list[tuple[str, int]]:
    words = [
        word.casefold()
        for word in re.findall(r"[A-Za-z][A-Za-z+#./-]{2,}", text)
        if word.casefold() not in STOPWORDS
    ]
    counts: dict[str, int] = {}
    known = {entry.casefold() for entry in lexicon.by_canonical}
    for index, word in enumerate(words):
        if word not in known:
            counts[word] = counts.get(word, 0) + 1
        if index + 1 < len(words):
            bigram = f"{word} {words[index + 1]}"
            if len(bigram) <= 40:
                counts[bigram] = counts.get(bigram, 0) + 1
    ranked = [(term, count) for term, count in counts.items() if count >= 2]
    ranked.sort(key=lambda item: (-item[1], item[0]))
    return ranked


def _detect_certifications(text: str) -> list[Requirement]:
    found: list[Requirement] = []
    seen: set[str] = set()
    for pattern in CERT_PATTERNS:
        for match in re.finditer(pattern, text, re.IGNORECASE):
            value = re.sub(r"\s+", " ", match.group(0)).strip(" ,.;")
            key = value.casefold()
            if len(value) < 3 or key in seen:
                continue
            seen.add(key)
            found.append(
                Requirement(
                    skill=value,
                    category="Certification",
                    importance="nice_to_have",
                    weight=1.0,
                    context="Mentioned in the job description.",
                    dimension="certification",
                )
            )
    return found


def _is_education_requirement(value: str) -> bool:
    lowered = value.casefold()
    if not EDUCATION_RE.search(lowered):
        return False
    return any(
        term in lowered
        for term in (
            "degree",
            "bachelor",
            "master's",
            "master of",
            "mba",
            "ph.d",
            "phd",
            "doctorate",
            "associate's",
            "b.tech",
            "m.tech",
        )
    )


def _is_experience_requirement(value: str) -> bool:
    lowered = value.casefold()
    if not EXPERIENCE_YEARS_RE.search(lowered):
        return False
    return any(
        term in lowered
        for term in (
            "experience",
            "technical",
            "industry",
            "relevant",
            "track record",
            "background",
        )
    )


def _requirement_sentences(text: str) -> list[str]:
    sentences: list[str] = []
    for value in SENTENCE_SPLIT_RE.split(text):
        cleaned = re.sub(r"^[\s•*\-–]+", "", value)
        cleaned = re.sub(r"\s+", " ", cleaned).strip(" ,;:-")
        if 8 <= len(cleaned) <= 300:
            sentences.append(cleaned)
    return sentences


def _detect_education_requirements(text: str) -> list[Requirement]:
    found: list[Requirement] = []
    seen: set[str] = set()
    for sentence in _requirement_sentences(text):
        if not _is_education_requirement(sentence):
            continue
        key = sentence.casefold()
        if key in seen:
            continue
        seen.add(key)
        found.append(
            Requirement(
                skill=sentence,
                category="Education",
                importance="must_have",
                weight=1.5,
                context="Mentioned in the job description.",
                dimension="education",
            )
        )
    return found


def _detect_experience_requirements(text: str) -> list[Requirement]:
    found: list[Requirement] = []
    seen: set[str] = set()
    for sentence in _requirement_sentences(text):
        if not _is_experience_requirement(sentence):
            continue
        key = sentence.casefold()
        if key in seen:
            continue
        seen.add(key)
        found.append(
            Requirement(
                skill=sentence,
                category="Experience",
                importance="must_have",
                weight=1.5,
                context="Mentioned in the job description.",
                dimension="experience",
            )
        )
    return found


def _normalize_requirements(
    values: Any, importance: str, *, canonicalize: bool = True
) -> list[Requirement]:
    result: list[Requirement] = []
    seen: set[str] = set()
    if not isinstance(values, list):
        return result
    for item in values:
        dimension = ""
        if isinstance(item, str):
            skill = item.strip()
            category = ""
            weight = 1.0
            context = ""
        elif isinstance(item, dict):
            skill = str(item.get("skill", "")).strip()
            category = str(item.get("category", "")).strip()
            dimension = str(item.get("dimension", "")).strip()
            try:
                weight = float(item.get("weight", 1.0))
            except (TypeError, ValueError):
                weight = 1.0
            context = str(item.get("context", "")).strip()
        else:
            continue
        if not skill or len(skill) > 120:
            continue
        canonical = lexicon.canonicalize(skill) if canonicalize else skill
        key = canonical.casefold()
        if key in seen:
            continue
        seen.add(key)
        resolved_category = category or lexicon.category_for(canonical)
        result.append(
            Requirement(
                skill=canonical,
                category=resolved_category,
                importance="must_have" if importance == "must_have" else "nice_to_have",
                weight=max(0.5, min(weight, 2.0)),
                context=context,
                dimension=dimension or classify_dimension(canonical, resolved_category),
            )
        )
    return result


def _merge_requirements(
    primary: list[Requirement], secondary: list[Requirement]
) -> list[Requirement]:
    merged = list(primary)
    existing = {lexicon.normalize(item.skill) for item in merged}
    for item in secondary:
        key = lexicon.normalize(item.skill)
        if key in existing:
            continue
        if any(
            key in lexicon.normalize(other.skill) or lexicon.normalize(other.skill) in key
            for other in merged
            if len(key) > 4 and len(lexicon.normalize(other.skill)) > 4
        ):
            continue
        merged.append(item)
        existing.add(key)
    return merged


def _heuristic_summary(title: str, must: list[Requirement], text: str) -> str:
    top = ", ".join(item.skill for item in must[:5])
    first_sentences = " ".join(re.split(r"(?<=[.!?])\s+", text)[:2]).strip()
    if title and top:
        return f"{title} role focused on {top}. {first_sentences}"[:700]
    return first_sentences[:700]


async def analyze_job(
    text: str,
    llm: OllamaClient,
    *,
    title: str = "",
    company: str = "",
    location: str = "",
    source_url: str = "",
) -> JobDescription:
    text = text.strip()
    if not text:
        raise ValueError("Job description text is empty.")

    heuristic_must, heuristic_nice, heuristic_keywords = heuristic_requirements(text)
    heuristic_certs = _detect_certifications(text)
    warnings: list[str] = []
    method = "heuristic"
    llm_data: dict[str, Any] = {}
    fallback_reason = ""
    llm_attempts = 0

    if llm.enabled:
        prompt = (
            f"Known title: {title or 'unknown'}\n"
            f"Known company: {company or 'unknown'}\n"
            f"Known location: {location or 'unknown'}\n\n"
            "Job description:\n"
            "<<<JD\n"
            f"{text[:16000]}\n"
            "JD>>>"
        )
        try:
            llm_data = await llm.json(
                load_prompt("jd_extract.md"),
                prompt,
                schema=LLMJobExtraction,
                operation="jd_extract",
            )
            llm_attempts = getattr(llm, "last_attempts", 1)
            method = "llm"
        except Exception as exc:
            fallback_reason = str(exc)
            llm_attempts = getattr(exc, "attempts", 0)
            warnings.append(f"LLM extraction failed; using deterministic extraction. {exc}")

    must = _normalize_requirements(llm_data.get("must_have"), "must_have")
    nice = _normalize_requirements(llm_data.get("nice_to_have"), "nice_to_have")
    certifications = _normalize_requirements(llm_data.get("certifications"), "nice_to_have")

    must = _merge_requirements(must, heuristic_must)
    nice = _merge_requirements(nice, heuristic_nice)
    certifications = _merge_requirements(certifications, heuristic_certs)

    education_requirements = _merge_requirements(
        _normalize_requirements(
            llm_data.get("education_requirements"), "must_have", canonicalize=False
        ),
        _detect_education_requirements(text),
    )
    experience_requirements = _merge_requirements(
        _normalize_requirements(
            llm_data.get("experience_requirements"), "must_have", canonicalize=False
        ),
        _detect_experience_requirements(text),
    )
    for requirement in education_requirements:
        requirement.category = "Education"
    for requirement in experience_requirements:
        requirement.category = "Experience"

    must = [
        item
        for item in must
        if not _is_education_requirement(item.skill) and not _is_experience_requirement(item.skill)
    ]
    nice = [
        item
        for item in nice
        if not _is_education_requirement(item.skill) and not _is_experience_requirement(item.skill)
    ]

    if not must:
        must = heuristic_must
        warnings.append("No must-have requirements were detected; review the JD manually.")
    if not nice:
        nice = heuristic_nice

    keywords: dict[str, float] = {}
    raw_keywords = llm_data.get("keywords")
    if isinstance(raw_keywords, dict):
        for key, value in raw_keywords.items():
            value_text = str(key).strip()
            if not value_text:
                continue
            try:
                weight = float(value)
            except (TypeError, ValueError):
                weight = 1.0
            keywords[value_text] = max(0.5, min(weight, 5.0))
    for requirement in [*must, *nice]:
        keywords.setdefault(requirement.skill, max(1.0, requirement.weight))
    for key, value in heuristic_keywords.items():
        keywords.setdefault(key, value)

    resolved_title = str(llm_data.get("title", "")).strip() or title or "Target role"
    resolved_company = str(llm_data.get("company", "")).strip() or company
    resolved_location = str(llm_data.get("location", "")).strip() or location
    role_family = str(llm_data.get("role_family", "")).strip() or _role_family(resolved_title, text)
    if role_family not in {
        "cloud_architect",
        "platform_engineer",
        "devops",
        "sre",
        "ai_infra",
        "engineering_manager",
        "cloud_operations",
        "other",
    }:
        role_family = _role_family(resolved_title, text)
    seniority = str(llm_data.get("seniority", "")).strip() or _seniority(resolved_title, text)
    if seniority == "unknown":
        seniority = _seniority(resolved_title, text)
    summary = str(llm_data.get("summary", "")).strip() or _heuristic_summary(
        resolved_title, must, text
    )
    responsibilities = (
        [str(item).strip() for item in llm_data.get("responsibilities", []) if str(item).strip()]
        if isinstance(llm_data.get("responsibilities"), list)
        else []
    )
    if not responsibilities:
        responsibilities = _extract_responsibilities(text)
    responsibility_requirements = [
        Requirement(
            skill=item,
            category="Responsibility",
            importance="must_have",
            weight=1.0,
            context="Derived from the job responsibilities.",
            dimension=classify_dimension(item),
        )
        for item in responsibilities[:12]
        if item.strip()
    ]

    return JobDescription(
        title=resolved_title,
        company=resolved_company,
        location=resolved_location,
        seniority=seniority,
        role_family=role_family,
        summary=summary,
        must_have=must,
        nice_to_have=nice,
        education_requirements=education_requirements,
        experience_requirements=experience_requirements,
        responsibility_requirements=responsibility_requirements,
        responsibilities=responsibilities[:12],
        certifications=certifications,
        keywords=dict(sorted(keywords.items(), key=lambda item: -item[1])[:40]),
        raw_text=text,
        source_url=source_url,
        fetched_at=datetime.now(UTC),
        extraction_method=method,
        extraction_fallback_reason=fallback_reason,
        llm_attempts=llm_attempts,
        warnings=warnings,
    )


def _extract_responsibilities(text: str) -> list[str]:
    lines = [line.strip(" •-\t") for line in text.splitlines() if line.strip()]
    candidates = [
        line
        for line in lines
        if 30 <= len(line) <= 300
        and re.match(
            r"(design|build|lead|manage|develop|implement|own|drive|architect|"
            r"collaborate|support|maintain|automate|deliver|create|define|partner)",
            line,
            re.IGNORECASE,
        )
    ]
    return candidates[:10]


def job_prompt_payload(job: JobDescription) -> str:
    return json.dumps(
        {
            "title": job.title,
            "company": job.company,
            "seniority": job.seniority,
            "role_family": job.role_family,
            "summary": job.summary,
            "must_have": [item.model_dump() for item in job.must_have],
            "nice_to_have": [item.model_dump() for item in job.nice_to_have],
            "education_requirements": [item.model_dump() for item in job.education_requirements],
            "experience_requirements": [item.model_dump() for item in job.experience_requirements],
            "responsibility_requirements": [
                item.model_dump() for item in job.responsibility_requirements
            ],
            "responsibilities": job.responsibilities,
            "certifications": [item.model_dump() for item in job.certifications],
            "keywords": job.keywords,
        },
        indent=2,
    )
