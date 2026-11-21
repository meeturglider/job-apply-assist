from __future__ import annotations

import contextlib
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_host: str = "127.0.0.1"
    app_port: int = 8765
    log_level: str = "INFO"
    shared_password: str = ""
    rate_limit_per_minute: int = Field(default=30, ge=0, le=600)
    trust_proxy_headers: bool = False
    portal_mode: bool = False
    allow_registration: bool = True
    session_ttl_minutes: int = Field(default=480, ge=5, le=10080)
    session_cookie_secure: bool = False

    llm_provider: str = "ollama"
    ollama_base_url: str = "http://localhost:11434"
    ollama_model: str = "qwen3:8b"
    ollama_timeout_seconds: int = 600
    ollama_max_retries: int = Field(default=1, ge=0, le=3)
    ollama_retry_backoff_seconds: float = Field(default=1.0, ge=0, le=10)
    ollama_num_ctx: int = Field(default=32768, ge=4096, le=131072)
    ollama_max_predict: int = Field(default=8192, ge=512, le=32768)

    nlp_provider: str = "none"
    nlp_model: str = "gliner-community/gliner_small-v2.5"
    nlp_model_path: str = ""
    nlp_max_chars: int = Field(default=24000, ge=1000, le=100000)
    nlp_chunk_chars: int = Field(default=4000, ge=1000, le=12000)
    nlp_candidate_limit: int = Field(default=80, ge=10, le=300)
    nlp_threshold: float = Field(default=0.4, ge=0, le=1)

    master_resume_path: Path = Path("MASTER_RESUME.md")
    portfolio_enabled: bool = True
    portfolio_base_url: str = "https://hari.is-a.dev"
    portfolio_cache_seconds: int = 3600

    data_dir: Path = Path("data")

    user_agent: str = Field(
        default=(
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0 Safari/537.36"
        )
    )

    @property
    def applications_dir(self) -> Path:
        return self.data_dir / "applications"

    @property
    def profile_dir(self) -> Path:
        return self.data_dir / "profile"

    @property
    def database_path(self) -> Path:
        return self.data_dir / "applications.db"

    @property
    def accounts_path(self) -> Path:
        return self.data_dir / "accounts.db"

    @staticmethod
    def _secure_directory(path: Path) -> None:
        path.mkdir(parents=True, exist_ok=True)
        with contextlib.suppress(OSError):
            path.chmod(0o700)

    def ensure_dirs(self) -> None:
        self._secure_directory(self.data_dir)
        self._secure_directory(self.applications_dir)
        self._secure_directory(self.profile_dir)


settings = Settings()
