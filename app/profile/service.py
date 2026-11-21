from __future__ import annotations

import json
from pathlib import Path

from app.config import Settings
from app.models import CandidateProfile
from app.profile.extras import load_extra_profiles
from app.profile.master_resume import parse_master_resume_text
from app.profile.merge import build_profile, merge_profiles, profile_hash
from app.profile.portfolio import PortfolioSnapshot, load_portfolio
from app.profile.provenance import summarize_provenance

MAX_RESUME_BYTES = 1_000_000


class ProfileService:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.cache_path = settings.profile_dir / "cache" / "portfolio.json"
        self.profile_path = settings.profile_dir / "candidate.json"
        self.uploaded_resume_path = settings.profile_dir / "resume.md"

    @property
    def active_resume_path(self) -> Path:
        if self.uploaded_resume_path.exists():
            return self.uploaded_resume_path
        return self.settings.master_resume_path

    async def load(
        self,
        refresh: bool = False,
        extra_urls: list[str] | None = None,
    ) -> CandidateProfile:
        max_age = 0 if refresh else self.settings.portfolio_cache_seconds
        portfolio: PortfolioSnapshot | None = None
        if self.settings.portfolio_enabled:
            try:
                portfolio = await load_portfolio(
                    self.settings.portfolio_base_url,
                    self.cache_path,
                    max_age,
                    self.settings.user_agent,
                )
            except Exception as exc:
                fallback = self._load_cached_portfolio()
                if fallback is not None:
                    portfolio = fallback
                    portfolio.payload.setdefault("warnings", []).append(
                        f"Portfolio refresh failed; using cached snapshot. {exc}"
                    )

        master_path = self.active_resume_path
        if not master_path.exists():
            raise FileNotFoundError(f"Resume not found: {master_path}")

        profile = build_profile(
            master_path, portfolio, uploaded_resume=self.uploaded_resume_path.exists()
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
            resume_kind="uploaded" if self.uploaded_resume_path.exists() else "default",
            resume_name=master_path.name,
            portfolio_url=self.settings.portfolio_base_url,
            portfolio_cached=portfolio is not None,
            portfolio_fetched_at=portfolio.fetched_at.isoformat() if portfolio else "",
            extra_urls=list(extra_urls or []),
        )
        self.profile_path.parent.mkdir(parents=True, exist_ok=True)
        self.profile_path.write_text(profile.model_dump_json(indent=2), encoding="utf-8")
        return profile

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
        self.uploaded_resume_path.parent.mkdir(parents=True, exist_ok=True)
        self.uploaded_resume_path.write_text(value, encoding="utf-8")
        return profile

    def reset_resume(self) -> None:
        self.uploaded_resume_path.unlink(missing_ok=True)

    @staticmethod
    def summary(profile: CandidateProfile) -> dict[str, object]:
        return {
            "identity": profile.identity.model_dump(),
            "experience_years": profile.experience_years,
            "experience_count": len(profile.experience),
            "project_count": len(profile.projects),
            "skill_count": len(profile.skills),
            "certification_count": len(profile.certifications),
            "award_count": len(profile.awards),
            "warnings": profile.warnings,
            "sources": profile.sources,
            "provenance": profile.provenance.model_dump(),
        }

    def _load_cached_portfolio(self) -> PortfolioSnapshot | None:
        if not self.cache_path.exists():
            return None
        try:
            return PortfolioSnapshot(json.loads(self.cache_path.read_text(encoding="utf-8")))
        except (json.JSONDecodeError, OSError):
            return None

    @staticmethod
    def hash(profile: CandidateProfile) -> str:
        return profile_hash(profile)

    def status(self) -> dict[str, object]:
        active = self.active_resume_path
        default = self.settings.master_resume_path
        cached = self._load_cached_portfolio()
        return {
            "master_resume": str(active),
            "master_resume_exists": active.exists(),
            "master_resume_modified": active.stat().st_mtime if active.exists() else None,
            "default_master_resume": str(default),
            "uploaded_resume": self.uploaded_resume_path.exists(),
            "portfolio_base_url": self.settings.portfolio_base_url,
            "portfolio_cached": cached is not None,
            "portfolio_fetched_at": cached.fetched_at.isoformat() if cached else None,
        }
