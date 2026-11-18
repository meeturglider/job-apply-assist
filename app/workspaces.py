from __future__ import annotations

import re
from pathlib import Path

from app.artifacts import FileArtifactStore
from app.config import Settings
from app.profile.service import ProfileService
from app.service import ApplicationService
from app.store import ApplicationStore

_SAFE_ACCOUNT_ID = re.compile(r"[A-Za-z0-9_-]{1,128}")


def account_data_dir(data_dir: Path, account_id: str) -> Path:
    if not _SAFE_ACCOUNT_ID.fullmatch(account_id):
        raise ValueError("Invalid account identifier.")
    return data_dir / "users" / account_id


def build_account_workspace(settings: Settings, account_id: str) -> ApplicationService:
    data_dir = account_data_dir(settings.data_dir, account_id)
    workspace_settings = settings.model_copy(
        update={
            "data_dir": data_dir,
            "master_resume_path": data_dir / "profile" / "default_resume.md",
        }
    )
    workspace_settings.ensure_dirs()
    workspace = ApplicationService(
        workspace_settings,
        store=ApplicationStore(workspace_settings.database_path),
        profiles=ProfileService(workspace_settings),
        artifacts=FileArtifactStore(workspace_settings.applications_dir),
    )
    workspace.recover_interrupted()
    return workspace
