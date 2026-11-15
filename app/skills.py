from __future__ import annotations

import re
from dataclasses import dataclass
from functools import cached_property
from pathlib import Path

import yaml


@dataclass(frozen=True)
class SkillEntry:
    canonical: str
    category: str
    aliases: tuple[str, ...]


class SkillLexicon:
    def __init__(self, entries: list[SkillEntry]) -> None:
        self.entries = entries
        self.by_canonical = {entry.canonical.casefold(): entry for entry in entries}
        self.alias_to_canonical: dict[str, str] = {}
        for entry in entries:
            names = {entry.canonical, *entry.aliases}
            for name in names:
                self.alias_to_canonical[self.normalize(name)] = entry.canonical
        aliases = sorted(self.alias_to_canonical, key=len, reverse=True)
        self._pattern = re.compile(
            r"(?<![a-z0-9])(" + "|".join(re.escape(alias) for alias in aliases) + r")(?![a-z0-9])",
            re.IGNORECASE,
        )

    @staticmethod
    def normalize(value: str) -> str:
        value = value.casefold()
        value = re.sub(r"[^a-z0-9+#./ -]+", " ", value)
        value = re.sub(r"\s+", " ", value)
        return value.strip(" -")

    @classmethod
    def load(cls, path: Path | None = None) -> SkillLexicon:
        source = path or Path(__file__).parent / "data" / "skills.yaml"
        raw = yaml.safe_load(source.read_text(encoding="utf-8")) or []
        entries = [
            SkillEntry(
                canonical=str(item["canonical"]),
                category=str(item.get("category", "")),
                aliases=tuple(str(alias) for alias in item.get("aliases", [])),
            )
            for item in raw
        ]
        return cls(entries)

    @cached_property
    def categories(self) -> dict[str, list[str]]:
        result: dict[str, list[str]] = {}
        for entry in self.entries:
            result.setdefault(entry.category or "Other", []).append(entry.canonical)
        return result

    def canonicalize(self, value: str) -> str:
        normalized = self.normalize(value)
        if not normalized:
            return ""
        if normalized in self.alias_to_canonical:
            return self.alias_to_canonical[normalized]
        matches = self.find(normalized)
        if matches:
            return max(matches, key=lambda item: len(item[0]))[0]
        return value.strip()

    def category_for(self, canonical: str) -> str:
        entry = self.by_canonical.get(canonical.casefold())
        return entry.category if entry else ""

    def find(self, text: str) -> list[tuple[str, int]]:
        """Return canonical skills found in text and their mention counts."""
        counts: dict[str, int] = {}
        for match in self._pattern.finditer(text):
            canonical = self.alias_to_canonical.get(self.normalize(match.group(1)))
            if canonical:
                counts[canonical] = counts.get(canonical, 0) + 1
        return sorted(counts.items(), key=lambda item: (-item[1], item[0]))

    def aliases_for(self, canonical: str) -> list[str]:
        entry = self.by_canonical.get(canonical.casefold())
        if not entry:
            return [canonical]
        return [entry.canonical, *entry.aliases]


lexicon = SkillLexicon.load()
