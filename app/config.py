"""Application configuration loaded from environment variables (.env)."""

from __future__ import annotations

import os
import platform
from dataclasses import dataclass
from pathlib import Path
from dotenv import load_dotenv


PROJECT_ROOT = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class Settings:
    """Runtime settings for the assistant (secrets never exposed via public_config)."""

    gemini_api_key: str
    llm_model: str
    allowed_hosts: tuple[str, ...]
    runner_timeout_sec: int
    browser_slowmo_ms: int
    browser_hold_ms: int
    storage_dir: Path
    runs_dir: Path
    db_path: Path

    def public_config(self) -> dict:
        """Return safe runtime and environment metadata for UI display & evidence export."""
        return {
            "provider": "Google Gemini REST API (Free Tier)",
            "configured_model": self.llm_model,
            "api_key_configured": bool(
                self.gemini_api_key
                and self.gemini_api_key != "your_free_tier_gemini_api_key_here"
            ),
            "allowed_hosts": list(self.allowed_hosts),
            "runner_timeout_sec": self.runner_timeout_sec,
            "browser_slowmo_ms": self.browser_slowmo_ms,
            "isolation_mode": "Restricted Python subprocess (shell=False, stripped env, per-run sandbox dir)",
            "runtime_environment": {
                "os": f"{platform.system()} {platform.release()}",
                "python_version": platform.python_version(),
            },
        }


def load_settings() -> Settings:
    """Load configuration from .env (if present) and OS environment variables."""
    load_dotenv(PROJECT_ROOT / ".env", override=True)

    raw_hosts = os.getenv("ALLOWED_HOSTS", "").strip()
    allowed_hosts = tuple(
        host.strip().lower() for host in raw_hosts.split(",") if host.strip()
    )

    storage_dir = PROJECT_ROOT / "storage"
    runs_dir = storage_dir / "runs"
    db_path = storage_dir / "assistant.db"

    return Settings(
        gemini_api_key=os.getenv("GEMINI_API_KEY", "").strip(),
        llm_model=os.getenv("LLM_MODEL", "gemini-3.5-flash-lite").strip()
        or "gemini-3.5-flash-lite",
        allowed_hosts=allowed_hosts,
        runner_timeout_sec=int(os.getenv("RUNNER_TIMEOUT_SEC", "60")),
        browser_slowmo_ms=int(os.getenv("BROWSER_SLOWMO_MS", "0")),
        browser_hold_ms=int(os.getenv("BROWSER_HOLD_MS", "1200")),
        storage_dir=storage_dir,
        runs_dir=runs_dir,
        db_path=db_path,
    )
