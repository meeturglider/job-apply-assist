from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from app.models import TailoredResume

TEMPLATE_DIR = Path(__file__).parent / "templates"


@lru_cache
def _environment() -> Environment:
    return Environment(
        loader=FileSystemLoader(TEMPLATE_DIR),
        autoescape=select_autoescape(["html", "xml"]),
        trim_blocks=True,
        lstrip_blocks=True,
    )


def resume_html(resume: TailoredResume) -> str:
    template = _environment().get_template("resume.html")
    return template.render(resume=resume)


def date_range(start: str, end: str) -> str:
    if start and end:
        return f"{start} – {end}"
    return start or end
