from __future__ import annotations

import asyncio
from pathlib import Path

from app.config import Settings
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
