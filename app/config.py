from __future__ import annotations

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

    llm_provider: str = "ollama"
    ollama_base_url: str = "http://localhost:11434"
    ollama_model: str = "qwen3:8b"
    ollama_timeout_seconds: int = 300

    master_resume_path: Path = Path("MASTER_RESUME.md")
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

    def ensure_dirs(self) -> None:
        self.applications_dir.mkdir(parents=True, exist_ok=True)
        self.profile_dir.mkdir(parents=True, exist_ok=True)
        self.data_dir.mkdir(parents=True, exist_ok=True)


settings = Settings()
