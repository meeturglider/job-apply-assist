from __future__ import annotations

from app.tailoring.claims import check_claims, extract_claims

SOURCE = (
    "Led a team of 6 engineers and reduced cloud spend by $250,000 across 3 regions "
    "using Kubernetes."
)


def _unsupported(text: str, source: str = SOURCE) -> set[str]:
    checks, _, _ = check_claims(text, [source])
    return {check.claim.claim_type for check in checks if not check.supported}


def test_extracts_fact_claim_types() -> None:
    claims = extract_claims(SOURCE)
    types = {claim.claim_type for claim in claims}
    assert {"technology", "team_size", "financial", "scope", "leadership", "outcome"} <= types


def test_supported_claims_pass_against_source() -> None:
    checks, source, score = check_claims(SOURCE, [SOURCE])
    assert all(check.supported for check in checks)
    assert source == SOURCE
    assert score == 1.0


def test_changed_metrics_team_size_and_scope_are_unsupported() -> None:
    text = (
        "Led a team of 12 engineers and reduced cloud spend by $2M across 8 regions "
        "using Kubernetes."
    )
    assert _unsupported(text) == {"team_size", "financial", "scope", "metric"}


def test_changed_dates_are_unsupported() -> None:
    source = "Migrated the platform in Jan 2020."
    text = "Migrated the platform in Jan 2021."
    assert "date" in _unsupported(text, source)


def test_title_claims_must_match_a_source_role() -> None:
    checks, _, _ = check_claims(
        "Principal Cloud Architect",
        ["Senior Platform Engineer"],
        include_title=True,
    )
    assert any(check.claim.claim_type == "title" and not check.supported for check in checks)


def test_unsupported_technology_is_detected() -> None:
    assert "technology" in _unsupported("Built services with Azure.", SOURCE)
