from __future__ import annotations

import hashlib
from pathlib import Path

PROMPT_DIR = Path(__file__).resolve().parent.parent / "prompts"


def load_prompt(name: str) -> str:
    return (PROMPT_DIR / name).read_text(encoding="utf-8")


def prompt_hash(name: str) -> str:
    return hashlib.sha256(load_prompt(name).encode("utf-8")).hexdigest()[:12]
