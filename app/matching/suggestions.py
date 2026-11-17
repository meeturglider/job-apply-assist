from __future__ import annotations

import json
import re

from app.llm import OllamaClient
from app.llm_schemas import LLMProfileSuggestions
from app.matching.matcher import literal_keyword_presence
from app.models import AnalysisResult, CandidateProfile, JobDescription, KeywordSuggestion
from app.prompts import load_prompt
from app.skills import lexicon

MAX_EVIDENCE_ITEMS = 80
MAX_SUGGESTIONS = 8


def _tokens(value: str) -> set[str]:
    return {token for token in re.findall(r"[a-z0-9]+", value.casefold()) if len(token) > 2}


def _grounded(evidence: dict[str, str], proposal: str) -> bool:
    haystack = _tokens(proposal)
    source = _tokens(evidence["source"])
    text = _tokens(evidence["text"])
    if source & haystack:
        return True
    return len(text & haystack) >= 2


def _evidence_items(profile: CandidateProfile) -> list[dict[str, str]]:
    items: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for item in profile.all_evidence():
        text = " ".join(item.text.split())[:600]
        if len(text) < 8:
            continue
        marker = (item.source.casefold(), text.casefold())
        if marker in seen:
            continue
        seen.add(marker)
        items.append({"source": item.source, "text": text})
        if len(items) >= MAX_EVIDENCE_ITEMS:
            break
    return items


async def enrich_profile_suggestions(
    job: JobDescription,
    profile: CandidateProfile,
    analysis: AnalysisResult,
    llm: OllamaClient,
) -> AnalysisResult:
    missing = [
        keyword.strip()
        for keyword in analysis.keyword_coverage
        if keyword.strip() and not literal_keyword_presence(keyword, profile)
    ]
    evidence = _evidence_items(profile)
    if not llm.enabled or not missing or not evidence:
        return analysis

    payload = {
        "keywords": missing[:24],
        "evidence": [{"index": index, **item} for index, item in enumerate(evidence)],
    }
    try:
        data = await llm.json(
            load_prompt("profile_suggestions.md"),
            json.dumps(payload, ensure_ascii=False),
            schema=LLMProfileSuggestions,
            operation="profile_suggest",
            temperature=0.1,
        )
    except Exception as exc:
        warnings = [*analysis.warnings, f"Semantic profile suggestions unavailable: {exc}"]
        return analysis.model_copy(update={"warnings": warnings})

    missing_lookup = {lexicon.normalize(keyword): keyword for keyword in missing}
    accepted = []
    seen_accepted: set[tuple[str, int]] = set()
    for item in data.get("suggestions", []):
        keyword = missing_lookup.get(lexicon.normalize(str(item.get("keyword", ""))))
        if keyword is None:
            continue
        index = int(item.get("evidence_index", -1))
        if index < 0 or index >= len(evidence):
            continue
        suggestion = " ".join(str(item.get("suggestion", "")).split())[:300]
        reason = " ".join(str(item.get("reason", "")).split())[:300]
        if not suggestion or not _grounded(evidence[index], f"{suggestion} {reason}"):
            continue
        marker = (keyword.casefold(), index)
        if marker in seen_accepted:
            continue
        seen_accepted.add(marker)
        accepted.append(
            {
                "keyword": keyword,
                "source": evidence[index]["source"],
                "suggestion": suggestion,
                "reason": reason,
                "evidence": evidence[index]["text"],
                "origin": "semantic",
            }
        )
        if len(accepted) >= MAX_SUGGESTIONS:
            break

    existing = [item.model_dump() for item in analysis.keyword_suggestions]
    merged: list[KeywordSuggestion] = []
    seen_merged: set[tuple[str, str]] = set()
    for item in [*existing, *accepted]:
        merged_key = (str(item["keyword"]).casefold(), str(item["suggestion"]).casefold())
        if merged_key in seen_merged:
            continue
        seen_merged.add(merged_key)
        merged.append(KeywordSuggestion.model_validate(item))
        if len(merged) >= MAX_SUGGESTIONS:
            break
    return analysis.model_copy(update={"keyword_suggestions": merged})
