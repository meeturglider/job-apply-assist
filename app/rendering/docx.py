from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Inches, Pt

from app.models import TailoredResume

if TYPE_CHECKING:
    from docx.document import Document as DocxDocument

DocumentType = "DocxDocument"


def _set_cell_border(paragraph, size: int = 6) -> None:
    p_pr = paragraph._p.get_or_add_pPr()
    borders = p_pr.find(qn("w:pBdr"))
    if borders is None:
        borders = OxmlElement("w:pBdr")
        p_pr.append(borders)
    bottom = OxmlElement("w:bottom")
    bottom.set(qn("w:val"), "single")
    bottom.set(qn("w:sz"), str(size))
    bottom.set(qn("w:space"), "1")
    bottom.set(qn("w:color"), "444444")
    borders.append(bottom)


def _section_heading(document: DocxDocument, text: str) -> None:
    paragraph = document.add_paragraph()
    paragraph.paragraph_format.space_before = Pt(7)
    paragraph.paragraph_format.space_after = Pt(2)
    run = paragraph.add_run(text.upper())
    run.bold = True
    run.font.size = Pt(10)
    _set_cell_border(paragraph)


def _bullet(document: DocxDocument, text: str) -> None:
    paragraph = document.add_paragraph(text, style="List Bullet")
    paragraph.paragraph_format.space_after = Pt(1)
    paragraph.paragraph_format.left_indent = Inches(0.18)
    paragraph.paragraph_format.line_spacing = 1.0
    for run in paragraph.runs:
        run.font.size = Pt(10)


def _date_range(start: str, end: str) -> str:
    if start and end:
        return f"{start} – {end}"
    return start or end


def render_docx(resume: TailoredResume, output_path: Path) -> Path:
    document = Document()
    section = document.sections[0]
    section.top_margin = Inches(0.5)
    section.bottom_margin = Inches(0.5)
    section.left_margin = Inches(0.58)
    section.right_margin = Inches(0.58)

    styles = document.styles
    normal = styles["Normal"]
    normal.font.name = "Calibri"
    normal.font.size = Pt(10)
    normal.paragraph_format.space_after = Pt(1)
    normal.paragraph_format.line_spacing = 1.0

    header = resume.header
    name = document.add_paragraph()
    name.alignment = WD_ALIGN_PARAGRAPH.CENTER
    name_run = name.add_run(header.name)
    name_run.bold = True
    name_run.font.size = Pt(21)
    name.paragraph_format.space_after = Pt(1)

    if header.title:
        title = document.add_paragraph()
        title.alignment = WD_ALIGN_PARAGRAPH.CENTER
        title_run = title.add_run(header.title)
        title_run.bold = True
        title_run.font.size = Pt(10.5)
        title.paragraph_format.space_after = Pt(2)

    contact_values = [
        header.location,
        header.email,
        header.phone,
        header.linkedin,
        header.github,
        header.portfolio,
    ]
    contact_text = " | ".join(value for value in contact_values if value)
    if contact_text:
        contact = document.add_paragraph()
        contact.alignment = WD_ALIGN_PARAGRAPH.CENTER
        contact_run = contact.add_run(contact_text)
        contact_run.font.size = Pt(8.5)
        contact.paragraph_format.space_after = Pt(3)

    if resume.summary:
        _section_heading(document, "Professional Summary")
        document.add_paragraph(resume.summary)

    if resume.skills:
        _section_heading(document, "Skills")
        for group in resume.skills:
            paragraph = document.add_paragraph()
            paragraph.paragraph_format.space_after = Pt(0)
            label = paragraph.add_run(f"{group.category}: ")
            label.bold = True
            label.font.size = Pt(9.5)
            values = paragraph.add_run(", ".join(group.items))
            values.font.size = Pt(9.5)

    if resume.experience:
        _section_heading(document, "Professional Experience")
        for entry in resume.experience:
            role = document.add_paragraph()
            role.paragraph_format.space_before = Pt(3)
            role.paragraph_format.space_after = Pt(0)
            role_run = role.add_run(entry.role)
            role_run.bold = True
            role_run.font.size = Pt(10)
            dates = _date_range(entry.start, entry.end)
            if dates:
                dates_run = role.add_run(f"  |  {dates}")
                dates_run.font.size = Pt(9)

            company = document.add_paragraph()
            company.paragraph_format.space_after = Pt(1)
            company_run = company.add_run(
                " | ".join(value for value in [entry.company, entry.location] if value)
            )
            company_run.font.size = Pt(9)
            company_run.italic = True

            for bullet in entry.bullets:
                _bullet(document, bullet)

    if resume.projects:
        _section_heading(document, "Selected Projects")
        for project in resume.projects:
            title = document.add_paragraph()
            title.paragraph_format.space_before = Pt(3)
            title.paragraph_format.space_after = Pt(0)
            title_run = title.add_run(project.name)
            title_run.bold = True
            title_run.font.size = Pt(10)
            if project.period:
                title_run = title.add_run(f"  |  {project.period}")
                title_run.font.size = Pt(9)
            if project.context:
                context = document.add_paragraph()
                context.paragraph_format.space_after = Pt(1)
                context_run = context.add_run(project.context)
                context_run.font.size = Pt(9)
                context_run.italic = True
            for bullet in project.bullets:
                _bullet(document, bullet)
            if project.technologies:
                tech = document.add_paragraph()
                tech.paragraph_format.space_after = Pt(1)
                tech_label = tech.add_run("Technologies: ")
                tech_label.bold = True
                tech_label.font.size = Pt(9)
                tech_values = tech.add_run(", ".join(project.technologies))
                tech_values.font.size = Pt(9)

    if resume.certifications:
        _section_heading(document, "Certifications")
        for cert in resume.certifications:
            _bullet(document, cert)

    if resume.education:
        _section_heading(document, "Education")
        for item in resume.education:
            _bullet(document, item)

    if resume.awards:
        _section_heading(document, "Awards")
        for award in resume.awards:
            _bullet(document, award)

    document.core_properties.title = f"{header.name} — {header.title}"
    document.core_properties.author = header.name
    output_path.parent.mkdir(parents=True, exist_ok=True)
    document.save(str(output_path))
    return output_path
