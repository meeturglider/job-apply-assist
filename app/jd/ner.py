from __future__ import annotations

import importlib
import re
from dataclasses import dataclass, field
from typing import Any

from app.config import Settings
from app.skills import lexicon

NER_LABELS = (
    "technical skill",
    "tool",
    "programming language",
    "framework",
    "cloud service",
    "platform",
    "methodology",
    "job title",
    "organization",
    "location",
    "certification",
    "degree",
    "years of experience",
    "responsibility",
)

SKILL_LABELS = {
    "technical skill",
    "tool",
    "programming language",
    "framework",
    "cloud service",
    "platform",
    "methodology",
}
HINT_LABELS = {"job title": "title", "organization": "company", "location": "location"}
DISABLED_PROVIDERS = {"", "none", "off", "disabled"}


@dataclass(frozen=True)
class NerCandidate:
    id: str
    text: str
    canonical: str
    label: str
    start: int
    end: int
    score: float
    source: str
    mentions: int = 1
    context: str = ""


@dataclass
class FastExtraction:
    candidates: list[NerCandidate] = field(default_factory=list)
    hints: dict[str, list[str]] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    model: str = ""


def _clean(value: object) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip(" ,.;:-")
    return text if 2 <= len(text) <= 120 else ""


def _chunks(text: str, max_chars: int, chunk_chars: int) -> list[tuple[int, str]]:
    bounded = text[:max_chars]
    if not bounded:
        return []
    overlap = min(200, chunk_chars // 4)
    step = max(1, chunk_chars - overlap)
    result: list[tuple[int, str]] = []
    for start in range(0, len(bounded), step):
        end = min(start + chunk_chars, len(bounded))
        result.append((start, bounded[start:end]))
        if end == len(bounded):
            break
    return result


def _entity_fields(entity: object) -> tuple[str, str, int, int, float] | None:
    if not isinstance(entity, dict):
        return None
    text = _clean(entity.get("text"))
    label = str(entity.get("label") or "").strip().casefold()
    try:
        start = int(entity.get("start", 0))
        end = int(entity.get("end", 0))
        score = float(entity.get("score", 0.0))
    except (TypeError, ValueError):
        return None
    if not text or not label or end <= start:
        return None
    return text, label, start, end, score


def _candidate_context(text: str, start: int, end: int) -> str:
    left = max(0, start - 80)
    right = min(len(text), end + 80)
    return re.sub(r"\s+", " ", text[left:right]).strip()


class GlinerExtractor:
    def __init__(
        self,
        *,
        provider: str,
        model_name: str,
        model_path: str = "",
        max_chars: int = 24000,
        chunk_chars: int = 4000,
        candidate_limit: int = 80,
        threshold: float = 0.4,
    ) -> None:
        self.provider = provider
        self.model = model_path or model_name
        self.max_chars = max_chars
        self.chunk_chars = chunk_chars
        self.candidate_limit = candidate_limit
        self.threshold = threshold
        self._model: Any | None = None
        self._load_warning = ""

    @classmethod
    def from_settings(cls, settings: Settings) -> GlinerExtractor:
        return cls(
            provider=settings.nlp_provider,
            model_name=settings.nlp_model,
            model_path=settings.nlp_model_path,
            max_chars=settings.nlp_max_chars,
            chunk_chars=settings.nlp_chunk_chars,
            candidate_limit=settings.nlp_candidate_limit,
            threshold=settings.nlp_threshold,
        )

    @property
    def enabled(self) -> bool:
        return self.provider.strip().casefold() not in DISABLED_PROVIDERS

    def load_model(self) -> Any | None:
        if self._model is not None:
            return self._model
        if self._load_warning:
            return None
        if not self.enabled:
            self._load_warning = "GLiNER extraction is disabled."
            return None
        try:
            module = importlib.import_module("gliner")
            self._model = module.GLiNER.from_pretrained(self.model)
            return self._model
        except Exception as exc:
            self._load_warning = f"GLiNER extraction unavailable: {exc}"
            return None

    def extract(self, text: str) -> FastExtraction:
        result = FastExtraction(model=self.model)
        if not text.strip() or not self.enabled:
            return result
        model = self.load_model()
        if model is None:
            if self._load_warning:
                result.warnings.append(self._load_warning)
            return result

        candidates: dict[tuple[str, str], NerCandidate] = {}
        try:
            for offset, chunk in _chunks(text, self.max_chars, self.chunk_chars):
                entities = model.predict_entities(chunk, list(NER_LABELS), threshold=self.threshold)
                for entity in entities or []:
                    fields = _entity_fields(entity)
                    if fields is None:
                        continue
                    value, label, relative_start, relative_end, score = fields
                    start = offset + relative_start
                    end = offset + relative_end
                    hint = HINT_LABELS.get(label)
                    if hint:
                        if value not in result.hints.setdefault(hint, []):
                            result.hints[hint].append(value)
                        continue
                    canonical = lexicon.canonicalize(value) if label in SKILL_LABELS else value
                    if not canonical:
                        continue
                    key = (label, lexicon.normalize(canonical))
                    existing = candidates.get(key)
                    if existing is not None:
                        candidates[key] = NerCandidate(
                            id=existing.id,
                            text=existing.text,
                            canonical=existing.canonical,
                            label=existing.label,
                            start=existing.start,
                            end=existing.end,
                            score=max(existing.score, score),
                            source=existing.source,
                            mentions=existing.mentions + 1,
                            context=existing.context,
                        )
                        continue
                    candidates[key] = NerCandidate(
                        id=f"ner:{lexicon.normalize(canonical) or len(candidates)}",
                        text=value,
                        canonical=canonical,
                        label=label,
                        start=start,
                        end=end,
                        score=score,
                        source="gliner",
                        context=_candidate_context(text, start, end),
                    )
        except Exception as exc:
            result.warnings.append(f"GLiNER extraction failed: {exc}")

        result.candidates = sorted(
            candidates.values(), key=lambda item: (-item.score, -item.mentions, item.id)
        )[: self.candidate_limit]
        return result


def warm_model(settings: Settings) -> str:
    extractor = GlinerExtractor.from_settings(settings)
    if not extractor.enabled:
        return "NLP_PROVIDER is disabled; no model loaded."
    if extractor.load_model() is None:
        raise RuntimeError(extractor._load_warning or "GLiNER model could not be loaded.")
    return f"GLiNER model ready: {extractor.model}"
