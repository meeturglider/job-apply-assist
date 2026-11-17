from __future__ import annotations

import asyncio
import json

from app.config import Settings
from app.llm import LLMError, OllamaClient
from app.matching.scorer import analyze_match
from app.matching.suggestions import enrich_profile_suggestions
from app.models import CandidateProfile, ExperienceEntry, JobDescription


class StubClient(OllamaClient):
    def __init__(self, response: object, *, retries: int = 0) -> None:
        super().__init__(
            Settings(
                llm_provider="ollama",
                ollama_model="test-model",
                ollama_max_retries=retries,
            )
        )
        self.response = response
        self.calls = 0
        self.system = ""
        self.prompt = ""

    async def chat(
        self,
        system: str,
        prompt: str,
        *,
        json_mode: bool = True,
        temperature: float = 0.1,
    ) -> str:
        self.calls += 1
        self.system = system
        self.prompt = prompt
        if isinstance(self.response, Exception):
            raise self.response
        return str(self.response)


def _job() -> JobDescription:
    job = JobDescription(title="Cloud Engineer")
    job.keywords = {"alphabet": 5.0, "COBOL": 1.0}
    return job


def test_semantic_suggestions_accept_grounded_proposals(profile: CandidateProfile) -> None:
    job = _job()
    analysis = analyze_match(job, profile)
    assert analysis.keyword_coverage["alphabet"] is False
    llm = StubClient(
        json.dumps(
            {
                "suggestions": [
                    {
                        "keyword": "alphabet",
                        "evidence_index": 0,
                        "suggestion": "Add Alphabet as the parent organization next to the GCP work.",
                        "reason": "Google Cloud Platform is an Alphabet product.",
                    },
                    {
                        "keyword": "COBOL",
                        "evidence_index": 99,
                        "suggestion": "Add COBOL.",
                        "reason": "The JD asks for it.",
                    },
                    {
                        "keyword": "Fortran",
                        "evidence_index": 0,
                        "suggestion": "Add Fortran.",
                        "reason": "Google Cloud Platform is an Alphabet product.",
                    },
                ]
            }
        )
    )

    result = asyncio.run(enrich_profile_suggestions(job, profile, analysis, llm))

    assert llm.calls == 1
    assert "suggest truthful profile wording" in llm.system
    semantic = [item for item in result.keyword_suggestions if item.origin == "semantic"]
    assert len(semantic) == 1
    assert semantic[0].keyword == "alphabet"
    assert "Alphabet" in semantic[0].suggestion
    assert semantic[0].evidence
    assert not analysis.keyword_suggestions


def test_entity_matched_keyword_still_requests_semantic_suggestion(
    profile: CandidateProfile,
) -> None:
    candidate = profile.model_copy(
        update={
            "experience": [
                ExperienceEntry(company="Google Operations Center", role="Operations Analyst"),
                *profile.experience,
            ]
        }
    )
    job = JobDescription(title="Cloud Engineer")
    job.keywords = {"alphabet": 5.0}
    analysis = analyze_match(job, candidate)
    assert analysis.keyword_coverage["alphabet"] is True
    llm = StubClient(json.dumps({"suggestions": []}))

    result = asyncio.run(enrich_profile_suggestions(job, candidate, analysis, llm))

    assert llm.calls == 1
    assert "alphabet" in llm.prompt
    assert len(result.keyword_suggestions) == 1
    assert result.keyword_suggestions[0].origin == "deterministic"


def test_semantic_suggestions_are_skipped_when_llm_is_disabled(
    profile: CandidateProfile,
) -> None:
    job = _job()
    analysis = analyze_match(job, profile)
    llm = OllamaClient(Settings(llm_provider="none"))

    result = asyncio.run(enrich_profile_suggestions(job, profile, analysis, llm))

    assert result == analysis


def test_semantic_suggestion_failure_keeps_deterministic_results(
    profile: CandidateProfile,
) -> None:
    job = _job()
    analysis = analyze_match(job, profile)
    llm = StubClient(LLMError("model unavailable", retryable=False))

    result = asyncio.run(enrich_profile_suggestions(job, profile, analysis, llm))

    assert result.keyword_suggestions == analysis.keyword_suggestions
    assert "Semantic profile suggestions unavailable" in result.warnings[-1]
