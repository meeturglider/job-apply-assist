from __future__ import annotations

import re
from dataclasses import dataclass
from difflib import SequenceMatcher

from app.matching.dimensions import tokens
from app.skills import lexicon

NUMBER_RE = re.compile(r"(?<![A-Za-z0-9_.])\d[\d,]*(?:\.\d+)?%?\+?(?![A-Za-z0-9_.])")
MONTH_RE = re.compile(
    r"\b(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|"
    r"jul(?:y)?|aug(?:ust)?|sep(?:tember)?|oct(?:ober)?|nov(?:ember)?|"
    r"dec(?:ember)?)\s+\d{4}\b",
    re.IGNORECASE,
)
TEAM_SIZE_RE = re.compile(
    r"\bteam of\s+(\d[\d,]*)\b|\b(\d[\d,]*)\s*(?:person|people|reports?|members?)\b",
    re.IGNORECASE,
)
FINANCIAL_RE = re.compile(
    r"(?:\$|₹|€|£)\s?\d[\d,.]*\s?(?:k|m|bn|billion|million|thousand)?\b|"
    r"\b\d[\d,.]*\s?(?:million|billion|thousand|k|m|bn)\b",
    re.IGNORECASE,
)
SCOPE_RE = re.compile(
    r"\b\d[\d,]*\s*\+?\s*(?:regions?|clusters?|accounts?|teams?|projects?|"
    r"applications?|services?|users?|customers?|environments?|workloads?|countries?)\b",
    re.IGNORECASE,
)
LEADERSHIP_RE = re.compile(
    r"\b(lead|led|leading|manage|managed|managing|mentor|mentored|mentoring|"
    r"coach|coached|coaching|supervise|supervised|supervising|direct|directed|"
    r"directing|own|owned|owning|head of)\b",
    re.IGNORECASE,
)
OUTCOME_RE = re.compile(
    r"\b(reduc(?:e|ed|es|ing)|increas(?:e|ed|es|ing)|improv(?:e|ed|es|ing)|"
    r"sav(?:e|ed|es|ing)|accelerat(?:e|ed|es|ing)|grew|grow(?:th|n|ing)?|"
    r"cut|achiev(?:e|ed|es|ing)|deliver(?:ed|s|ing)?|launch(?:ed|es|ing)?|"
    r"migrat(?:e|ed|es|ing)|scal(?:e|ed|es|ing))\b",
    re.IGNORECASE,
)
TITLE_RE = re.compile(
    r"\b(?:(?:senior|staff|principal|lead|junior|chief|head|director|vp)\s+)?"
    r"(?:software|cloud|platform|data|ai|devops|site reliability|security|systems|"
    r"infrastructure|solutions?)\s+"
    r"(?:engineer|architect|manager|director|consultant|developer|administrator|analyst)\b",
    re.IGNORECASE,
)


LEADERSHIP_BASES = {
    "lead": "lead",
    "led": "lead",
    "leading": "lead",
    "manage": "manage",
    "managed": "manage",
    "managing": "manage",
    "mentor": "mentor",
    "mentored": "mentor",
    "mentoring": "mentor",
    "coach": "coach",
    "coached": "coach",
    "coaching": "coach",
    "supervise": "supervise",
    "supervised": "supervise",
    "supervising": "supervise",
    "direct": "direct",
    "directed": "direct",
    "directing": "direct",
    "own": "own",
    "owned": "own",
    "owning": "own",
    "head of": "head",
}

OUTCOME_BASES = {
    "reduce": "reduce",
    "reduced": "reduce",
    "reduces": "reduce",
    "reducing": "reduce",
    "increase": "increase",
    "increased": "increase",
    "increases": "increase",
    "increasing": "increase",
    "improve": "improve",
    "improved": "improve",
    "improves": "improve",
    "improving": "improve",
    "save": "save",
    "saved": "save",
    "saves": "save",
    "saving": "save",
    "accelerate": "accelerate",
    "accelerated": "accelerate",
    "accelerates": "accelerate",
    "accelerating": "accelerate",
    "grew": "grow",
    "grow": "grow",
    "growth": "grow",
    "growing": "grow",
    "cut": "cut",
    "achieve": "achieve",
    "achieved": "achieve",
    "achieves": "achieve",
    "achieving": "achieve",
    "deliver": "deliver",
    "delivered": "deliver",
    "delivers": "deliver",
    "delivering": "deliver",
    "launch": "launch",
    "launched": "launch",
    "launches": "launch",
    "launching": "launch",
    "migrate": "migrate",
    "migrated": "migrate",
    "migrates": "migrate",
    "migrating": "migrate",
    "scale": "scale",
    "scaled": "scale",
    "scales": "scale",
    "scaling": "scale",
}


@dataclass(frozen=True)
class Claim:
    text: str
    claim_type: str


@dataclass(frozen=True)
class ClaimCheck:
    claim: Claim
    supported: bool
    source: str
    reason: str


AMOUNT_RE = re.compile(r"\d[\d,]*(?:\.\d+)?")


def _numbers(value: str) -> set[str]:
    return {match.group(0).replace(",", "").replace(" ", "") for match in NUMBER_RE.finditer(value)}


def _amounts(value: str) -> set[str]:
    return {match.group(0).replace(",", "") for match in AMOUNT_RE.finditer(value)}


def _normalized(value: str) -> str:
    return re.sub(r"\s+", " ", value.casefold()).strip()


def _similarity(left: str, right: str) -> float:
    if _normalized(left) == _normalized(right):
        return 1.0
    left_tokens, right_tokens = tokens(left), tokens(right)
    if not left_tokens or not right_tokens:
        return 0.0
    jaccard = len(left_tokens & right_tokens) / len(left_tokens | right_tokens)
    sequence = SequenceMatcher(None, left.casefold(), right.casefold()).ratio()
    return max(jaccard, sequence)


def best_source(text: str, sources: list[str]) -> tuple[str, float]:
    best = ("", 0.0)
    for source in sources:
        score = _similarity(text, source)
        if score > best[1]:
            best = (source, score)
    return best


def _technology_supported(text: str, sources: list[str]) -> bool:
    wanted = {lexicon.canonicalize(canonical) for canonical, _ in lexicon.find(text)}
    if not wanted:
        return True
    available = {
        lexicon.canonicalize(canonical)
        for source in sources
        for canonical, _ in lexicon.find(source)
    }
    return wanted <= available


def _numbers_supported(text: str, sources: list[str], allowed_numbers: set[str]) -> bool:
    wanted = _numbers(text)
    available = set(allowed_numbers)
    for source in sources:
        available.update(_numbers(source))
    return wanted <= available


def _leadership_supported(text: str, sources: list[str]) -> bool:
    wanted = {
        LEADERSHIP_BASES.get(match.group(0).casefold(), match.group(0).casefold())
        for match in LEADERSHIP_RE.finditer(text)
    }
    available: set[str] = set()
    for source in sources:
        available.update(
            LEADERSHIP_BASES.get(match.group(0).casefold(), match.group(0).casefold())
            for match in LEADERSHIP_RE.finditer(source)
        )
    return not wanted or wanted <= available


def _title_supported(text: str, sources: list[str]) -> bool:
    source_tokens: set[str] = set()
    for source in sources:
        source_tokens.update(tokens(source))
    for match in TITLE_RE.finditer(text):
        wanted = tokens(match.group(0))
        if not wanted <= source_tokens:
            return False
    return True


def _date_supported(text: str, sources: list[str]) -> bool:
    wanted = {_normalized(match.group(0)) for match in MONTH_RE.finditer(text)}
    available = {
        _normalized(match.group(0)) for source in sources for match in MONTH_RE.finditer(source)
    }
    return not wanted or wanted <= available


def _team_size_supported(text: str, sources: list[str]) -> bool:
    return _numbers_supported(text, sources, set())


def _financial_supported(text: str, sources: list[str]) -> bool:
    wanted = _amounts(text)
    available: set[str] = set()
    for source in sources:
        available.update(_amounts(source))
    return wanted <= available


def _scope_supported(text: str, sources: list[str]) -> bool:
    return _numbers_supported(text, sources, set())


def _outcome_supported(text: str, sources: list[str]) -> bool:
    wanted = {
        OUTCOME_BASES.get(match.group(0).casefold(), match.group(0).casefold())
        for match in OUTCOME_RE.finditer(text)
    }
    available: set[str] = set()
    for source in sources:
        available.update(
            OUTCOME_BASES.get(match.group(0).casefold(), match.group(0).casefold())
            for match in OUTCOME_RE.finditer(source)
        )
    return not wanted or wanted <= available


def _claim_supported(
    claim: Claim,
    sources: list[str],
    allowed_numbers: set[str],
) -> bool:
    if claim.claim_type == "technology":
        return _technology_supported(claim.text, sources)
    if claim.claim_type == "metric":
        return _numbers_supported(claim.text, sources, allowed_numbers)
    if claim.claim_type == "date":
        return _date_supported(claim.text, sources)
    if claim.claim_type == "team_size":
        return _team_size_supported(claim.text, sources)
    if claim.claim_type == "financial":
        return _financial_supported(claim.text, sources)
    if claim.claim_type == "scope":
        return _scope_supported(claim.text, sources)
    if claim.claim_type == "leadership":
        return _leadership_supported(claim.text, sources)
    if claim.claim_type == "outcome":
        return _outcome_supported(claim.text, sources)
    if claim.claim_type == "title":
        return _title_supported(claim.text, sources)
    return True


def extract_claims(text: str, *, include_title: bool = False) -> list[Claim]:
    claims: list[Claim] = []
    seen: set[tuple[str, str]] = set()

    def add(value: str, claim_type: str) -> None:
        cleaned = re.sub(r"\s+", " ", value).strip()
        key = (claim_type, cleaned.casefold())
        if cleaned and key not in seen:
            seen.add(key)
            claims.append(Claim(text=cleaned, claim_type=claim_type))

    for canonical, _ in lexicon.find(text):
        add(canonical, "technology")
    for match in TEAM_SIZE_RE.finditer(text):
        add(match.group(0), "team_size")
    for match in FINANCIAL_RE.finditer(text):
        add(match.group(0), "financial")
    for match in SCOPE_RE.finditer(text):
        add(match.group(0), "scope")
    for match in MONTH_RE.finditer(text):
        add(match.group(0), "date")
    for match in LEADERSHIP_RE.finditer(text):
        add(match.group(0), "leadership")
    for match in OUTCOME_RE.finditer(text):
        add(match.group(0), "outcome")
    if numbers := _numbers(text):
        add(", ".join(sorted(numbers)), "metric")
    if include_title:
        for match in TITLE_RE.finditer(text):
            add(match.group(0), "title")
    return claims


def check_claims(
    text: str,
    sources: list[str],
    *,
    allowed_numbers: set[str] | None = None,
    include_title: bool = False,
) -> tuple[list[ClaimCheck], str, float]:
    best, score = best_source(text, sources)
    checks: list[ClaimCheck] = []
    for claim in extract_claims(text, include_title=include_title):
        supported = _claim_supported(claim, sources, allowed_numbers or set())
        checks.append(
            ClaimCheck(
                claim=claim,
                supported=supported,
                source=best,
                reason=(
                    "Claim is supported by source evidence."
                    if supported
                    else "Claim is not supported by source evidence."
                ),
            )
        )
    return checks, best, score
