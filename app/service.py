from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
import uuid
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import quote

from fastapi.responses import Response

from app.artifacts import ArtifactStore, FileArtifactStore
from app.config import Settings
from app.jd.analyze import analyze_job
from app.jd.contacts import extract_contacts
from app.jd.extractor import JobExtraction, extract_job, manual_job_text
from app.jd.fetcher import FetchError, ManualPasteRequired, fetch_job_page
from app.llm import OllamaClient
from app.matching.matcher import literal_keyword_presence
from app.matching.provenance import attach_analysis_provenance
from app.matching.scorer import analyze_match
from app.matching.suggestions import enrich_profile_suggestions
from app.models import (
    AnalysisNote,
    AnalysisResult,
    ApplicationRecord,
    ArtifactInfo,
    AtsCheck,
    AtsReport,
    CandidateProfile,
    ContactCandidate,
    EmailDraft,
    JobDescription,
    PrefillFields,
    TrackerStatus,
    ValidationReport,
)
from app.profile.memory_service import MemoryProfileService
from app.profile.notes import apply_notes
from app.profile.service import ProfileService
from app.rendering.ats import check_ats, check_docx
from app.rendering.docx import render_docx
from app.rendering.html import resume_html
from app.rendering.markdown import resume_markdown
from app.rendering.pdf import RenderError, render_pdf
from app.store import ApplicationStore, MemoryApplicationStore
from app.tailoring.builder import build_resume

logger = logging.getLogger(__name__)


def _slug(value: str) -> str:
    value = re.sub(r"[^a-z0-9]+", "-", value.casefold()).strip("-")
    return value[:48] or "role"


def _notes_revision(notes: list[AnalysisNote]) -> str:
    payload = [
        {"target": note.target.strip(), "text": " ".join(note.text.split())} for note in notes
    ]
    encoded = json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()[:16]


class ApplicationService:
    def __init__(
        self,
        settings: Settings,
        *,
        store: ApplicationStore | MemoryApplicationStore | None = None,
        profiles: ProfileService | MemoryProfileService | None = None,
        artifacts: ArtifactStore | None = None,
    ) -> None:
        if store is None or profiles is None or artifacts is None:
            settings.ensure_dirs()
        self.settings = settings
        self.store = store or ApplicationStore(settings.database_path)
        self.profiles = profiles or ProfileService(settings)
        self.artifacts = artifacts or FileArtifactStore(settings.applications_dir)
        self.llm = OllamaClient(settings)
        self._tasks: set[asyncio.Task[None]] = set()

    def _new_record(
        self,
        company: str,
        role: str,
        source_url: str,
        profile_urls: list[str] | None = None,
    ) -> ApplicationRecord:
        now = datetime.now(UTC)
        application_id = (
            f"{now.strftime('%Y%m%d-%H%M%S')}-{_slug(company or role)}-{uuid.uuid4().hex[:4]}"
        )
        record = ApplicationRecord(
            id=application_id,
            created_at=now,
            updated_at=now,
            company=company,
            role=role,
            source_url=source_url,
            profile_urls=profile_urls or [],
            status="queued",
            stage="queued",
            message="Queued for analysis.",
            model=self.settings.ollama_model,
        )
        self.store.save(record)
        logger.info(
            "application_created",
            extra={"application_id": application_id, "has_url": bool(source_url)},
        )
        return record

    def _update(self, record: ApplicationRecord, **fields: object) -> ApplicationRecord:
        for key, value in fields.items():
            setattr(record, key, value)
        record.updated_at = datetime.now(UTC)
        self.store.save(record)
        return record

    def _directory(self, application_id: str) -> Path:
        directory = self.settings.applications_dir / application_id
        directory.mkdir(parents=True, exist_ok=True)
        return directory

    def _write(self, application_id: str, name: str, data: str | bytes) -> None:
        self.artifacts.write(application_id, name, data)

    def _read_text(self, application_id: str, name: str) -> str:
        return self.artifacts.read_text(application_id, name)

    def _exists(self, application_id: str, name: str) -> bool:
        return self.artifacts.exists(application_id, name)

    def _track(self, task: asyncio.Task[None]) -> None:
        self._tasks.add(task)

        def done(completed: asyncio.Task[None]) -> None:
            self._tasks.discard(completed)
            if completed.cancelled():
                logger.info("background_task_cancelled")
                return
            error = completed.exception()
            if error is not None:
                logger.error(
                    "background_task_failed",
                    exc_info=(type(error), error, error.__traceback__),
                )

        task.add_done_callback(done)

    async def shutdown(self) -> None:
        tasks = [task for task in self._tasks if not task.done()]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        logger.info("service_stopped", extra={"cancelled_tasks": len(tasks)})

    def start_analysis(
        self,
        *,
        url: str = "",
        text: str = "",
        company: str = "",
        role: str = "",
        profile_urls: list[str] | None = None,
    ) -> ApplicationRecord:
        record = self._new_record(company, role, url, profile_urls)
        task = asyncio.create_task(
            self._analyze(
                record.id,
                url=url,
                text=text,
                company=company,
                role=role,
                profile_urls=record.profile_urls,
            )
        )
        self._track(task)
        return record

    async def _analyze(
        self,
        application_id: str,
        *,
        url: str,
        text: str,
        company: str,
        role: str,
        profile_urls: list[str],
    ) -> None:
        record = self.store.get(application_id)
        if record is None:
            return
        logger.info("analysis_started", extra={"application_id": application_id})
        try:
            self._update(
                record,
                status="analyzing",
                stage="fetching",
                message="Fetching the job description...",
            )
            extraction = await self._ingest(url=url, text=text)
            extraction = JobExtraction(
                text=extraction.text,
                title=role or extraction.title,
                company=company or extraction.company,
                location=extraction.location,
                employment_type=extraction.employment_type,
                method=extraction.method,
                warnings=extraction.warnings,
                source_url=url or extraction.source_url,
                job_posting=extraction.job_posting,
            )
            self._save_jd(application_id, extraction)

            self._update(
                record,
                company=extraction.company or record.company,
                role=extraction.title or record.role,
                stage="matching",
                message="Loading your resume and portfolio...",
            )
            profile = await self.profiles.load(extra_urls=profile_urls)
            self._write(application_id, "profile.json", profile.model_dump_json(indent=2))

            self._update(
                record,
                stage="extracting",
                message="Extracting requirements with the local model...",
            )
            job = await analyze_job(
                extraction.text,
                self.llm,
                title=extraction.title,
                company=extraction.company,
                location=extraction.location,
                source_url=url or extraction.source_url,
            )
            contacts = extract_contacts(
                extraction.text,
                job_posting=extraction.job_posting,
                source_url=url or extraction.source_url,
            )
            job.contacts = contacts
            self._update(
                record,
                stage="scoring",
                message="Matching evidence and calculating your score...",
            )
            analysis = analyze_match(job, profile)
            self._update(
                record,
                stage="suggestions",
                message="Checking profile suggestions with the local model...",
            )
            analysis = await enrich_profile_suggestions(job, profile, analysis, self.llm)
            analysis = self._finalize_analysis(analysis, profile)

            self._write(application_id, "job.json", job.model_dump_json(indent=2))
            self._write(application_id, "analysis.json", analysis.model_dump_json(indent=2))
            self._update(
                record,
                status="analyzed",
                stage="done",
                message=f"{analysis.score_label}: {analysis.score.total}% match.",
                score=analysis.score.total,
                company=job.company or record.company,
                role=job.title or record.role,
                profile_hash=ProfileService.hash(profile),
                contacts=contacts,
                tracker_status="analyzed",
            )
        except ManualPasteRequired as exc:
            logger.warning(
                "analysis_needs_manual_paste",
                extra={"application_id": application_id},
            )
            self._update(
                record,
                status="needs_paste",
                stage="manual_paste",
                message=exc.message,
                error=exc.message,
            )
        except FetchError as exc:
            logger.warning(
                "analysis_fetch_failed",
                extra={"application_id": application_id, "error": str(exc)},
            )
            self._update(
                record,
                status="error",
                stage="error",
                message=str(exc),
                error=str(exc),
            )
        except Exception:
            logger.exception("analysis_failed", extra={"application_id": application_id})
            self._update(
                record,
                status="error",
                stage="error",
                message="Analysis failed unexpectedly. Check the app logs for details.",
                error="internal_error",
            )

    def _finalize_analysis(
        self, analysis: AnalysisResult, profile: CandidateProfile
    ) -> AnalysisResult:
        suggestions = [
            item
            for item in analysis.keyword_suggestions
            if not literal_keyword_presence(item.keyword, profile)
        ]
        return attach_analysis_provenance(
            analysis.model_copy(update={"keyword_suggestions": suggestions}), profile
        )

    async def _ingest(self, *, url: str, text: str) -> JobExtraction:
        if text.strip() and len(text.strip()) >= 100:
            return manual_job_text(text, url)
        if not url.strip():
            raise FetchError("Provide a job URL or paste the job description text.")
        fetched = await fetch_job_page(url, self.settings)
        extraction = extract_job(fetched.html, fetched.final_url)
        extraction.warnings.extend(fetched.warnings)
        extraction.source_url = fetched.final_url
        return extraction

    def _save_jd(self, application_id: str, extraction: JobExtraction) -> None:
        self._write(application_id, "jd.md", extraction.text)
        metadata = {
            "source_url": extraction.source_url,
            "title": extraction.title,
            "company": extraction.company,
            "location": extraction.location,
            "employment_type": extraction.employment_type,
            "method": extraction.method,
            "warnings": extraction.warnings,
            "fetched_at": datetime.now(UTC).isoformat(),
        }
        self._write(
            application_id,
            "jd.json",
            json.dumps(metadata, indent=2, ensure_ascii=False),
        )

    def save_notes(self, application_id: str, entries: list[dict[str, str]]) -> ApplicationRecord:
        record = self.store.get(application_id)
        if record is None:
            raise ValueError("Application not found.")
        if record.status in {"queued", "analyzing", "generating"}:
            raise ValueError("Wait for the current task to finish before editing notes.")
        notes = [
            AnalysisNote(target=item["target"].strip(), text=item["text"].strip())
            for item in entries
        ]
        revision = _notes_revision(notes)
        self._write(
            application_id,
            "notes.json",
            json.dumps([item.model_dump(mode="json") for item in notes], indent=2),
        )
        self.artifacts.delete(application_id, "profile.noted.json")
        return self._update(
            record,
            status="analyzed",
            stage="notes",
            message="Evidence notes changed. Revalidate before generating.",
            notes=notes,
            notes_revision=revision,
            notes_validated_revision="",
            artifacts=[],
            error="",
        )

    def start_revalidation(self, application_id: str) -> ApplicationRecord:
        record = self.store.get(application_id)
        if record is None:
            raise ValueError("Application not found.")
        if record.status not in {"analyzed", "generated"}:
            raise ValueError("Analyze the job before revalidating notes.")
        if not self._exists(application_id, "job.json") or not self._exists(
            application_id, "profile.json"
        ):
            raise ValueError("Analyze the job before revalidating notes.")
        self._update(
            record,
            status="analyzing",
            stage="notes",
            message="Revalidating with your evidence notes...",
        )
        task = asyncio.create_task(self._revalidate(application_id))
        self._track(task)
        return record

    async def _revalidate(self, application_id: str) -> None:
        record = self.store.get(application_id)
        if record is None:
            return
        try:
            job = JobDescription.model_validate_json(self._read_text(application_id, "job.json"))
            profile = CandidateProfile.model_validate_json(
                self._read_text(application_id, "profile.json")
            )
            if self._exists(application_id, "analysis.json") and not self._exists(
                application_id, "analysis.baseline.json"
            ):
                self._write(
                    application_id,
                    "analysis.baseline.json",
                    self.artifacts.read_bytes(application_id, "analysis.json"),
                )

            noted_profile, decisions = apply_notes(profile, record.notes)
            self._write(
                application_id,
                "notes.json",
                json.dumps([item.model_dump(mode="json") for item in decisions], indent=2),
            )
            self._write(
                application_id,
                "profile.noted.json",
                noted_profile.model_dump_json(indent=2),
            )

            self._update(
                record,
                stage="scoring",
                message="Scoring your evidence notes...",
            )
            analysis = analyze_match(job, noted_profile)
            self._update(
                record,
                stage="suggestions",
                message="Refreshing profile suggestions...",
            )
            analysis = await enrich_profile_suggestions(job, noted_profile, analysis, self.llm)
            analysis = self._finalize_analysis(analysis, noted_profile)
            self._write(application_id, "analysis.json", analysis.model_dump_json(indent=2))

            accepted = sum(item.status == "accepted" for item in decisions)
            rejected = sum(item.status == "rejected" for item in decisions)
            self._update(
                record,
                status="analyzed",
                stage="done",
                message=(
                    f"{analysis.score_label}: {analysis.score.total}% match. "
                    f"Revalidated {len(decisions)} note(s): {accepted} accepted, {rejected} rejected."
                ),
                score=analysis.score.total,
                profile_hash=ProfileService.hash(noted_profile),
                notes=decisions,
                notes_validated_revision=record.notes_revision,
            )
            logger.info(
                "revalidation_completed",
                extra={"application_id": application_id, "notes": len(decisions)},
            )
        except Exception:
            logger.exception("revalidation_failed", extra={"application_id": application_id})
            self._update(
                record,
                status="error",
                stage="error",
                message="Revalidation failed unexpectedly. Check the app logs for details.",
                error="internal_error",
            )

    def start_generation(self, application_id: str) -> ApplicationRecord:
        record = self.store.get(application_id)
        if record is None:
            raise ValueError("Application not found.")
        if record.status not in {"analyzed", "generated"}:
            raise ValueError("Analyze the job before generating a resume.")
        if record.notes_revision != record.notes_validated_revision:
            raise ValueError("Revalidate your evidence notes before generating.")
        logger.info("generation_started", extra={"application_id": application_id})
        self._update(
            record,
            status="generating",
            stage="tailoring",
            message="Building a tailored resume...",
        )
        task = asyncio.create_task(self._generate(application_id))
        self._track(task)
        return record

    async def _generate(self, application_id: str) -> None:
        record = self.store.get(application_id)
        if record is None:
            return
        logger.info("generation_rendering", extra={"application_id": application_id})
        try:
            job = JobDescription.model_validate_json(self._read_text(application_id, "job.json"))
            analysis = AnalysisResult.model_validate_json(
                self._read_text(application_id, "analysis.json")
            )
            profile_name = (
                "profile.noted.json"
                if self._exists(application_id, "profile.noted.json")
                else "profile.json"
            )
            profile = CandidateProfile.model_validate_json(
                self._read_text(application_id, profile_name)
            )
            resume, validation, method = await build_resume(profile, job, analysis, self.llm)
            self._update(
                record,
                stage="rendering",
                message="Rendering Markdown, HTML, DOCX, and PDF...",
            )
            markdown = resume_markdown(resume)
            html = resume_html(resume)
            self._write(application_id, "resume.json", resume.model_dump_json(indent=2))
            self._write(application_id, "resume.md", markdown)
            self._write(application_id, "resume.html", html)
            self._write(application_id, "validation.json", validation.model_dump_json(indent=2))

            artifacts: list[ArtifactInfo] = []
            artifacts.append(self._artifact(application_id, "markdown", "resume.md"))
            artifacts.append(self._artifact(application_id, "html", "resume.html"))

            pdf_error = ""
            ats_report: AtsReport
            with self.artifacts.render_dir(application_id) as render_dir:
                docx_path = render_dir / "resume.docx"
                render_docx(resume, docx_path)
                self._write(application_id, "resume.docx", docx_path.read_bytes())
                artifacts.append(self._artifact(application_id, "docx", "resume.docx"))

                pdf_path = render_dir / "resume.pdf"
                try:
                    await render_pdf(html, pdf_path)
                    self._write(application_id, "resume.pdf", pdf_path.read_bytes())
                    artifacts.append(self._artifact(application_id, "pdf", "resume.pdf"))
                except RenderError as exc:
                    pdf_error = str(exc)

                if pdf_error:
                    checks = [AtsCheck(name="PDF rendering", passed=False, detail=pdf_error)]
                    try:
                        checks.extend(check_docx(resume, docx_path))
                    except Exception as exc:
                        checks.append(
                            AtsCheck(
                                name="DOCX text extraction",
                                passed=False,
                                detail=f"DOCX verification failed: {exc}",
                            )
                        )
                    ats_report = AtsReport(checks=checks)
                else:
                    try:
                        ats_report = check_ats(resume, markdown, pdf_path, job, html, docx_path)
                    except Exception as exc:
                        ats_report = AtsReport(
                            checks=[
                                AtsCheck(
                                    name="ATS inspection",
                                    passed=False,
                                    detail=f"ATS text extraction failed: {exc}",
                                )
                            ]
                        )
            self._write(application_id, "ats_report.json", ats_report.model_dump_json(indent=2))

            metadata = {
                "application_id": application_id,
                "generated_at": datetime.now(UTC).isoformat(),
                "method": method,
                "model": self.settings.ollama_model,
                "job_title": job.title,
                "company": job.company,
                "score": analysis.score.total,
                "validation": validation.model_dump(),
                "ats": ats_report.model_dump(),
                "pdf_error": pdf_error,
                "notes_revision": record.notes_revision,
                "evidence_sources": profile.provenance.model_dump(),
            }
            self._write(
                application_id,
                "metadata.json",
                json.dumps(metadata, indent=2, ensure_ascii=False),
            )
            method_labels = {
                "llm": "LLM tailoring",
                "llm-sanitized": "LLM tailoring with sanitization",
                "deterministic": "deterministic selection",
                "deterministic-fallback": "deterministic fallback",
            }
            message = f"Resume generated with {method_labels.get(method, method)}."
            if pdf_error:
                message += " PDF rendering needs Playwright Chromium: playwright install chromium."
            self._update(
                record,
                status="generated",
                stage="done",
                message=message,
                error=pdf_error,
                artifacts=artifacts,
            )
            logger.info(
                "generation_completed",
                extra={
                    "application_id": application_id,
                    "method": method,
                    "artifact_count": len(artifacts),
                },
            )
        except Exception:
            logger.exception("generation_failed", extra={"application_id": application_id})
            self._update(
                record,
                status="error",
                stage="error",
                message="Resume generation failed unexpectedly. Check the app logs for details.",
                error="internal_error",
            )

    def _artifact(self, application_id: str, kind: str, filename: str) -> ArtifactInfo:
        return ArtifactInfo(
            kind=kind,
            filename=filename,
            size=self.artifacts.size(application_id, filename),
        )

    def get(self, application_id: str) -> ApplicationRecord | None:
        return self.store.get(application_id)

    def list(self, limit: int = 50) -> list[ApplicationRecord]:
        return self.store.list(limit)

    def _allowed_artifact(self, application_id: str, filename: str) -> None:
        record = self.store.get(application_id)
        if record is None:
            raise FileNotFoundError("Application not found.")
        allowed = {artifact.filename for artifact in record.artifacts}
        if filename not in allowed:
            raise FileNotFoundError("Artifact not found.")

    def artifact_path(self, application_id: str, filename: str) -> Path:
        self._allowed_artifact(application_id, filename)
        if not isinstance(self.artifacts, FileArtifactStore):
            raise FileNotFoundError("Artifacts are held in memory for this session.")
        path = self.artifacts.path(application_id, filename)
        if not path.exists():
            raise FileNotFoundError("Artifact file is missing.")
        return path

    def artifact_response(self, application_id: str, filename: str, *, download: bool) -> Response:
        self._allowed_artifact(application_id, filename)
        media_types = {
            ".pdf": "application/pdf",
            ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            ".md": "text/markdown",
            ".html": "text/html",
        }
        suffix = Path(filename).suffix
        previewable = {".html", ".md"}
        disposition = "inline" if suffix in previewable and not download else "attachment"
        return self.artifacts.response(
            application_id,
            filename,
            media_types.get(suffix, "application/octet-stream"),
            disposition,
        )

    def analysis_payload(self, application_id: str) -> dict[str, object]:
        record = self.store.get(application_id)
        if record is None:
            raise FileNotFoundError("Application not found.")
        payload: dict[str, object] = {"record": record.model_dump(mode="json")}
        for name in (
            "job",
            "analysis",
            "profile",
            "profile.noted",
            "notes",
            "ats_report",
            "validation",
            "resume",
            "metadata",
        ):
            filename = f"{name}.json"
            if self._exists(application_id, filename):
                payload[name] = json.loads(self._read_text(application_id, filename))
        if self._exists(application_id, "resume.md"):
            payload["resume_markdown"] = self._read_text(application_id, "resume.md")
        validation = payload.get("validation")
        if isinstance(validation, dict):
            payload["validation"] = ValidationReport.model_validate(validation).model_dump(
                mode="json"
            )
        return payload

    def _read_profile(self, application_id: str) -> CandidateProfile:
        name = (
            "profile.noted.json"
            if self._exists(application_id, "profile.noted.json")
            else "profile.json"
        )
        return CandidateProfile.model_validate_json(self._read_text(application_id, name))

    def add_contact(self, application_id: str, email: str, name: str = "") -> ApplicationRecord:
        record = self.store.get(application_id)
        if record is None:
            raise ValueError("Application not found.")
        cleaned = email.strip().strip(".,;:()[]<>")
        if not re.fullmatch(r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}", cleaned, re.IGNORECASE):
            raise ValueError("Enter a valid contact email address.")
        contacts = [item for item in record.contacts if item.email.casefold() != cleaned.casefold()]
        contacts.append(
            ContactCandidate(
                email=cleaned,
                name=name.strip()[:120],
                source="Manual entry",
                confidence=1,
                confirmed=True,
            )
        )
        return self._update(
            record,
            contacts=contacts[:10],
            selected_contact=cleaned,
        )

    def draft_outreach(
        self,
        application_id: str,
        *,
        contact_email: str = "",
        user_note: str = "",
    ) -> dict[str, object]:
        record = self.store.get(application_id)
        if record is None:
            raise ValueError("Application not found.")
        if record.status not in {"analyzed", "generated"}:
            raise ValueError("Analyze the job before drafting an email.")
        if contact_email:
            record = self.add_contact(application_id, contact_email)
        selected = record.selected_contact
        if not selected:
            confirmed = [item for item in record.contacts if item.confirmed]
            candidates = confirmed or record.contacts
            if not candidates:
                raise ValueError("Add or select a recruiter contact first.")
            selected = candidates[0].email
            record = self._update(record, selected_contact=selected)
        job = JobDescription.model_validate_json(self._read_text(application_id, "job.json"))
        analysis = AnalysisResult.model_validate_json(
            self._read_text(application_id, "analysis.json")
        )
        profile = self._read_profile(application_id)
        contact = next(
            (item for item in record.contacts if item.email.casefold() == selected.casefold()),
            None,
        )
        greeting = "Hello," if not contact or not contact.name else f"Hello {contact.name},"
        name = profile.identity.name or "Candidate"
        title = job.title or record.role or "the role"
        company = job.company or record.company or "your team"
        headline = profile.identity.headline
        years = profile.experience_years
        skills = ", ".join(analysis.matched_skills[:6]) or "the role-fit skills in my resume"
        note = " ".join(user_note.split())[:1000]
        lines = [
            greeting,
            "",
            f"I'm {name}, and I'm applying for the {title} role at {company}.",
        ]
        if headline and years:
            lines.append(f"I bring {years:g} years of experience in {headline}.")
        elif years:
            lines.append(f"I bring {years:g} years of professional experience.")
        lines.append(f"My profile includes evidence for: {skills}.")
        if note:
            lines.extend(["", note])
        lines.extend(
            [
                "",
                "I've attached a tailored resume and would be glad to discuss how I can help.",
                "",
                "Best regards,",
                name,
                profile.identity.email,
            ]
        )
        draft = EmailDraft(
            to=selected,
            subject=f"Application for {title} - {name}",
            body="\n".join(lines).strip(),
            source="deterministic-evidence",
        )
        self._update(
            record,
            email_draft=draft,
            selected_contact=selected,
            tracker_status="email_drafted",
        )
        href = f"mailto:{quote(selected)}?subject={quote(draft.subject)}&body={quote(draft.body)}"
        return {"draft": draft.model_dump(mode="json"), "mailto": href}

    def update_tracker(self, application_id: str, status: TrackerStatus) -> ApplicationRecord:
        record = self.store.get(application_id)
        if record is None:
            raise ValueError("Application not found.")
        return self._update(record, tracker_status=status)

    def prefill_payload(self, application_id: str) -> dict[str, object]:
        record = self.store.get(application_id)
        if record is None:
            raise ValueError("Application not found.")
        if record.status not in {"analyzed", "generated"}:
            raise ValueError("Analyze the job before pre-filling an application.")
        profile = self._read_profile(application_id)
        analysis = AnalysisResult.model_validate_json(
            self._read_text(application_id, "analysis.json")
        )
        fields = PrefillFields(
            name=profile.identity.name,
            email=profile.identity.email,
            phone=profile.identity.phone,
            linkedin=profile.identity.linkedin,
            github=profile.identity.github,
            portfolio=profile.identity.portfolio,
            role=analysis.job.title or record.role,
            company=analysis.job.company or record.company,
            location=analysis.job.location,
            experience_years=profile.experience_years,
            skills=list(analysis.matched_skills),
        )
        markdown = (
            self._read_text(application_id, "resume.md")
            if self._exists(application_id, "resume.md")
            else ""
        )
        status = record.tracker_status if record.tracker_status == "submitted" else "prefilled"
        self._update(
            record,
            tracker_status=status,
            prefill_count=record.prefill_count + 1,
        )
        return {"fields": fields.model_dump(mode="json"), "resume_markdown": markdown}
