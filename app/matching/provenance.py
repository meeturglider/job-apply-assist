from __future__ import annotations

from collections import Counter

from app.matching.matcher import entity_evidence_matches
from app.models import AnalysisResult, CandidateProfile, Evidence
from app.skills import lexicon


def evidence_for_keyword(keyword: str, profile: CandidateProfile) -> list[Evidence]:
    canonical = lexicon.canonicalize(keyword)
    results: list[Evidence] = []
    for skill, items in profile.skills.items():
        if lexicon.canonicalize(skill) == canonical:
            results.extend(items)
    results.extend(entity_evidence_matches(keyword, profile))
    needle = lexicon.normalize(keyword)
    if not results and needle:
        for item in profile.all_evidence():
            if needle in lexicon.normalize(item.text) or needle in lexicon.normalize(item.source):
                results.append(item)
    seen: set[tuple[str, str]] = set()
    unique: list[Evidence] = []
    for item in results:
        marker = (item.source, item.text)
        if marker in seen:
            continue
        seen.add(marker)
        unique.append(item)
    return unique


def _source_label(item: Evidence) -> str:
    label = item.origin_label or item.origin.replace("_", " ").title()
    return f"{label}: {item.source}" if item.source else label


def attach_analysis_provenance(
    analysis: AnalysisResult, profile: CandidateProfile
) -> AnalysisResult:
    keyword_sources: dict[str, list[str]] = {}
    keyword_origins: dict[str, list[str]] = {}
    for keyword in analysis.keyword_coverage:
        evidence = evidence_for_keyword(keyword, profile)
        keyword_sources[keyword] = [_source_label(item) for item in evidence]
        keyword_origins[keyword] = sorted({item.origin for item in evidence})

    matches = []
    for match in analysis.matches:
        matches.append(
            match.model_copy(
                update={"evidence_origins": dict(Counter(item.origin for item in match.evidence))}
            )
        )
    return analysis.model_copy(
        update={
            "matches": matches,
            "provenance": profile.provenance,
            "keyword_sources": keyword_sources,
            "keyword_origins": keyword_origins,
        }
    )
