from __future__ import annotations

import asyncio
import base64
import binascii
import logging
import secrets
import time
import uuid
from collections import deque
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.config import Settings, settings
from app.jd.fetcher import FetchError, validate_url
from app.service import ApplicationService

WEB_DIR = Path(__file__).parent / "web"
STATIC_DIR = WEB_DIR / "static"
logger = logging.getLogger(__name__)


class AnalyzeRequest(BaseModel):
    url: str = Field(default="", max_length=2048)
    text: str = Field(default="", max_length=100_000)
    company: str = Field(default="", max_length=200)
    role: str = Field(default="", max_length=200)
    profile_urls: list[str] = Field(default_factory=list, max_length=5)


class ResumeUploadRequest(BaseModel):
    text: str = Field(min_length=1, max_length=1_000_000)
    filename: str = Field(default="uploaded_resume.md", max_length=200)


class NoteInput(BaseModel):
    target: str = Field(min_length=1, max_length=120)
    text: str = Field(min_length=10, max_length=2000)


class NotesRequest(BaseModel):
    notes: list[NoteInput] = Field(default_factory=list, max_length=25)


def _configure_logging(level: str) -> None:
    root = logging.getLogger()
    if not root.handlers:
        logging.basicConfig(
            level=level.upper(),
            format="%(asctime)s %(levelname)s %(name)s %(message)s",
        )
    else:
        root.setLevel(level.upper())


def _request_id(request: Request) -> str:
    candidate = request.headers.get("x-request-id", "").strip()
    if (
        candidate
        and len(candidate) <= 64
        and all(character.isalnum() or character in "-_." for character in candidate)
    ):
        return candidate
    return uuid.uuid4().hex[:12]


def _basic_auth_valid(request: Request, password: str) -> bool:
    header = request.headers.get("authorization", "").strip()
    scheme, _, token = header.partition(" ")
    if scheme.casefold() != "basic" or not token.strip():
        return False
    try:
        decoded = base64.b64decode(token.strip(), validate=True).decode("utf-8")
    except (binascii.Error, UnicodeDecodeError, ValueError):
        return False
    _, separator, supplied = decoded.partition(":")
    return bool(separator) and secrets.compare_digest(supplied, password)


def _client_key(request: Request, trust_proxy_headers: bool) -> str:
    if trust_proxy_headers:
        forwarded = request.headers.get("cf-connecting-ip", "").strip()
        if not forwarded:
            forwarded = request.headers.get("x-forwarded-for", "").split(",")[0].strip()
        if forwarded and len(forwarded) <= 64:
            return forwarded
    return request.client.host if request.client else "unknown"


def _error_code(status_code: int) -> str:
    return {
        401: "unauthorized",
        404: "not_found",
        409: "conflict",
        413: "payload_too_large",
        422: "validation_error",
        429: "rate_limited",
    }.get(status_code, "request_error")


def _clean_profile_urls(values: list[str]) -> list[str]:
    cleaned: list[str] = []
    for value in values:
        if not value.strip():
            continue
        try:
            url = validate_url(value)
        except FetchError as exc:
            raise HTTPException(status_code=422, detail=f"Invalid profile URL: {exc}") from exc
        if url not in cleaned:
            cleaned.append(url)
    return cleaned


def _clean_notes(values: list[NoteInput]) -> list[dict[str, str]]:
    cleaned: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for item in values:
        target = " ".join(item.target.split())[:120]
        text = " ".join(item.text.split())[:2000]
        if len(target) < 1 or len(text) < 10:
            raise HTTPException(
                status_code=422, detail="Each note needs a target and at least 10 characters."
            )
        marker = (target.casefold(), text.casefold())
        if marker in seen:
            continue
        seen.add(marker)
        cleaned.append({"target": target, "text": text})
    return cleaned


def _error_payload(request: Request, detail: Any, code: str) -> dict[str, Any]:
    return {
        "detail": detail,
        "code": code,
        "request_id": getattr(request.state, "request_id", ""),
    }


def _error_response(
    request: Request,
    status_code: int,
    detail: str,
    *,
    extra_headers: dict[str, str] | None = None,
) -> JSONResponse:
    response = JSONResponse(
        status_code=status_code,
        content=_error_payload(request, detail, _error_code(status_code)),
        headers=extra_headers,
    )
    response.headers["X-Request-ID"] = request.state.request_id
    if request.url.path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-store"
    return response


def create_app(custom_settings: Settings | None = None) -> FastAPI:
    active_settings = custom_settings or settings
    _configure_logging(active_settings.log_level)
    active_settings.ensure_dirs()
    service = ApplicationService(active_settings)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        logger.info(
            "service_started",
            extra={
                "llm_provider": active_settings.llm_provider,
                "llm_model": active_settings.ollama_model,
            },
        )
        try:
            yield
        finally:
            await service.shutdown()

    app = FastAPI(
        title="Job Apply Assist",
        description="Local, evidence-based ATS resume tailoring",
        version="0.1.0",
        lifespan=lifespan,
    )
    app.state.service = service
    app.state.settings = active_settings
    rate_windows: dict[str, deque[float]] = {}
    rate_lock = asyncio.Lock()

    async def _rate_limited(request: Request) -> bool:
        limit = active_settings.rate_limit_per_minute
        if limit <= 0 or request.method != "POST" or not request.url.path.startswith("/api/"):
            return False
        key = _client_key(request, active_settings.trust_proxy_headers)
        now = time.monotonic()
        async with rate_lock:
            window = rate_windows.setdefault(key, deque())
            while window and now - window[0] >= 60:
                window.popleft()
            if len(window) >= limit:
                return True
            window.append(now)
        return False

    @app.middleware("http")
    async def request_context(request: Request, call_next):
        request.state.request_id = _request_id(request)
        started = time.perf_counter()
        password = active_settings.shared_password.strip()
        if password and not _basic_auth_valid(request, password):
            logger.warning(
                "auth_rejected",
                extra={
                    "request_id": request.state.request_id,
                    "path": request.url.path,
                },
            )
            response = _error_response(
                request,
                401,
                "A valid username and shared password are required.",
                extra_headers={
                    "WWW-Authenticate": 'Basic realm="Job Apply Assist", charset="UTF-8"'
                },
            )
        elif await _rate_limited(request):
            logger.warning(
                "rate_limit_rejected",
                extra={
                    "request_id": request.state.request_id,
                    "method": request.method,
                    "path": request.url.path,
                },
            )
            response = _error_response(
                request,
                429,
                "Too many requests. Try again shortly.",
                extra_headers={"Retry-After": "60"},
            )
        else:
            try:
                response = await call_next(request)
            except Exception:
                logger.exception(
                    "http_request_unhandled",
                    extra={
                        "request_id": request.state.request_id,
                        "method": request.method,
                        "path": request.url.path,
                    },
                )
                raise
        response.headers["X-Request-ID"] = request.state.request_id
        if request.url.path.startswith("/api/"):
            response.headers.setdefault("Cache-Control", "no-store")
            logger.info(
                "http_request",
                extra={
                    "request_id": request.state.request_id,
                    "method": request.method,
                    "path": request.url.path,
                    "status": response.status_code,
                    "duration_ms": round((time.perf_counter() - started) * 1000, 1),
                },
            )
        return response

    @app.exception_handler(StarletteHTTPException)
    async def http_exception_handler(request: Request, exc: StarletteHTTPException):
        detail = exc.detail if isinstance(exc.detail, str) else "Request failed."
        return JSONResponse(
            status_code=exc.status_code,
            content=_error_payload(request, detail, _error_code(exc.status_code)),
            headers=exc.headers,
        )

    @app.exception_handler(RequestValidationError)
    async def validation_exception_handler(request: Request, exc: RequestValidationError):
        errors = [
            {
                "loc": [str(part) for part in error.get("loc", [])],
                "msg": str(error.get("msg", "Invalid value.")),
                "type": str(error.get("type", "invalid")),
            }
            for error in exc.errors()
        ]
        return JSONResponse(
            status_code=422,
            content=_error_payload(
                request,
                {"message": "Request validation failed.", "errors": errors},
                "validation_error",
            ),
        )

    @app.exception_handler(Exception)
    async def unhandled_exception_handler(request: Request, exc: Exception):
        logger.exception(
            "http_request_failed",
            extra={
                "request_id": getattr(request.state, "request_id", ""),
                "method": request.method,
                "path": request.url.path,
            },
        )
        return JSONResponse(
            status_code=500,
            content=_error_payload(
                request,
                "Internal server error. Check the app logs for details.",
                "internal_error",
            ),
            headers={"X-Request-ID": getattr(request.state, "request_id", "")},
        )

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
        summary = service.profiles.summary(profile)
        return {**summary, "profile": summary}

    @app.post("/api/profile/resume")
    async def resume_upload(request: ResumeUploadRequest) -> dict[str, object]:
        try:
            profile = service.profiles.upload_resume(request.text, request.filename)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        summary = service.profiles.summary(profile)
        return {**summary, "profile": summary, "status": service.profiles.status()}

    @app.post("/api/profile/resume/reset")
    async def resume_reset() -> dict[str, object]:
        service.profiles.reset_resume()
        profile = await service.profiles.load()
        summary = service.profiles.summary(profile)
        return {**summary, "profile": summary, "status": service.profiles.status()}

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
            profile_urls=_clean_profile_urls(request.profile_urls),
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
        return {
            "application": {
                **record.model_dump(mode="json"),
                "progress_pct": record.progress_pct,
            }
        }

    @app.get("/api/applications/{application_id}")
    async def application_detail(application_id: str) -> dict[str, object]:
        try:
            return service.analysis_payload(application_id)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.put("/api/applications/{application_id}/notes")
    async def save_notes(application_id: str, request: NotesRequest) -> dict[str, object]:
        try:
            record = service.save_notes(application_id, _clean_notes(request.notes))
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return {"application": record.model_dump(mode="json")}

    @app.post("/api/applications/{application_id}/revalidate")
    async def revalidate(application_id: str) -> dict[str, object]:
        try:
            record = service.start_revalidation(application_id)
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return {
            "application": {
                **record.model_dump(mode="json"),
                "progress_pct": record.progress_pct,
            }
        }

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
