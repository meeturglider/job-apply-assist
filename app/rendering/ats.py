from __future__ import annotations

import re
from pathlib import Path

from pypdf import PdfReader

from app.models import AtsCheck, AtsReport, JobDescription, TailoredResume
from app.skills import lexicon


def _keyword_in_text(keyword: str, text: str) -> bool:
    canonical = lexicon.canonicalize(keyword)
    aliases = lexicon.aliases_for(canonical) if canonical else [keyword]
    lowered = text.casefold()
    if keyword.casefold() in lowered:
        return True
    for alias in aliases:
        pattern = re.escape(alias).replace(r"\ ", r"[\s-]+")
        if re.search(rf"(?<![a-z0-9]){pattern}(?![a-z0-9])", text, re.IGNORECASE):
            return True
    return False


def check_ats(
    resume: TailoredResume,
    markdown: str,
    pdf_path: Path,
    job: JobDescription,
    html: str = "",
) -> AtsReport:
    checks: list[AtsCheck] = []
    reader = PdfReader(str(pdf_path))
    pdf_text = "\n".join(page.extract_text() or "" for page in reader.pages)
    text = f"{markdown}\n{pdf_text}"
    page_count = len(reader.pages)

    checks.append(
        AtsCheck(
            name="Selectable text",
            passed=len(pdf_text.strip()) > 700,
            detail=f"Extracted {len(pdf_text)} characters from the PDF.",
        )
    )
    checks.append(
        AtsCheck(
            name="Page length",
            passed=1 <= page_count <= 2,
            detail=f"PDF has {page_count} page(s).",
        )
    )
    contact_ok = bool(resume.header.email) and resume.header.email.casefold() in text.casefold()
    checks.append(
        AtsCheck(
            name="Contact details",
            passed=contact_ok,
            detail="Email is visible in the generated document."
            if contact_ok
            else "Email missing.",
        )
    )
    required_sections = ["professional summary", "skills", "professional experience", "education"]
    present = [section for section in required_sections if section in text.casefold()]
    checks.append(
        AtsCheck(
            name="Standard sections",
            passed=len(present) >= 3,
            detail="Found: " + ", ".join(present),
        )
    )
    no_tables = "<table" not in html.casefold() if html else True
    checks.append(
        AtsCheck(
            name="No ATS-breaking tables",
            passed=no_tables,
            detail="Single-column template without tables." if no_tables else "Table detected.",
        )
    )

    coverage: dict[str, bool] = {}
    for keyword in job.keywords:
        coverage[keyword] = _keyword_in_text(keyword, text)
    pct = (
        round(sum(1 for value in coverage.values() if value) / len(coverage) * 100, 1)
        if coverage
        else 100.0
    )
    checks.append(
        AtsCheck(
            name="Job keyword coverage",
            passed=pct >= 60,
            detail=f"{pct}% of JD keywords appear in the generated resume text.",
        )
    )

    must_have = [item.skill for item in job.must_have]
    missing = [skill for skill in must_have if not _keyword_in_text(skill, text)]
    checks.append(
        AtsCheck(
            name="Must-have keyword presence",
            passed=len(missing) <= max(1, len(must_have) // 4),
            detail=(
                "Missing: " + ", ".join(missing[:8])
                if missing
                else "All detected must-have skills are present."
            ),
        )
    )

    return AtsReport(
        checks=checks,
        keyword_coverage=coverage,
        keyword_coverage_pct=pct,
        page_count=page_count,
        text_length=len(pdf_text),
    )
