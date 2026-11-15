from __future__ import annotations

import json

from app.config import Settings
from app.models import CandidateProfile
from app.profile.merge import build_profile, profile_hash
from app.profile.portfolio import PortfolioSnapshot, load_portfolio


class ProfileService:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.cache_path = settings.profile_dir / "cache" / "portfolio.json"
        self.profile_path = settings.profile_dir / "candidate.json"

    async def load(self, refresh: bool = False) -> CandidateProfile:
        max_age = 0 if refresh else self.settings.portfolio_cache_seconds
        portfolio: PortfolioSnapshot | None
        try:
            portfolio = await load_portfolio(
                self.settings.portfolio_base_url,
                self.cache_path,
                max_age,
                self.settings.user_agent,
            )
        except Exception as exc:
            portfolio = None
            fallback = self._load_cached_portfolio()
            if fallback is not None:
                portfolio = fallback
                portfolio.payload.setdefault("warnings", []).append(
                    f"Portfolio refresh failed; using cached snapshot. {exc}"
                )

        master_path = self.settings.master_resume_path
        if not master_path.exists():
            raise FileNotFoundError(f"Master resume not found: {master_path}")

        profile = build_profile(master_path, portfolio)
        self.profile_path.parent.mkdir(parents=True, exist_ok=True)
        self.profile_path.write_text(profile.model_dump_json(indent=2), encoding="utf-8")
        return profile

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
        master = self.settings.master_resume_path
        cached = self._load_cached_portfolio()
        return {
            "master_resume": str(master),
            "master_resume_exists": master.exists(),
            "master_resume_modified": master.stat().st_mtime if master.exists() else None,
            "portfolio_base_url": self.settings.portfolio_base_url,
            "portfolio_cached": cached is not None,
            "portfolio_fetched_at": cached.fetched_at.isoformat() if cached else None,
        }
