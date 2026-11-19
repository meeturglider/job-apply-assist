from __future__ import annotations

import contextlib
import hashlib
import hmac
import re
import secrets
import sqlite3
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from fastapi import Request, Response

SESSION_COOKIE = "jobassist_session"
CSRF_COOKIE = "jobassist_csrf"
CSRF_HEADER = "x-csrf-token"
_PBKDF2_ITERATIONS = 200_000
_RECOVERY_MAX_ATTEMPTS = 5
_RECOVERY_LOCK_SECONDS = 900


@dataclass(frozen=True)
class Account:
    id: str
    email: str
    created_at: str
    has_recovery_code: bool = False


@dataclass
class Session:
    id: str
    user_id: str
    email: str
    csrf_token: str
    expires_at: float
    workspace: Any = None

    @property
    def expired(self) -> bool:
        return time.time() >= self.expires_at


class AccountStore:
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
                CREATE TABLE IF NOT EXISTS accounts (
                    id TEXT PRIMARY KEY,
                    email TEXT NOT NULL UNIQUE,
                    password_hash TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    recovery_hash TEXT,
                    recovery_created_at TEXT,
                    recovery_attempts INTEGER NOT NULL DEFAULT 0,
                    recovery_locked_until REAL
                )
                """
            )
            columns = {row["name"] for row in connection.execute("PRAGMA table_info(accounts)")}
            if "recovery_hash" not in columns:
                connection.execute("ALTER TABLE accounts ADD COLUMN recovery_hash TEXT")
            if "recovery_created_at" not in columns:
                connection.execute("ALTER TABLE accounts ADD COLUMN recovery_created_at TEXT")
            if "recovery_attempts" not in columns:
                connection.execute(
                    "ALTER TABLE accounts ADD COLUMN recovery_attempts INTEGER NOT NULL DEFAULT 0"
                )
            if "recovery_locked_until" not in columns:
                connection.execute("ALTER TABLE accounts ADD COLUMN recovery_locked_until REAL")

    @staticmethod
    def normalize_email(value: str) -> str:
        return value.strip().casefold()

    @staticmethod
    def hash_password(password: str) -> str:
        salt = secrets.token_bytes(16)
        digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, _PBKDF2_ITERATIONS)
        return f"pbkdf2_sha256${_PBKDF2_ITERATIONS}${salt.hex()}${digest.hex()}"

    @staticmethod
    def verify_password(password: str, encoded: str) -> bool:
        parts = encoded.split("$")
        if len(parts) != 4 or parts[0] != "pbkdf2_sha256":
            return False
        try:
            iterations = int(parts[1])
            salt = bytes.fromhex(parts[2])
            expected = bytes.fromhex(parts[3])
        except (ValueError, TypeError):
            return False
        supplied = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
        return hmac.compare_digest(supplied, expected)

    @staticmethod
    def normalize_recovery_code(value: str) -> str:
        return re.sub(r"[^A-Z0-9]", "", value.upper())

    @staticmethod
    def generate_recovery_code() -> str:
        alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
        groups = ["".join(secrets.choice(alphabet) for _ in range(5)) for _ in range(4)]
        return "-".join(groups)

    @staticmethod
    def _account_from_row(row: sqlite3.Row) -> Account:
        return Account(
            id=row["id"],
            email=row["email"],
            created_at=row["created_at"],
            has_recovery_code=bool(row["recovery_hash"]),
        )

    def create_with_recovery(self, email: str, password: str) -> tuple[Account | None, str]:
        normalized = self.normalize_email(email)
        if not normalized or "@" not in normalized or len(password) < 8:
            return None, ""
        account_id = secrets.token_urlsafe(18)
        created_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        recovery_code = self.generate_recovery_code()
        try:
            with self._connect() as connection:
                connection.execute(
                    """
                    INSERT INTO accounts (
                        id, email, password_hash, created_at, recovery_hash, recovery_created_at
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        account_id,
                        normalized,
                        self.hash_password(password),
                        created_at,
                        self.hash_password(self.normalize_recovery_code(recovery_code)),
                        created_at,
                    ),
                )
        except sqlite3.IntegrityError:
            return None, ""
        self._secure_database()
        return (
            Account(
                id=account_id,
                email=normalized,
                created_at=created_at,
                has_recovery_code=True,
            ),
            recovery_code,
        )

    def create(self, email: str, password: str) -> Account | None:
        account, _ = self.create_with_recovery(email, password)
        return account

    def set_recovery_code(self, account_id: str) -> str:
        recovery_code = self.generate_recovery_code()
        created_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        with self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE accounts
                SET recovery_hash = ?, recovery_created_at = ?,
                    recovery_attempts = 0, recovery_locked_until = NULL
                WHERE id = ?
                """,
                (
                    self.hash_password(self.normalize_recovery_code(recovery_code)),
                    created_at,
                    account_id,
                ),
            )
        return recovery_code if cursor.rowcount else ""

    def needs_recovery_code(self, account_id: str) -> bool:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT recovery_hash FROM accounts WHERE id = ?", (account_id,)
            ).fetchone()
        return bool(row and row["recovery_hash"])

    def authenticate(self, email: str, password: str) -> Account | None:
        normalized = self.normalize_email(email)
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT id, email, password_hash, created_at, recovery_hash
                FROM accounts WHERE email = ?
                """,
                (normalized,),
            ).fetchone()
        if row is None or not self.verify_password(password, row["password_hash"]):
            return None
        return self._account_from_row(row)

    def reset_password(
        self, email: str, recovery_code: str, new_password: str
    ) -> tuple[Account | None, str]:
        normalized = self.normalize_email(email)
        normalized_code = self.normalize_recovery_code(recovery_code)
        if (
            not normalized
            or "@" not in normalized
            or len(normalized_code) < 16
            or len(new_password) < 8
        ):
            return None, ""
        now = time.time()
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT id, email, created_at, recovery_hash, recovery_attempts,
                       recovery_locked_until
                FROM accounts WHERE email = ?
                """,
                (normalized,),
            ).fetchone()
            if row is None or not row["recovery_hash"]:
                return None, ""
            locked_until = row["recovery_locked_until"] or 0
            if locked_until and locked_until > now:
                return None, ""
            if not self.verify_password(normalized_code, row["recovery_hash"]):
                attempts = int(row["recovery_attempts"] or 0) + 1
                new_lock = now + _RECOVERY_LOCK_SECONDS if attempts >= _RECOVERY_MAX_ATTEMPTS else 0
                connection.execute(
                    "UPDATE accounts SET recovery_attempts = ?, recovery_locked_until = ? WHERE id = ?",
                    (attempts, new_lock or None, row["id"]),
                )
                return None, ""
            next_code = self.generate_recovery_code()
            created_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            connection.execute(
                """
                UPDATE accounts
                SET password_hash = ?, recovery_hash = ?, recovery_created_at = ?,
                    recovery_attempts = 0, recovery_locked_until = NULL
                WHERE id = ?
                """,
                (
                    self.hash_password(new_password),
                    self.hash_password(next_code),
                    created_at,
                    row["id"],
                ),
            )
            account = Account(
                id=row["id"],
                email=row["email"],
                created_at=row["created_at"],
                has_recovery_code=True,
            )
        return account, next_code


class SessionManager:
    def __init__(self, ttl_minutes: int) -> None:
        self.ttl_seconds = ttl_minutes * 60
        self._sessions: dict[str, Session] = {}

    def create(self, account: Account, workspace: Any = None) -> Session:
        self.prune()
        session = Session(
            id=secrets.token_urlsafe(32),
            user_id=account.id,
            email=account.email,
            csrf_token=secrets.token_urlsafe(24),
            expires_at=time.time() + self.ttl_seconds,
            workspace=workspace,
        )
        self._sessions[session.id] = session
        return session

    def get(self, session_id: str) -> Session | None:
        session = self._sessions.get(session_id)
        if session is None:
            return None
        if session.expired:
            self._sessions.pop(session_id, None)
            return None
        return session

    def delete(self, session_id: str) -> None:
        self._sessions.pop(session_id, None)

    def delete_for_user(self, user_id: str) -> None:
        expired = [key for key, session in self._sessions.items() if session.user_id == user_id]
        for key in expired:
            self._sessions.pop(key, None)

    def all(self) -> list[Session]:
        self.prune()
        return list(self._sessions.values())

    def prune(self) -> None:
        now = time.time()
        expired = [key for key, value in self._sessions.items() if now >= value.expires_at]
        for key in expired:
            self.delete(key)

    @staticmethod
    def from_request(request: Request, manager: SessionManager) -> Session | None:
        session_id = request.cookies.get(SESSION_COOKIE, "")
        return manager.get(session_id) if session_id else None


def set_session_cookies(
    response: Response, session: Session, *, secure: bool, max_age: int | None = None
) -> None:
    response.set_cookie(
        SESSION_COOKIE,
        session.id,
        httponly=True,
        samesite="lax",
        secure=secure,
        max_age=max_age,
        path="/",
    )
    response.set_cookie(
        CSRF_COOKIE,
        session.csrf_token,
        httponly=False,
        samesite="lax",
        secure=secure,
        max_age=max_age,
        path="/",
    )


def clear_session_cookies(response: Response, *, secure: bool) -> None:
    response.delete_cookie(SESSION_COOKIE, path="/", secure=secure, samesite="lax")
    response.delete_cookie(CSRF_COOKIE, path="/", secure=secure, samesite="lax")


@dataclass
class PrefillPayload:
    fields: dict[str, str] = field(default_factory=dict)
    resume_markdown: str = ""
    expires_at: float = 0.0
