from __future__ import annotations

import asyncio

import pytest
from pydantic import BaseModel, Field

from app.config import Settings
from app.jd.analyze import analyze_job
from app.llm import LLMError, OllamaClient, _extract_json
from app.llm_schemas import LLMJobExtraction


class SampleSchema(BaseModel):
    title: str
    must_have: list[str] = Field(default_factory=list)


class StubClient(OllamaClient):
    def __init__(self, responses: list[object], *, retries: int = 1) -> None:
        super().__init__(
            Settings(
                llm_provider="ollama",
                ollama_model="test-model",
                ollama_max_retries=retries,
                ollama_retry_backoff_seconds=0,
            )
        )
        self.responses = responses
        self.calls = 0

    async def chat(
        self,
        system: str,
        prompt: str,
        *,
        json_mode: bool = True,
        temperature: float = 0.1,
        json_schema: dict[str, object] | None = None,
        max_tokens: int | None = None,
    ) -> str:
        self.last_schema = json_schema
        self.last_max_tokens = max_tokens
        self.calls += 1
        value = self.responses.pop(0)
        if isinstance(value, Exception):
            raise value
        return str(value)


def test_extract_json_repairs_trailing_commas_and_prose() -> None:
    value = 'Here is the result:\n```json\n{"title":"Role","items":["a",],}\n```\nThanks.'
    assert _extract_json(value) == {"title": "Role", "items": ["a"]}


def test_json_retries_invalid_json_then_validates_schema() -> None:
    client = StubClient(
        [
            "not json",
            '{"title": "Platform Engineer", "must_have": ["Kubernetes"]}',
        ]
    )
    data = asyncio.run(client.json("system", "prompt", schema=SampleSchema))
    assert data == {"title": "Platform Engineer", "must_have": ["Kubernetes"]}
    assert client.calls == 2
    assert client.last_attempts == 2
    assert client.last_schema and client.last_schema["type"] == "object"
    assert "must_have" in client.last_schema["properties"]


def test_json_forwards_output_budget_to_chat() -> None:
    client = StubClient(['{"title": "Platform Engineer"}'])
    asyncio.run(client.json("system", "prompt", schema=SampleSchema, max_tokens=4096))
    assert client.last_max_tokens == 4096


def test_json_retries_invalid_schema_then_succeeds() -> None:
    client = StubClient(
        [
            '{"title": "Platform Engineer", "must_have": "Kubernetes"}',
            '{"title": "Platform Engineer", "must_have": ["Kubernetes"]}',
        ]
    )
    data = asyncio.run(client.json("system", "prompt", schema=SampleSchema))
    assert data["must_have"] == ["Kubernetes"]
    assert client.calls == 2


def test_json_does_not_retry_non_retryable_timeout() -> None:
    client = StubClient([LLMError("timed out", retryable=False)])
    with pytest.raises(LLMError) as error:
        asyncio.run(client.json("system", "prompt"))
    assert client.calls == 1
    assert error.value.attempts == 1


def test_json_stops_after_configured_retries() -> None:
    client = StubClient(["not json", "still not json"], retries=1)
    with pytest.raises(LLMError) as error:
        asyncio.run(client.json("system", "prompt"))
    assert client.calls == 2
    assert error.value.attempts == 2
    assert "2 attempt" in str(error.value)


def test_analyze_job_tracks_llm_fallback_attempts() -> None:
    client = StubClient(["not json", "still not json"], retries=1)
    job = asyncio.run(
        analyze_job(
            "We need a platform engineer with Kubernetes and Terraform experience.",
            client,
        )
    )
    assert job.extraction_method == "heuristic"
    assert job.llm_attempts == 2
    assert job.extraction_fallback_reason
    assert any("LLM extraction failed" in warning for warning in job.warnings)


def test_job_schema_rejects_wrong_top_level_types() -> None:
    client = StubClient(
        [
            '{"title": "Platform Engineer", "must_have": "Kubernetes"}',
            (
                '{"title": "Platform Engineer", "must_have": '
                '[{"skill": "Kubernetes", "weight": 2.0}]}'
            ),
        ]
    )
    data = asyncio.run(
        client.json(
            "system",
            "prompt",
            schema=LLMJobExtraction,
            operation="jd_extract",
        )
    )
    assert data["title"] == "Platform Engineer"
    assert data["must_have"][0]["skill"] == "Kubernetes"
    assert client.calls == 2
