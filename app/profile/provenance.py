from __future__ import annotations

from collections import Counter

from app.models import CandidateProfile, ProfileProvenance


def summarize_provenance(
    profile: CandidateProfile,
    *,
    resume_kind: str = "",
    resume_name: str = "",
    portfolio_url: str = "",
    portfolio_cached: bool = False,
    portfolio_fetched_at: str = "",
    extra_urls: list[str] | None = None,
    warnings: list[str] | None = None,
) -> ProfileProvenance:
    evidence_counts = {
        str(key): value
        for key, value in Counter(item.origin for item in profile.all_evidence()).items()
    }
    skill_counts = {
        str(key): value
        for key, value in Counter(
            item.origin for items in profile.skills.values() for item in items
        ).items()
    }
    return ProfileProvenance(
        resume_kind=resume_kind or profile.provenance.resume_kind,
        resume_name=resume_name or profile.provenance.resume_name,
        portfolio_url=portfolio_url or profile.provenance.portfolio_url,
        portfolio_cached=portfolio_cached or profile.provenance.portfolio_cached,
        portfolio_fetched_at=portfolio_fetched_at or profile.provenance.portfolio_fetched_at,
        extra_urls=extra_urls if extra_urls is not None else profile.provenance.extra_urls,
        evidence_counts=dict(evidence_counts),
        skill_counts=dict(skill_counts),
        warnings=warnings if warnings is not None else profile.warnings,
    )
