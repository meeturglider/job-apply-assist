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
from typing import Any, Literal

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.auth import (
    CSRF_HEADER,
    Account,
    AccountStore,
    SessionManager,
    clear_session_cookies,
    set_session_cookies,
)
from app.config import Settings, settings
from app.jd.fetcher import FetchError, validate_url
from app.llm import OllamaClient
from app.models import TailoredResume
from app.service import ApplicationService
from app.workspaces import build_account_workspace

WEB_DIR = Path(__file__).parent / "web"
STATIC_DIR = WEB_DIR / "static"
logger = logging.getLogger(__name__)


class AnalyzeRequest(BaseModel):
    url: str = Field(default="", max_length=2048)
    text: str = Field(default="", max_length=100_000)
    company: str = Field(default="", max_length=200)
    role: str = Field(default="", max_length=200)
    profile_urls: list[str] = Field(default_factory=list, max_length=5)
    application_id: str = Field(default="", max_length=120)


class ApplicationDetailsRequest(BaseModel):
    company: str = Field(default="", max_length=200)
    role: str = Field(default="", max_length=200)
    source_url: str = Field(default="", max_length=2048)


class ResumeUploadRequest(BaseModel):
    text: str = Field(min_length=1, max_length=1_000_000)
    filename: str = Field(default="uploaded_resume.md", max_length=200)


class NoteInput(BaseModel):
    target: str = Field(min_length=1, max_length=1000)
    text: str = Field(min_length=10, max_length=2000)


class NotesRequest(BaseModel):
    notes: list[NoteInput] = Field(default_factory=list, max_length=25)


def _validated_email(value: str) -> str:
    email = value.strip()
    if (
        len(email) < 3
        or len(email) > 320
        or "@" not in email
        or email.startswith("@")
        or email.endswith("@")
        or any(character.isspace() for character in email)
    ):
        raise ValueError("Enter a valid email address.")
    return email


class AccountRequest(BaseModel):
    email: str
    password: str

    @field_validator("email")
    @classmethod
    def validate_email(cls, value: str) -> str:
        return _validated_email(value)

    @field_validator("password")
    @classmethod
    def validate_password(cls, value: str) -> str:
        if len(value) < 8 or len(value) > 200:
            raise ValueError("Use a password of at least 8 characters.")
        return value


class RecoveryResetRequest(BaseModel):
    email: str
    recovery_code: str = Field(min_length=12, max_length=40)
    new_password: str = Field(min_length=8, max_length=200)

    @field_validator("email")
    @classmethod
    def validate_email(cls, value: str) -> str:
        return _validated_email(value)


class ContactRequest(BaseModel):
    email: str = Field(min_length=3, max_length=320)
    name: str = Field(default="", max_length=120)


class OutreachRequest(BaseModel):
    contact_email: str = Field(default="", max_length=320)
    user_note: str = Field(default="", max_length=1000)


class TrackerRequest(BaseModel):
    status: Literal["draft", "analyzed", "email_drafted", "prefilled", "submitted"]


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
        403: "forbidden",
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
    portal_mode = active_settings.portal_mode
    if portal_mode:
        active_settings.data_dir.mkdir(parents=True, exist_ok=True)
        accounts = AccountStore(active_settings.accounts_path)
        sessions = SessionManager(active_settings.session_ttl_minutes)
    else:
        active_settings.ensure_dirs()
        accounts = None
        sessions = None

    service = None if portal_mode else ApplicationService(active_settings)
    workspaces: dict[str, ApplicationService] = {}

    def _account_workspace(account: Account) -> ApplicationService:
        workspace = workspaces.get(account.id)
        if workspace is None:
            workspace = build_account_workspace(active_settings, account.id)
            workspaces[account.id] = workspace
        return workspace

    def _workspace(request: Request) -> ApplicationService:
        if service is not None:
            return service
        session = getattr(request.state, "session", None)
        if session is None or session.workspace is None:
            raise HTTPException(status_code=401, detail="Sign in to continue.")
        return session.workspace

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        logger.info(
            "service_started",
            extra={
                "llm_provider": active_settings.llm_provider,
                "llm_model": active_settings.ollama_model,
                "portal_mode": portal_mode,
            },
        )
        try:
            yield
        finally:
            if service is not None:
                await service.shutdown()
            for workspace in workspaces.values():
                await workspace.shutdown()

    app = FastAPI(
        title="Job Apply Assist",
        description="Local, evidence-based ATS resume tailoring",
        version="0.1.0",
        lifespan=lifespan,
    )
    app.state.service = service
    app.state.settings = active_settings
    app.state.accounts = accounts
    app.state.sessions = sessions
    rate_windows: dict[str, deque[float]] = {}
    rate_lock = asyncio.Lock()

    async def _rate_limited(request: Request) -> bool:
        limit = active_settings.rate_limit_per_minute
        unsafe = request.method in {"POST", "PUT", "PATCH", "DELETE"}
        if limit <= 0 or not unsafe or not request.url.path.startswith("/api/"):
            return False
        session = getattr(request.state, "session", None)
        if portal_mode and session is not None:
            key = f"user:{session.user_id}"
        elif portal_mode:
            key = f"ip:{_client_key(request, active_settings.trust_proxy_headers)}"
        else:
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
        request.state.session = None
        request.state.principal = None
        started = time.perf_counter()
        path = request.url.path
        auth_free = (
            path.startswith("/api/auth/")
            or path.startswith("/static/")
            or path in {"/", "/api/health", "/favicon.ico"}
        )
        if portal_mode and sessions is not None:
            sessions.prune()
            session = SessionManager.from_request(request, sessions)
            request.state.session = session
            if session is not None:
                request.state.principal = session.user_id
        password = active_settings.shared_password.strip()
        if portal_mode and sessions is not None and not auth_free and request.state.session is None:
            response = _error_response(request, 401, "Sign in to continue.")
        elif (
            portal_mode
            and request.state.session is not None
            and not auth_free
            and request.method in {"POST", "PUT", "PATCH", "DELETE"}
            and not secrets.compare_digest(
                request.headers.get(CSRF_HEADER, ""), request.state.session.csrf_token
            )
        ):
            response = _error_response(request, 403, "Invalid CSRF token.")
        elif not portal_mode and password and not _basic_auth_valid(request, password):
            logger.warning(
                "auth_rejected",
                extra={
                    "request_id": request.state.request_id,
                    "path": path,
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
                    "path": path,
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
                        "path": path,
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
        errors = []
        for error in exc.errors():
            message = str(error.get("msg", "Invalid value."))
            if message.startswith("Value error, "):
                message = message.removeprefix("Value error, ")
            errors.append(
                {
                    "loc": [str(part) for part in error.get("loc", [])],
                    "msg": message,
                    "type": str(error.get("type", "invalid")),
                }
            )
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

    def _require_portal() -> None:
        if not portal_mode or accounts is None or sessions is None:
            raise HTTPException(status_code=404, detail="Portal accounts are disabled.")

    @app.get("/api/auth/config")
    async def auth_config() -> dict[str, object]:
        return {
            "portal_mode": portal_mode,
            "allow_registration": active_settings.allow_registration,
        }

    @app.post("/api/auth/register")
    async def register(payload: AccountRequest, response: Response) -> dict[str, object]:
        _require_portal()
        assert accounts is not None and sessions is not None
        if not active_settings.allow_registration:
            raise HTTPException(status_code=403, detail="Registration is disabled.")
        account, recovery_code = accounts.create_with_recovery(payload.email, payload.password)
        if account is None:
            raise HTTPException(
                status_code=409,
                detail="Use a valid email and a password of at least 8 characters.",
            )
        session = sessions.create(account, _account_workspace(account))
        set_session_cookies(
            response,
            session,
            secure=active_settings.session_cookie_secure,
            max_age=active_settings.session_ttl_minutes * 60,
        )
        return {
            "account": {"email": account.email},
            "csrf_token": session.csrf_token,
            "recovery_code": recovery_code,
            "needs_recovery_code": False,
            "session_only": False,
        }

    @app.post("/api/auth/login")
    async def login(payload: AccountRequest, response: Response) -> dict[str, object]:
        _require_portal()
        assert accounts is not None and sessions is not None
        account = accounts.authenticate(payload.email, payload.password)
        if account is None:
            raise HTTPException(status_code=401, detail="Invalid email or password.")
        session = sessions.create(account, _account_workspace(account))
        set_session_cookies(
            response,
            session,
            secure=active_settings.session_cookie_secure,
            max_age=active_settings.session_ttl_minutes * 60,
        )
        return {
            "account": {"email": account.email},
            "csrf_token": session.csrf_token,
            "needs_recovery_code": not accounts.needs_recovery_code(account.id),
            "session_only": False,
        }

    @app.post("/api/auth/reset")
    async def reset_password(payload: RecoveryResetRequest) -> dict[str, object]:
        _require_portal()
        assert accounts is not None and sessions is not None
        account, recovery_code = accounts.reset_password(
            payload.email, payload.recovery_code, payload.new_password
        )
        if account is None:
            raise HTTPException(
                status_code=400,
                detail="Invalid email, recovery code, or password. Check the code and try again.",
            )
        sessions.delete_for_user(account.id)
        return {
            "account": {"email": account.email},
            "recovery_code": recovery_code,
            "needs_recovery_code": False,
            "session_only": False,
        }

    @app.post("/api/account/recovery-code")
    async def create_recovery_code(request: Request) -> dict[str, object]:
        _require_portal()
        assert accounts is not None
        session = request.state.session
        if session is None:
            raise HTTPException(status_code=401, detail="Sign in to continue.")
        recovery_code = accounts.set_recovery_code(session.user_id)
        if not recovery_code:
            raise HTTPException(status_code=404, detail="Account not found.")
        return {"recovery_code": recovery_code}

    @app.post("/api/auth/logout")
    async def logout(request: Request, response: Response) -> dict[str, object]:
        _require_portal()
        assert sessions is not None
        session = request.state.session
        if session is not None:
            sessions.delete(session.id)
        clear_session_cookies(response, secure=active_settings.session_cookie_secure)
        return {"ok": True}

    @app.get("/api/auth/me")
    async def me(request: Request) -> dict[str, object]:
        _require_portal()
        assert accounts is not None
        session = request.state.session
        if session is None:
            raise HTTPException(status_code=401, detail="Sign in to continue.")
        return {
            "account": {"email": session.email},
            "session_only": False,
            "csrf_token": session.csrf_token,
            "needs_recovery_code": not accounts.needs_recovery_code(session.user_id),
        }

    @app.get("/api/health")
    async def health() -> dict[str, object]:
        llm = service.llm if service is not None else OllamaClient(active_settings)
        return {
            "ok": True,
            "llm_provider": active_settings.llm_provider,
            "llm_model": active_settings.ollama_model,
            "llm_available": await llm.available(),
            "profile": service.profiles.status() if service is not None else {},
        }

    @app.get("/api/profile/status")
    async def profile_status(request: Request) -> dict[str, object]:
        return _workspace(request).profiles.status()

    @app.post("/api/profile/refresh")
    async def profile_refresh(request: Request) -> dict[str, object]:
        workspace = _workspace(request)
        profile = await workspace.profiles.load(refresh=True)
        summary = workspace.profiles.summary(profile)
        return {**summary, "profile": summary}

    @app.post("/api/profile/resume")
    async def resume_upload(request: Request, payload: ResumeUploadRequest) -> dict[str, object]:
        workspace = _workspace(request)
        try:
            profile = workspace.profiles.upload_resume(payload.text, payload.filename)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        summary = workspace.profiles.summary(profile)
        return {**summary, "profile": summary, "status": workspace.profiles.status()}

    @app.post("/api/profile/resume/reset")
    async def resume_reset(request: Request) -> dict[str, object]:
        workspace = _workspace(request)
        workspace.profiles.reset_resume()
        if portal_mode:
            return {
                "identity": {},
                "experience_count": 0,
                "project_count": 0,
                "skill_count": 0,
                "certification_count": 0,
                "award_count": 0,
                "warnings": [],
                "sources": {},
                "provenance": {},
                "status": workspace.profiles.status(),
            }
        try:
            profile = await workspace.profiles.load()
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        summary = workspace.profiles.summary(profile)
        return {**summary, "profile": summary, "status": workspace.profiles.status()}

    @app.post("/api/analyze")
    async def analyze(request: Request, payload: AnalyzeRequest) -> dict[str, object]:
        if not payload.url.strip() and not payload.text.strip():
            raise HTTPException(
                status_code=422, detail="Provide a job URL or paste the job description."
            )
        profile_status = _workspace(request).profiles.status()
        if not profile_status.get("uploaded_resume") and not profile_status.get(
            "master_resume_exists"
        ):
            raise HTTPException(status_code=422, detail="Upload a resume before analyzing a job.")
        try:
            record = _workspace(request).start_analysis(
                url=payload.url.strip(),
                text=payload.text.strip(),
                company=payload.company.strip(),
                role=payload.role.strip(),
                profile_urls=_clean_profile_urls(payload.profile_urls),
                application_id=payload.application_id.strip(),
            )
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return {"application": record.model_dump(mode="json")}

    @app.get("/api/applications")
    async def applications(request: Request) -> dict[str, object]:
        records = _workspace(request).list()
        return {"applications": [record.model_dump(mode="json") for record in records]}

    @app.post("/api/applications/manual")
    async def create_manual_session(
        request: Request, payload: ApplicationDetailsRequest
    ) -> dict[str, object]:
        record = _workspace(request).create_manual_draft(
            company=payload.company,
            role=payload.role,
            source_url=payload.source_url,
        )
        return {"application": record.model_dump(mode="json")}

    @app.patch("/api/applications/{application_id}/details")
    async def update_application_details(
        request: Request, application_id: str, payload: ApplicationDetailsRequest
    ) -> dict[str, object]:
        try:
            record = _workspace(request).update_details(
                application_id,
                company=payload.company,
                role=payload.role,
                source_url=payload.source_url,
            )
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return {"application": record.model_dump(mode="json")}

    @app.get("/api/applications/{application_id}/status")
    async def application_status(request: Request, application_id: str) -> dict[str, object]:
        record = _workspace(request).get(application_id)
        if record is None:
            raise HTTPException(status_code=404, detail="Application not found.")
        return {
            "application": {
                **record.model_dump(mode="json"),
                "progress_pct": record.progress_pct,
            }
        }

    @app.get("/api/applications/{application_id}")
    async def application_detail(request: Request, application_id: str) -> dict[str, object]:
        try:
            return _workspace(request).analysis_payload(application_id)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.put("/api/applications/{application_id}/notes")
    async def save_notes(
        request: Request, application_id: str, payload: NotesRequest
    ) -> dict[str, object]:
        try:
            record = _workspace(request).save_notes(application_id, _clean_notes(payload.notes))
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return {"application": record.model_dump(mode="json")}

    @app.post("/api/applications/{application_id}/revalidate")
    async def revalidate(request: Request, application_id: str) -> dict[str, object]:
        try:
            record = _workspace(request).start_revalidation(application_id)
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return {
            "application": {
                **record.model_dump(mode="json"),
                "progress_pct": record.progress_pct,
            }
        }

    @app.post("/api/applications/{application_id}/generate")
    async def generate(request: Request, application_id: str) -> dict[str, object]:
        try:
            record = _workspace(request).start_generation(application_id)
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return {"application": record.model_dump(mode="json")}

    @app.put("/api/applications/{application_id}/resume")
    async def update_resume(
        request: Request, application_id: str, payload: TailoredResume
    ) -> dict[str, object]:
        try:
            return await _workspace(request).update_resume(application_id, payload)
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.post("/api/applications/{application_id}/contacts")
    async def add_contact(
        request: Request, application_id: str, payload: ContactRequest
    ) -> dict[str, object]:
        try:
            record = _workspace(request).add_contact(application_id, payload.email, payload.name)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return {"application": record.model_dump(mode="json")}

    @app.post("/api/applications/{application_id}/outreach")
    async def outreach(
        request: Request, application_id: str, payload: OutreachRequest
    ) -> dict[str, object]:
        try:
            return _workspace(request).draft_outreach(
                application_id,
                contact_email=payload.contact_email.strip(),
                user_note=payload.user_note,
            )
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.patch("/api/applications/{application_id}/tracker")
    async def tracker(
        request: Request, application_id: str, payload: TrackerRequest
    ) -> dict[str, object]:
        try:
            record = _workspace(request).update_tracker(application_id, payload.status)
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return {"application": record.model_dump(mode="json")}

    @app.post("/api/applications/{application_id}/prefill")
    async def prefill(request: Request, application_id: str) -> dict[str, object]:
        try:
            return _workspace(request).prefill_payload(application_id)
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.get("/api/applications/{application_id}/artifacts/{filename}")
    async def artifact(
        request: Request,
        application_id: str,
        filename: str,
        download: bool = False,
    ) -> Response:
        try:
            return _workspace(request).artifact_response(
                application_id, filename, download=download
            )
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    return app


app = create_app()
