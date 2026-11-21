from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory

from app.config import Settings
from app.models import CandidateProfile
from app.profile.extras import load_extra_profiles
from app.profile.master_resume import parse_master_resume_text, tag_resume_origin
from app.profile.merge import merge_master_profile, merge_profiles, profile_hash
from app.profile.portfolio import PortfolioSnapshot, load_portfolio
from app.profile.provenance import summarize_provenance
from app.profile.service import MAX_RESUME_BYTES, ProfileService

_TIMESTAMP_FORMAT = "%Y-%m-%dT%H:%M:%SZ"


class MemoryProfileService:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.resume_text = ""
        self.resume_name = ""
        self.portfolio: PortfolioSnapshot | None = None
        self.profile: CandidateProfile | None = None

    async def load(
        self,
        refresh: bool = False,
        extra_urls: list[str] | None = None,
    ) -> CandidateProfile:
        if not self.resume_text:
            raise ValueError("Upload your resume before analyzing a job.")
        portfolio = await self._load_portfolio(refresh)
        master = parse_master_resume_text(self.resume_text, source=self.resume_name)
        tag_resume_origin(master, self.resume_name, uploaded=True)
        profile = merge_master_profile(master, portfolio)
        if portfolio is None:
            profile.warnings.append(
                "Portfolio data was unavailable; using your uploaded resume only."
            )
        if extra_urls:
            try:
                extras = await load_extra_profiles(extra_urls, self.settings)
                profile = merge_profiles(profile, extras.profiles)
                profile.warnings.extend(extras.warnings)
            except Exception as exc:
                profile.warnings.append(f"Additional profile sources failed: {exc}")
        profile.provenance = summarize_provenance(
            profile,
            resume_kind="uploaded",
            resume_name=self.resume_name,
            portfolio_url=self.settings.portfolio_base_url,
            portfolio_cached=portfolio is not None,
            portfolio_fetched_at=portfolio.fetched_at.isoformat() if portfolio else "",
            extra_urls=list(extra_urls or []),
        )
        self.profile = profile
        return profile

    async def _load_portfolio(self, refresh: bool) -> PortfolioSnapshot | None:
        if not self.settings.portfolio_enabled:
            return None
        if self.portfolio is not None and not refresh:
            return self.portfolio
        with TemporaryDirectory(prefix="job-assist-portfolio-") as directory:
            cache_path = Path(directory) / "portfolio.json"
            try:
                self.portfolio = await load_portfolio(
                    self.settings.portfolio_base_url,
                    cache_path,
                    0 if refresh else self.settings.portfolio_cache_seconds,
                    self.settings.user_agent,
                )
            except Exception:
                if self.portfolio is None:
                    self.portfolio = self._snapshot_from_cache(cache_path)
            return self.portfolio

    @staticmethod
    def _snapshot_from_cache(path: Path) -> PortfolioSnapshot | None:
        if not path.exists():
            return None
        try:
            return PortfolioSnapshot(json.loads(path.read_text(encoding="utf-8")))
        except (json.JSONDecodeError, OSError, ValueError):
            return None

    def upload_resume(self, text: str, filename: str = "uploaded_resume.md") -> CandidateProfile:
        value = text.strip()
        if not value:
            raise ValueError("The uploaded resume is empty.")
        if len(value.encode("utf-8")) > MAX_RESUME_BYTES:
            raise ValueError("The uploaded resume is too large (1 MB maximum).")
        source = Path(filename).name or "uploaded_resume.md"
        profile = parse_master_resume_text(value, source=source)
        if not profile.identity.name or not (profile.experience or profile.skills):
            raise ValueError(
                "Could not read a candidate name plus experience or skills from that file."
            )
        self.resume_text = value
        self.resume_name = source
        self.profile = profile
        return profile

    def reset_resume(self) -> None:
        self.resume_text = ""
        self.resume_name = ""
        self.profile = None

    @staticmethod
    def summary(profile: CandidateProfile) -> dict[str, object]:
        return ProfileService.summary(profile)

    @staticmethod
    def hash(profile: CandidateProfile) -> str:
        return profile_hash(profile)

    def status(self) -> dict[str, object]:
        return {
            "master_resume": self.resume_name or "uploaded_resume.md",
            "master_resume_exists": bool(self.resume_text),
            "master_resume_modified": None,
            "default_master_resume": "",
            "uploaded_resume": bool(self.resume_text),
            "portfolio_base_url": self.settings.portfolio_base_url,
            "portfolio_cached": self.portfolio is not None,
            "portfolio_fetched_at": (
                self.portfolio.fetched_at.astimezone(UTC).strftime(_TIMESTAMP_FORMAT)
                if self.portfolio
                else None
            ),
            "session_only": True,
        }


def utc_now_str() -> str:
    return datetime.now(UTC).strftime(_TIMESTAMP_FORMAT)
