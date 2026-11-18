from __future__ import annotations

import re
from types import SimpleNamespace

from app.config import Settings
from app.jd.analyze import extract_job_candidates, finalize_job_candidates
from app.jd.ner import GlinerExtractor


class FakeModel:
    def predict_entities(
        self, text: str, labels: list[str], *, threshold: float
    ) -> list[dict[str, object]]:
        result: list[dict[str, object]] = []
        for match in re.finditer("Kubernetes", text):
            result.append(
                {
                    "text": match.group(0),
                    "label": "technical skill",
                    "start": match.start(),
                    "end": match.end(),
                    "score": 0.9,
                }
            )
        return result


def test_gliner_extractor_chunks_dedupes_and_keeps_candidates(
    monkeypatch: object,
) -> None:
    fake_module = SimpleNamespace(GLiNER=SimpleNamespace(from_pretrained=lambda _name: FakeModel()))
    monkeypatch.setattr(  # type: ignore[attr-defined]
        "app.jd.ner.importlib.import_module", lambda _name: fake_module
    )
    settings = Settings(
        nlp_provider="gliner",
        nlp_max_chars=2400,
        nlp_chunk_chars=1000,
        nlp_candidate_limit=20,
    )

    extraction = GlinerExtractor.from_settings(settings).extract(
        "Kubernetes, Kubernetes, and Kubernetes on a platform team. " * 20
    )

    assert not extraction.warnings
    assert len(extraction.candidates) == 1
    assert extraction.candidates[0].canonical == "Kubernetes"
    assert extraction.candidates[0].mentions > 1
    assert extraction.candidates[0].context


def test_gliner_extractor_falls_back_when_package_missing(monkeypatch: object) -> None:
    def missing(_name: str) -> object:
        raise ImportError("gliner is not installed")

    monkeypatch.setattr("app.jd.ner.importlib.import_module", missing)  # type: ignore[attr-defined]
    settings = Settings(nlp_provider="gliner")

    extraction = GlinerExtractor.from_settings(settings).extract("Kubernetes required.")

    assert extraction.candidates == []
    assert extraction.warnings
    assert "GLiNER extraction unavailable" in extraction.warnings[0]


async def test_candidate_review_accepts_only_known_candidate_ids() -> None:
    class FakeLLM:
        enabled = True
        last_attempts = 1

        async def json(self, *args: object, **kwargs: object) -> dict[str, object]:
            return {
                "must_have_ids": ["rule:kubernetes", "rule:not-a-real-candidate"],
                "summary": "Kubernetes platform role.",
            }

    settings = Settings(nlp_provider="none")
    draft = extract_job_candidates(
        "We need a platform engineer with Kubernetes and Terraform experience.",
        settings,
        title="Platform Engineer",
    )

    job = await finalize_job_candidates(draft, FakeLLM())  # type: ignore[arg-type]

    assert "Kubernetes" in {item.skill for item in job.must_have}
    assert "rule:not-a-real-candidate" not in str(job.model_dump())
    assert job.extraction_method == "rules+llm-review"
    assert job.extraction_stages == ["rules", "llm-review"]
    assert job.llm_attempts == 1


async def test_candidate_review_can_demote_rule_candidate() -> None:
    class FakeLLM:
        enabled = True
        last_attempts = 1

        async def json(self, *args: object, **kwargs: object) -> dict[str, object]:
            return {
                "nice_to_have_ids": ["rule:kubernetes"],
                "summary": "Kubernetes is preferred, not mandatory.",
            }

    settings = Settings(nlp_provider="none")
    draft = extract_job_candidates(
        "We need a platform engineer with Kubernetes and Terraform experience.",
        settings,
        title="Platform Engineer",
    )

    job = await finalize_job_candidates(draft, FakeLLM())  # type: ignore[arg-type]

    assert "Kubernetes" not in {item.skill for item in job.must_have}
    assert "Kubernetes" in {item.skill for item in job.nice_to_have}


async def test_candidate_pipeline_works_without_llm() -> None:
    class DisabledLLM:
        enabled = False

    settings = Settings(nlp_provider="none")
    draft = extract_job_candidates(
        "Kubernetes and Terraform are required for this platform role.", settings
    )

    job = await finalize_job_candidates(draft, DisabledLLM())  # type: ignore[arg-type]

    assert {item.skill for item in job.must_have} >= {"Kubernetes", "Terraform"}
    assert job.extraction_method == "rules"
    assert job.llm_attempts == 0
