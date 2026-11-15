from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

import trafilatura
from bs4 import BeautifulSoup

from app.jd.fetcher import ManualPasteRequired


@dataclass
class JobExtraction:
    text: str
    title: str = ""
    company: str = ""
    location: str = ""
    employment_type: str = ""
    method: str = "heuristic"
    source_url: str = ""
    warnings: list[str] = field(default_factory=list)
    job_posting: dict[str, Any] | None = None


def clean_text(value: str) -> str:
    value = value.replace("\r\n", "\n").replace("\r", "\n")
    value = re.sub(r"[ \t]+", " ", value)
    value = re.sub(r"\n{3,}", "\n\n", value)
    return value.strip()


def _html_to_text(value: str) -> str:
    soup = BeautifulSoup(value, "html.parser")
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    return clean_text(soup.get_text("\n"))


def _json_ld_documents(html: str) -> list[dict[str, Any]]:
    soup = BeautifulSoup(html, "html.parser")
    documents: list[dict[str, Any]] = []
    for script in soup.find_all("script", attrs={"type": "application/ld+json"}):
        if not script.string:
            continue
        try:
            payload = json.loads(script.string)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            if isinstance(payload.get("@graph"), list):
                documents.extend(item for item in payload["@graph"] if isinstance(item, dict))
            else:
                documents.append(payload)
        elif isinstance(payload, list):
            documents.extend(item for item in payload if isinstance(item, dict))
    return documents


def _job_posting(documents: list[dict[str, Any]]) -> dict[str, Any] | None:
    for document in documents:
        if document.get("@type") == "JobPosting":
            return document
    return None


def _location(posting: dict[str, Any]) -> str:
    locations = posting.get("jobLocation")
    if isinstance(locations, list):
        values = [_location({"jobLocation": item}) for item in locations]
        return "; ".join(value for value in values if value)
    if not isinstance(locations, dict):
        return ""
    address = locations.get("address")
    if isinstance(address, dict):
        parts = [
            str(address.get(key, "")).strip()
            for key in ("addressLocality", "addressRegion", "addressCountry")
        ]
        return ", ".join(part for part in parts if part)
    return str(locations.get("name", "")).strip()


def _organization(value: Any) -> str:
    if isinstance(value, dict):
        return str(value.get("name", "")).strip()
    return str(value or "").strip()


def extract_job(html: str, url: str = "") -> JobExtraction:
    documents = _json_ld_documents(html)
    posting = _job_posting(documents)
    warnings: list[str] = []

    if posting:
        description = str(posting.get("description", "") or "")
        description_text = (
            _html_to_text(description) if "<" in description else clean_text(description)
        )
        title = str(posting.get("title", "") or "").strip()
        company = _organization(posting.get("hiringOrganization"))
        location = _location(posting)
        employment_type = posting.get("employmentType", "")
        if isinstance(employment_type, list):
            employment_type = ", ".join(str(item) for item in employment_type)
        text = description_text
        method = "json-ld"
        if len(text) < 400:
            fallback = _generic_text(html, url)
            if len(fallback) > len(text):
                text = fallback
                method = "json-ld+readability"
        if len(text) < 400:
            raise ManualPasteRequired(
                "The job page did not expose enough readable text. "
                "Paste the job description text instead.",
                url,
            )
        return JobExtraction(
            text=text,
            title=title,
            company=company,
            location=location,
            employment_type=str(employment_type),
            method=method,
            warnings=warnings,
            job_posting=posting,
        )

    text = _generic_text(html, url)
    if len(text) < 400:
        raise ManualPasteRequired(
            "The job page did not expose enough readable text. "
            "Paste the job description text instead.",
            url,
        )
    warnings.append("No schema.org JobPosting metadata was found; using page text.")
    return JobExtraction(text=text, method="readability", warnings=warnings)


def _generic_text(html: str, url: str) -> str:
    extracted = trafilatura.extract(
        html,
        url=url or None,
        include_comments=False,
        include_tables=False,
        favor_recall=True,
    )
    if extracted and len(extracted) >= 400:
        return clean_text(extracted)

    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript", "nav", "header", "footer", "svg"]):
        tag.decompose()
    container = soup.find("main") or soup.find("article") or soup.body or soup
    return clean_text(container.get_text("\n"))


def manual_job_text(text: str, url: str = "") -> JobExtraction:
    cleaned = clean_text(text)
    if len(cleaned) < 100:
        raise ValueError("Please paste at least a few lines from the job description.")
    return JobExtraction(
        text=cleaned,
        source_url=url,
        method="manual",
        warnings=[],
    )
