from __future__ import annotations

import asyncio
from pathlib import Path

from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.service import ApplicationService
from app.store import ApplicationStore


def test_data_directory_and_database_are_private(tmp_path: Path) -> None:
    settings = Settings(
        llm_provider="none",
        master_resume_path=tmp_path / "MASTER_RESUME.md",
        data_dir=tmp_path / "data",
    )
    settings.ensure_dirs()
    assert settings.data_dir.stat().st_mode & 0o777 == 0o700
    assert settings.applications_dir.stat().st_mode & 0o777 == 0o700
    assert settings.profile_dir.stat().st_mode & 0o777 == 0o700

    store = ApplicationStore(settings.database_path)
    assert store.path.stat().st_mode & 0o777 == 0o600


async def test_service_shutdown_cancels_background_tasks(tmp_path: Path) -> None:
    settings = Settings(
        llm_provider="none",
        master_resume_path=tmp_path / "MASTER_RESUME.md",
        data_dir=tmp_path / "data",
    )
    service = ApplicationService(settings)
    started = asyncio.Event()

    async def slow() -> None:
        started.set()
        await asyncio.sleep(60)

    task = asyncio.create_task(slow())
    service._track(task)
    await started.wait()
    await service.shutdown()
    assert task.cancelled()


def _shared_settings(tmp_path: Path, master_text: str, **overrides: object) -> Settings:
    master = tmp_path / "MASTER_RESUME.md"
    master.write_text(master_text, encoding="utf-8")
    defaults: dict[str, object] = {
        "llm_provider": "none",
        "master_resume_path": master,
        "portfolio_base_url": "http://127.0.0.1:9",
        "data_dir": tmp_path / "data",
        "shared_password": "",
    }
    defaults.update(overrides)
    return Settings(**defaults)


def test_shared_password_gates_all_routes(tmp_path: Path, master_text: str) -> None:
    app = create_app(_shared_settings(tmp_path, master_text, shared_password="secret"))
    with TestClient(app) as client:
        assert client.get("/").status_code == 401
        unauthorized = client.get("/api/health")
        assert unauthorized.status_code == 401
        assert unauthorized.headers["www-authenticate"].startswith("Basic")
        assert unauthorized.json()["code"] == "unauthorized"

        assert client.get("/api/health", auth=("anyone", "wrong")).status_code == 401
        assert client.get("/api/health", auth=("friend", "secret")).status_code == 200


def test_post_api_rate_limit_returns_error_envelope(tmp_path: Path, master_text: str) -> None:
    app = create_app(_shared_settings(tmp_path, master_text, rate_limit_per_minute=2))
    with TestClient(app) as client:
        assert client.post("/api/profile/refresh", json={}).status_code == 200
        assert client.post("/api/profile/refresh", json={}).status_code == 200

        limited = client.post("/api/profile/refresh", json={})
        assert limited.status_code == 429
        assert limited.json()["code"] == "rate_limited"
        assert limited.headers["retry-after"] == "60"
        assert client.get("/api/health").status_code == 200


def test_trusted_proxy_headers_separate_rate_limit_buckets(
    tmp_path: Path, master_text: str
) -> None:
    app = create_app(
        _shared_settings(
            tmp_path,
            master_text,
            rate_limit_per_minute=1,
            trust_proxy_headers=True,
        )
    )
    with TestClient(app) as client:
        first = {"CF-Connecting-IP": "203.0.113.10"}
        second = {"CF-Connecting-IP": "203.0.113.11"}
        assert client.post("/api/profile/refresh", json={}, headers=first).status_code == 200
        assert client.post("/api/profile/refresh", json={}, headers=second).status_code == 200
        assert client.post("/api/profile/refresh", json={}, headers=first).status_code == 429
