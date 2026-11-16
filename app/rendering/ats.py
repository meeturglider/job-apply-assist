from __future__ import annotations

import re
from pathlib import Path

from docx import Document as DocxDocument
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


def _docx_text(path: Path) -> str:
    document = DocxDocument(str(path))
    paragraphs = [paragraph.text for paragraph in document.paragraphs]
    for table in document.tables:
        for row in table.rows:
            for cell in row.cells:
                paragraphs.append(cell.text)
    return "\n".join(paragraphs)


def check_docx(resume: TailoredResume, docx_path: Path) -> list[AtsCheck]:
    checks: list[AtsCheck] = []
    try:
        text = _docx_text(docx_path)
    except Exception as exc:
        return [
            AtsCheck(
                name="DOCX text extraction",
                passed=False,
                detail=f"Could not read the generated DOCX: {exc}",
            )
        ]
    identity_ok = (
        len(text.strip()) > 300
        and resume.header.name.casefold() in text.casefold()
        and (not resume.header.email or resume.header.email.casefold() in text.casefold())
    )
    checks.append(
        AtsCheck(
            name="DOCX text extraction",
            passed=identity_ok,
            detail=(
                f"Extracted {len(text)} characters with candidate name and contact details."
                if identity_ok
                else "DOCX is missing selectable text, the candidate name, or contact details."
            ),
        )
    )
    companies = [entry.company for entry in resume.experience if entry.company]
    present = [company for company in companies if company.casefold() in text.casefold()]
    coverage_ok = bool(companies) and len(present) == len(companies)
    checks.append(
        AtsCheck(
            name="DOCX experience coverage",
            passed=coverage_ok,
            detail=f"{len(present)} of {len(companies)} experience entries found in the DOCX.",
        )
    )
    return checks


def check_ats(
    resume: TailoredResume,
    markdown: str,
    pdf_path: Path,
    job: JobDescription,
    html: str = "",
    docx_path: Path | None = None,
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
    name_ok = resume.header.name.casefold() in pdf_text.casefold()
    checks.append(
        AtsCheck(
            name="Candidate identity",
            passed=name_ok,
            detail=(
                "Candidate name is visible in the PDF."
                if name_ok
                else "Candidate name is missing from the PDF text."
            ),
        )
    )
    companies = [entry.company for entry in resume.experience if entry.company]
    companies_present = [
        company for company in companies if company.casefold() in pdf_text.casefold()
    ]
    experience_ok = bool(companies) and len(companies_present) == len(companies)
    checks.append(
        AtsCheck(
            name="PDF experience coverage",
            passed=experience_ok,
            detail=f"{len(companies_present)} of {len(companies)} experience entries found in the PDF.",
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
    if docx_path is not None:
        checks.extend(check_docx(resume, docx_path))

    return AtsReport(
        checks=checks,
        keyword_coverage=coverage,
        keyword_coverage_pct=pct,
        page_count=page_count,
        text_length=len(pdf_text),
    )
