from __future__ import annotations

import contextlib
import json
import sqlite3
from pathlib import Path

from app.models import ApplicationRecord


class ApplicationStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._init()
        self._secure_database()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        return connection

    def _secure_database(self) -> None:
        with contextlib.suppress(OSError):
            self.path.chmod(0o600)

    def _init(self) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS applications (
                    id TEXT PRIMARY KEY,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    payload TEXT NOT NULL
                )
                """
            )

    def save(self, record: ApplicationRecord) -> None:
        payload = record.model_dump_json()
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO applications (id, created_at, updated_at, payload)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    updated_at = excluded.updated_at,
                    payload = excluded.payload
                """,
                (
                    record.id,
                    record.created_at.isoformat(),
                    record.updated_at.isoformat(),
                    payload,
                ),
            )
        self._secure_database()

    def get(self, application_id: str) -> ApplicationRecord | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT payload FROM applications WHERE id = ?", (application_id,)
            ).fetchone()
        if row is None:
            return None
        try:
            return ApplicationRecord.model_validate_json(row["payload"])
        except (json.JSONDecodeError, ValueError):
            return None

    def list(self, limit: int = 50) -> list[ApplicationRecord]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT payload FROM applications ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        records: list[ApplicationRecord] = []
        for row in rows:
            try:
                records.append(ApplicationRecord.model_validate_json(row["payload"]))
            except (json.JSONDecodeError, ValueError):
                continue
        return records
