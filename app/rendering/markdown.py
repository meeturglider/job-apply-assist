from __future__ import annotations

from app.models import TailoredResume


def resume_markdown(resume: TailoredResume) -> str:
    lines: list[str] = []
    header = resume.header
    lines.append(f"# {header.name}")
    if header.title:
        lines.append(f"**{header.title}**")
    contact = [
        header.location,
        header.email,
        header.phone,
        header.linkedin,
        header.github,
        header.portfolio,
    ]
    contact_line = " | ".join(value for value in contact if value)
    if contact_line:
        lines.append(contact_line)
    lines.append("")

    if resume.summary:
        lines.extend(["## Professional Summary", "", resume.summary, ""])

    if resume.skills:
        lines.extend(["## Skills", ""])
        for group in resume.skills:
            lines.append(f"**{group.category}:** {', '.join(group.items)}")
        lines.append("")

    if resume.experience:
        lines.extend(["## Professional Experience", ""])
        for entry in resume.experience:
            if entry.roles:
                company_line = " | ".join(
                    value for value in [entry.company, entry.location] if value
                )
                lines.append(f"### {company_line or entry.company}")
                for role in entry.roles:
                    dates = _date_range(role.start, role.end)
                    lines.append(f"{role.role} | {dates}" if dates else role.role)
            else:
                lines.append(f"### {entry.role}")
                company_line = " | ".join(
                    value for value in [entry.company, entry.location] if value
                )
                dates = _date_range(entry.start, entry.end)
                if dates:
                    company_line = f"{company_line} | {dates}" if company_line else dates
                if company_line:
                    lines.append(company_line)
            for bullet in entry.bullets:
                lines.append(f"- {bullet}")
            lines.append("")

    if resume.projects:
        lines.extend(["## Selected Projects", ""])
        for project in resume.projects:
            lines.append(f"### {project.name}")
            meta = " | ".join(
                value for value in [project.context, project.status, project.period] if value
            )
            if meta:
                lines.append(meta)
            for bullet in project.bullets:
                lines.append(f"- {bullet}")
            if project.technologies:
                lines.append(f"**Technologies:** {', '.join(project.technologies)}")
            lines.append("")

    if resume.certifications:
        lines.extend(["## Certifications", ""])
        lines.extend(f"- {cert}" for cert in resume.certifications)
        lines.append("")

    if resume.education:
        lines.extend(["## Education", ""])
        lines.extend(f"- {value}" for value in resume.education)
        lines.append("")

    if resume.awards:
        lines.extend(["## Awards", ""])
        lines.extend(f"- {award}" for award in resume.awards)
        lines.append("")

    return "\n".join(lines).strip() + "\n"


def _date_range(start: str, end: str) -> str:
    if start and end:
        return f"{start} – {end}"
    return start or end
