from __future__ import annotations

import asyncio
import json
import re
from typing import Any

import httpx

from app.config import Settings


class LLMError(Exception):
    pass


def _extract_json(value: str) -> dict[str, Any]:
    text = value.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start == -1 or end == -1 or end <= start:
            raise LLMError("The model did not return valid JSON.") from None
        try:
            parsed = json.loads(text[start : end + 1])
        except json.JSONDecodeError as exc:
            raise LLMError(f"The model returned invalid JSON: {exc}") from exc
    if not isinstance(parsed, dict):
        raise LLMError("The model returned JSON that is not an object.")
    return parsed


class OllamaClient:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

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
    ) -> str:
        if not self.enabled:
            raise LLMError("LLM provider is disabled.")
        payload: dict[str, Any] = {
            "model": self.settings.ollama_model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": prompt},
            ],
            "stream": False,
            "options": {
                "temperature": temperature,
                "num_ctx": 16384,
                "num_predict": 2048,
            },
        }
        if json_mode:
            payload["format"] = "json"
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
                "Try a smaller model or increase OLLAMA_TIMEOUT_SECONDS."
            ) from exc
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
    ) -> dict[str, Any]:
        content = await self.chat(system, prompt, json_mode=True, temperature=temperature)
        return _extract_json(content)
