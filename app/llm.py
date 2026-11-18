from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import Any

import httpx
from pydantic import BaseModel, ValidationError

from app.config import Settings

logger = logging.getLogger(__name__)


class LLMError(Exception):
    def __init__(self, message: str, *, retryable: bool = True, attempts: int = 0) -> None:
        super().__init__(message)
        self.retryable = retryable
        self.attempts = attempts


def _extract_json(value: str) -> dict[str, Any]:
    text = value.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise LLMError("The model did not return valid JSON.")
    candidate = text[start : end + 1]
    try:
        parsed = json.loads(candidate)
    except json.JSONDecodeError:
        cleaned = re.sub(r",\s*([}\]])", r"\1", candidate)
        try:
            parsed = json.loads(cleaned)
        except json.JSONDecodeError as exc:
            raise LLMError(f"The model returned invalid JSON: {exc}") from exc
    if not isinstance(parsed, dict):
        raise LLMError("The model returned JSON that is not an object.")
    return parsed


class OllamaClient:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.last_attempts = 0

    @property
    def enabled(self) -> bool:
        return self.settings.llm_provider.casefold() not in {"none", "off", "disabled"}

    async def models(self) -> list[str]:
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.get(f"{self.settings.ollama_base_url.rstrip('/')}/api/tags")
            response.raise_for_status()
            payload = response.json()
        return [str(item.get("name")) for item in payload.get("models", []) if item.get("name")]

    async def available(self) -> bool:
        if not self.enabled:
            return False
        try:
            models = await self.models()
        except Exception:
            return False
        wanted = self.settings.ollama_model.casefold()
        return any(
            name.casefold() == wanted or name.casefold().startswith(f"{wanted}:") for name in models
        )

    async def chat(
        self,
        system: str,
        prompt: str,
        *,
        json_mode: bool = True,
        temperature: float = 0.1,
        json_schema: dict[str, Any] | None = None,
        max_tokens: int | None = None,
    ) -> str:
        if not self.enabled:
            raise LLMError("LLM provider is disabled.", retryable=False)
        payload: dict[str, Any] = {
            "model": self.settings.ollama_model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": prompt},
            ],
            "stream": False,
            "options": {
                "temperature": temperature,
                "num_ctx": self.settings.ollama_num_ctx,
                "num_predict": max_tokens or self.settings.ollama_max_predict,
            },
        }
        if json_mode:
            payload["format"] = json_schema or "json"
        if "qwen3" in self.settings.ollama_model.casefold():
            payload["think"] = False
        url = f"{self.settings.ollama_base_url.rstrip('/')}/api/chat"
        timeout = float(self.settings.ollama_timeout_seconds)
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                response = await asyncio.wait_for(
                    client.post(url, json=payload),
                    timeout=timeout + 10.0,
                )
                response.raise_for_status()
                data = response.json()
        except (TimeoutError, httpx.TimeoutException) as exc:
            raise LLMError(
                f"Ollama did not respond within {int(timeout)} seconds. "
                "Try a smaller model or increase OLLAMA_TIMEOUT_SECONDS.",
                retryable=False,
            ) from exc
        except httpx.HTTPStatusError as exc:
            detail = str(exc) or type(exc).__name__
            retryable = exc.response.status_code in {408, 429} or exc.response.status_code >= 500
            raise LLMError(f"Ollama request failed: {detail}", retryable=retryable) from exc
        except httpx.HTTPError as exc:
            detail = str(exc) or type(exc).__name__
            raise LLMError(f"Ollama request failed: {detail}") from exc
        content = (data.get("message") or {}).get("content", "")
        if not content:
            raise LLMError("Ollama returned an empty response.")
        return str(content)

    async def json(
        self,
        system: str,
        prompt: str,
        *,
        temperature: float = 0.1,
        schema: type[BaseModel] | None = None,
        operation: str = "llm_json",
        max_retries: int | None = None,
        max_tokens: int | None = None,
    ) -> dict[str, Any]:
        retries = self.settings.ollama_max_retries if max_retries is None else max(0, max_retries)
        attempts = 1 + min(retries, 3)
        json_schema = schema.model_json_schema() if schema is not None else None
        last_error: LLMError | None = None
        for attempt in range(1, attempts + 1):
            try:
                content = await self.chat(
                    system,
                    prompt,
                    json_mode=True,
                    temperature=temperature,
                    json_schema=json_schema,
                    max_tokens=max_tokens,
                )
                data = _extract_json(content)
                if schema is not None:
                    try:
                        data = schema.model_validate(data).model_dump()
                    except ValidationError as exc:
                        raise LLMError(
                            f"The model returned JSON that did not match the expected schema: {exc}",
                            retryable=True,
                        ) from exc
                self.last_attempts = attempt
                logger.info(
                    "llm_json_succeeded",
                    extra={
                        "operation": operation,
                        "attempt": attempt,
                        "model": self.settings.ollama_model,
                    },
                )
                return data
            except LLMError as exc:
                exc.attempts = attempt
                last_error = exc
                self.last_attempts = attempt
                retryable = exc.retryable and attempt < attempts
                logger.warning(
                    "llm_json_attempt_failed",
                    extra={
                        "operation": operation,
                        "attempt": attempt,
                        "max_attempts": attempts,
                        "retryable": retryable,
                        "error": str(exc),
                        "model": self.settings.ollama_model,
                    },
                )
                if not retryable:
                    break
                backoff = max(0.0, self.settings.ollama_retry_backoff_seconds) * attempt
                if backoff:
                    await asyncio.sleep(backoff)
        error = last_error or LLMError("LLM JSON request failed.")
        raise LLMError(
            f"LLM JSON request failed after {error.attempts} attempt(s): {error}",
            retryable=False,
            attempts=error.attempts,
        ) from error
