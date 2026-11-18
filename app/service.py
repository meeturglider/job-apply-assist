from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
import uuid
from datetime import UTC, datetime
from pathlib import Path

from app.config import Settings
from app.jd.analyze import analyze_job
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
    JobDescription,
    ValidationReport,
)
from app.profile.notes import apply_notes
from app.profile.service import ProfileService
from app.rendering.ats import check_ats, check_docx
from app.rendering.docx import render_docx
from app.rendering.html import resume_html
from app.rendering.markdown import resume_markdown
from app.rendering.pdf import RenderError, render_pdf
from app.store import ApplicationStore
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
    def __init__(self, settings: Settings) -> None:
        settings.ensure_dirs()
        self.settings = settings
        self.store = ApplicationStore(settings.database_path)
        self.profiles = ProfileService(settings)
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
            directory = self._directory(application_id)
            self._save_jd(directory, extraction)

            self._update(
                record,
                company=extraction.company or record.company,
                role=extraction.title or record.role,
                stage="matching",
                message="Loading your resume and portfolio...",
            )
            profile = await self.profiles.load(extra_urls=profile_urls)
            (directory / "profile.json").write_text(
                profile.model_dump_json(indent=2), encoding="utf-8"
            )

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

            (directory / "job.json").write_text(job.model_dump_json(indent=2), encoding="utf-8")
            (directory / "analysis.json").write_text(
                analysis.model_dump_json(indent=2), encoding="utf-8"
            )
            self._update(
                record,
                status="analyzed",
                stage="done",
                message=f"{analysis.score_label}: {analysis.score.total}% match.",
                score=analysis.score.total,
                company=job.company or record.company,
                role=job.title or record.role,
                profile_hash=ProfileService.hash(profile),
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

    def _save_jd(self, directory: Path, extraction: JobExtraction) -> None:
        (directory / "jd.md").write_text(extraction.text, encoding="utf-8")
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
        (directory / "jd.json").write_text(
            json.dumps(metadata, indent=2, ensure_ascii=False), encoding="utf-8"
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
        directory = self._directory(application_id)
        (directory / "notes.json").write_text(
            json.dumps([item.model_dump(mode="json") for item in notes], indent=2),
            encoding="utf-8",
        )
        (directory / "profile.noted.json").unlink(missing_ok=True)
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
        directory = self._directory(application_id)
        if not (directory / "job.json").exists() or not (directory / "profile.json").exists():
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
        directory = self._directory(application_id)
        try:
            job = JobDescription.model_validate_json(
                (directory / "job.json").read_text(encoding="utf-8")
            )
            profile = CandidateProfile.model_validate_json(
                (directory / "profile.json").read_text(encoding="utf-8")
            )
            analysis_path = directory / "analysis.json"
            baseline_path = directory / "analysis.baseline.json"
            if analysis_path.exists() and not baseline_path.exists():
                baseline_path.write_bytes(analysis_path.read_bytes())

            noted_profile, decisions = apply_notes(profile, record.notes)
            (directory / "notes.json").write_text(
                json.dumps([item.model_dump(mode="json") for item in decisions], indent=2),
                encoding="utf-8",
            )
            (directory / "profile.noted.json").write_text(
                noted_profile.model_dump_json(indent=2), encoding="utf-8"
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
            analysis_path.write_text(analysis.model_dump_json(indent=2), encoding="utf-8")

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
        directory = self._directory(application_id)
        logger.info("generation_rendering", extra={"application_id": application_id})
        try:
            job = JobDescription.model_validate_json(
                (directory / "job.json").read_text(encoding="utf-8")
            )
            analysis = AnalysisResult.model_validate_json(
                (directory / "analysis.json").read_text(encoding="utf-8")
            )
            noted_path = directory / "profile.noted.json"
            profile_path = noted_path if noted_path.exists() else directory / "profile.json"
            if profile_path.exists():
                profile = CandidateProfile.model_validate_json(
                    profile_path.read_text(encoding="utf-8")
                )
            else:
                profile = await self.profiles.load(extra_urls=record.profile_urls)
            resume, validation, method = await build_resume(profile, job, analysis, self.llm)
            self._update(
                record,
                stage="rendering",
                message="Rendering Markdown, HTML, DOCX, and PDF...",
            )
            markdown = resume_markdown(resume)
            html = resume_html(resume)
            (directory / "resume.json").write_text(
                resume.model_dump_json(indent=2), encoding="utf-8"
            )
            (directory / "resume.md").write_text(markdown, encoding="utf-8")
            (directory / "resume.html").write_text(html, encoding="utf-8")
            (directory / "validation.json").write_text(
                validation.model_dump_json(indent=2), encoding="utf-8"
            )

            artifacts: list[ArtifactInfo] = []
            artifacts.append(self._artifact(directory, "markdown", "resume.md"))
            artifacts.append(self._artifact(directory, "html", "resume.html"))
            render_docx(resume, directory / "resume.docx")
            artifacts.append(self._artifact(directory, "docx", "resume.docx"))

            pdf_error = ""
            ats_report: AtsReport
            try:
                await render_pdf(html, directory / "resume.pdf")
                artifacts.append(self._artifact(directory, "pdf", "resume.pdf"))
            except RenderError as exc:
                pdf_error = str(exc)
            if pdf_error:
                checks = [
                    AtsCheck(
                        name="PDF rendering",
                        passed=False,
                        detail=pdf_error,
                    )
                ]
                try:
                    checks.extend(check_docx(resume, directory / "resume.docx"))
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
                    ats_report = check_ats(
                        resume,
                        markdown,
                        directory / "resume.pdf",
                        job,
                        html,
                        directory / "resume.docx",
                    )
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
            (directory / "ats_report.json").write_text(
                ats_report.model_dump_json(indent=2), encoding="utf-8"
            )

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
            (directory / "metadata.json").write_text(
                json.dumps(metadata, indent=2, ensure_ascii=False), encoding="utf-8"
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

    def _artifact(self, directory: Path, kind: str, filename: str) -> ArtifactInfo:
        path = directory / filename
        return ArtifactInfo(
            kind=kind,
            filename=filename,
            size=path.stat().st_size if path.exists() else 0,
        )

    def get(self, application_id: str) -> ApplicationRecord | None:
        return self.store.get(application_id)

    def list(self, limit: int = 50) -> list[ApplicationRecord]:
        return self.store.list(limit)

    def artifact_path(self, application_id: str, filename: str) -> Path:
        record = self.store.get(application_id)
        if record is None:
            raise FileNotFoundError("Application not found.")
        allowed = {artifact.filename for artifact in record.artifacts}
        if filename not in allowed:
            raise FileNotFoundError("Artifact not found.")
        path = self._directory(application_id) / filename
        if not path.exists():
            raise FileNotFoundError("Artifact file is missing.")
        return path

    def analysis_payload(self, application_id: str) -> dict[str, object]:
        record = self.store.get(application_id)
        if record is None:
            raise FileNotFoundError("Application not found.")
        directory = self._directory(application_id)
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
            path = directory / f"{name}.json"
            if path.exists():
                payload[name] = json.loads(path.read_text(encoding="utf-8"))
        resume_md = directory / "resume.md"
        if resume_md.exists():
            payload["resume_markdown"] = resume_md.read_text(encoding="utf-8")
        validation = payload.get("validation")
        if isinstance(validation, dict):
            payload["validation"] = ValidationReport.model_validate(validation).model_dump(
                mode="json"
            )
        return payload
