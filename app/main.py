from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from app.config import Settings, settings
from app.service import ApplicationService

WEB_DIR = Path(__file__).parent / "web"
STATIC_DIR = WEB_DIR / "static"


class AnalyzeRequest(BaseModel):
    url: str = ""
    text: str = ""
    company: str = ""
    role: str = ""


def create_app(custom_settings: Settings | None = None) -> FastAPI:
    active_settings = custom_settings or settings
    active_settings.ensure_dirs()
    app = FastAPI(
        title="Job Apply Assist",
        description="Local, evidence-based ATS resume tailoring",
        version="0.1.0",
    )
    service = ApplicationService(active_settings)
    app.state.service = service
    app.state.settings = active_settings

    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    @app.get("/", include_in_schema=False)
    async def index() -> FileResponse:
        return FileResponse(WEB_DIR / "index.html")

    @app.get("/api/health")
    async def health() -> dict[str, object]:
        llm_available = await service.llm.available()
        return {
            "ok": True,
            "llm_provider": active_settings.llm_provider,
            "llm_model": active_settings.ollama_model,
            "llm_available": llm_available,
            "profile": service.profiles.status(),
        }

    @app.get("/api/profile/status")
    async def profile_status() -> dict[str, object]:
        return service.profiles.status()

    @app.post("/api/profile/refresh")
    async def profile_refresh() -> dict[str, object]:
        profile = await service.profiles.load(refresh=True)
        return {
            "identity": profile.identity.model_dump(),
            "experience_years": profile.experience_years,
            "experience_count": len(profile.experience),
            "project_count": len(profile.projects),
            "skill_count": len(profile.skills),
            "certification_count": len(profile.certifications),
            "warnings": profile.warnings,
            "sources": profile.sources,
        }

    @app.post("/api/analyze")
    async def analyze(request: AnalyzeRequest) -> dict[str, object]:
        if not request.url.strip() and not request.text.strip():
            raise HTTPException(
                status_code=422, detail="Provide a job URL or paste the job description."
            )
        record = service.start_analysis(
            url=request.url.strip(),
            text=request.text.strip(),
            company=request.company.strip(),
            role=request.role.strip(),
        )
        return {"application": record.model_dump(mode="json")}

    @app.get("/api/applications")
    async def applications() -> dict[str, object]:
        return {"applications": [record.model_dump(mode="json") for record in service.list()]}

    @app.get("/api/applications/{application_id}/status")
    async def application_status(application_id: str) -> dict[str, object]:
        record = service.get(application_id)
        if record is None:
            raise HTTPException(status_code=404, detail="Application not found.")
        return {"application": record.model_dump(mode="json")}

    @app.get("/api/applications/{application_id}")
    async def application_detail(application_id: str) -> dict[str, object]:
        try:
            return service.analysis_payload(application_id)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.post("/api/applications/{application_id}/generate")
    async def generate(application_id: str) -> dict[str, object]:
        try:
            record = service.start_generation(application_id)
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return {"application": record.model_dump(mode="json")}

    @app.get("/api/applications/{application_id}/artifacts/{filename}")
    async def artifact(
        application_id: str,
        filename: str,
        download: bool = False,
    ) -> FileResponse:
        try:
            path = service.artifact_path(application_id, filename)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        media_types = {
            ".pdf": "application/pdf",
            ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            ".md": "text/markdown",
            ".html": "text/html",
        }
        previewable = {".html", ".md"}
        disposition = "inline" if path.suffix in previewable and not download else "attachment"
        return FileResponse(
            path,
            media_type=media_types.get(path.suffix, "application/octet-stream"),
            filename=path.name,
            content_disposition_type=disposition,
            headers={
                "X-Content-Type-Options": "nosniff",
                "Cache-Control": "no-store",
            },
        )

    return app


app = create_app()
