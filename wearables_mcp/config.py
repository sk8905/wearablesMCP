"""Configuration and local state paths.

All secrets are read from a local `.env` file (never hardcoded, never
committed). Local state (encrypted Whoop token, SQLite cache, Garmin
session tokens) lives under DATA_DIR, which defaults to ~/.wearables-mcp
and is kept out of the git repo.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

# Load .env from the project root (parent of this package directory).
# override=False so real environment variables win over the file.
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(_PROJECT_ROOT / ".env", override=False)


def _data_dir() -> Path:
    raw = os.getenv("DATA_DIR")
    path = Path(raw).expanduser() if raw else Path.home() / ".wearables-mcp"
    path.mkdir(parents=True, exist_ok=True)
    # Best effort: keep local state private on POSIX systems.
    try:
        os.chmod(path, 0o700)
    except OSError:
        pass
    return path


@dataclass(frozen=True)
class Config:
    # Whoop
    whoop_client_id: str
    whoop_client_secret: str
    whoop_redirect_uri: str
    # Garmin
    garmin_email: str
    garmin_password: str
    # State
    data_dir: Path
    cache_ttl_hours: float

    @property
    def token_path(self) -> Path:
        """Encrypted Whoop OAuth token file."""
        return self.data_dir / "whoop_token.enc"

    @property
    def cache_path(self) -> Path:
        """SQLite response cache."""
        return self.data_dir / "cache.sqlite"

    @property
    def garmin_session_dir(self) -> Path:
        """Directory where garminconnect/garth persists its session tokens."""
        d = self.data_dir / "garmin_session"
        d.mkdir(parents=True, exist_ok=True)
        return d


def load_config() -> Config:
    """Build a Config from the environment. Missing values are allowed here;
    each client validates the specific vars it needs when first used, so the
    server can still start (and report a clear error) if only one integration
    is configured.
    """
    data_dir = _data_dir()
    ttl_raw = os.getenv("CACHE_TTL_HOURS", "6")
    try:
        ttl = float(ttl_raw)
    except ValueError:
        ttl = 6.0

    return Config(
        whoop_client_id=os.getenv("WHOOP_CLIENT_ID", "").strip(),
        whoop_client_secret=os.getenv("WHOOP_CLIENT_SECRET", "").strip(),
        whoop_redirect_uri=os.getenv(
            "WHOOP_REDIRECT_URI", "http://localhost:8080/callback"
        ).strip(),
        garmin_email=os.getenv("GARMIN_EMAIL", "").strip(),
        garmin_password=os.getenv("GARMIN_PASSWORD", ""),
        data_dir=data_dir,
        cache_ttl_hours=ttl,
    )
