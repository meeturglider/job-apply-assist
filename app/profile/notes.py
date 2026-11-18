from __future__ import annotations

import re

from app.models import AnalysisNote, CandidateProfile, Evidence
from app.profile.provenance import summarize_provenance
from app.skills import lexicon
from app.tailoring.claims import extract_claims

CLAIM_TYPES = {
    "team_size",
    "financial",
    "scope",
    "date",
    "leadership",
    "outcome",
    "metric",
    "title",
}


def _mentions(text: str, target: str, aliases: list[str]) -> bool:
    needles = sorted({target, *aliases}, key=len, reverse=True)
    for needle in needles:
        cleaned = needle.strip()
        if not cleaned:
            continue
        pattern = re.escape(cleaned).replace(r"\ ", r"[\s-]+")
        if re.search(rf"(?<![a-z0-9]){pattern}(?![a-z0-9])", text, re.IGNORECASE):
            return True
    return False


def apply_notes(
    profile: CandidateProfile, notes: list[AnalysisNote]
) -> tuple[CandidateProfile, list[AnalysisNote]]:
    updated = profile.model_copy(deep=True)
    decisions: list[AnalysisNote] = []
    for note in notes:
        target = note.target.strip()
        if not target or not note.text.strip():
            decisions.append(
                note.model_copy(
                    update={"status": "rejected", "reason": "A target and text are required."}
                )
            )
            continue
        canonical = lexicon.canonicalize(target) or target
        aliases = lexicon.aliases_for(canonical)
        if not _mentions(note.text, target, aliases):
            decisions.append(
                note.model_copy(
                    update={
                        "status": "rejected",
                        "reason": f'The note must mention the target skill "{target}".',
                    }
                )
            )
            continue
        evidence = Evidence(
            source=f"User note · {target}",
            text=note.text.strip(),
            origin="user_note",
            origin_label="User note",
        )
        bucket = updated.skills.setdefault(canonical, [])
        if all(item.text != evidence.text or item.source != evidence.source for item in bucket):
            bucket.append(evidence)
        flags = sorted(
            {
                claim.claim_type
                for claim in extract_claims(note.text)
                if claim.claim_type in CLAIM_TYPES
            }
        )
        reason = "Accepted as application-specific evidence."
        if flags:
            reason += f" Review factual claims: {', '.join(flags)}."
        decisions.append(
            note.model_copy(update={"status": "accepted", "reason": reason, "claim_flags": flags})
        )
    updated.provenance = summarize_provenance(
        updated,
        resume_kind=updated.provenance.resume_kind,
        resume_name=updated.provenance.resume_name,
        portfolio_url=updated.provenance.portfolio_url,
        portfolio_cached=updated.provenance.portfolio_cached,
        portfolio_fetched_at=updated.provenance.portfolio_fetched_at,
        extra_urls=updated.provenance.extra_urls,
        warnings=updated.warnings,
    )
    return updated, decisions
