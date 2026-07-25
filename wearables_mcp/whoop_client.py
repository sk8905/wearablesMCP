"""Whoop API v2 client (read-only).

Authentication is OAuth2. This client never runs the interactive login flow
itself — that is done once by `authorize_whoop.py`, which persists an encrypted
token bundle. Here we load that bundle, transparently refresh the access token
when it expires (using the refresh token + client credentials), and expose
read-only collection endpoints with pagination.

Docs: https://developer.whoop.com  (API base: /developer/v2)
"""

from __future__ import annotations

import logging
import time
from typing import Any, Optional

import requests

from .config import Config
from .token_store import TokenStore

log = logging.getLogger(__name__)

WHOOP_AUTH_URL = "https://api.prod.whoop.com/oauth/oauth2/auth"
WHOOP_TOKEN_URL = "https://api.prod.whoop.com/oauth/oauth2/token"
WHOOP_API_BASE = "https://api.prod.whoop.com/developer"

# Read-only scopes. `offline` is required to receive a refresh token.
WHOOP_SCOPES = [
    "offline",
    "read:recovery",
    "read:sleep",
    "read:cycles",
    "read:workout",
    "read:profile",
    "read:body_measurement",
]

# Whoop caps collection page size at 25.
_MAX_LIMIT = 25


class WhoopAuthError(RuntimeError):
    """Raised when we have no usable token and cannot refresh."""


class WhoopClient:
    def __init__(self, config: Config, token_store: Optional[TokenStore] = None):
        self.config = config
        self.token_store = token_store or TokenStore(config.token_path)
        self._token: Optional[dict] = None
        self._session = requests.Session()

    # -- token management ---------------------------------------------------

    def _load_token(self) -> dict:
        if self._token is None:
            self._token = self.token_store.load()
        if not self._token or "refresh_token" not in self._token:
            raise WhoopAuthError(
                "No Whoop token found. Run the one-time authorization first:\n"
                "    python -m wearables_mcp.authorize_whoop"
            )
        return self._token

    def _access_token(self) -> str:
        token = self._load_token()
        # Refresh a minute early to avoid races with the expiry boundary.
        if time.time() >= token.get("expires_at", 0) - 60:
            self._refresh()
            token = self._token  # type: ignore[assignment]
        return token["access_token"]

    def _refresh(self) -> None:
        token = self._load_token()
        if not self.config.whoop_client_id or not self.config.whoop_client_secret:
            raise WhoopAuthError(
                "WHOOP_CLIENT_ID / WHOOP_CLIENT_SECRET missing from .env; "
                "cannot refresh the access token."
            )
        log.info("Refreshing Whoop access token.")
        resp = self._session.post(
            WHOOP_TOKEN_URL,
            data={
                "grant_type": "refresh_token",
                "refresh_token": token["refresh_token"],
                "client_id": self.config.whoop_client_id,
                "client_secret": self.config.whoop_client_secret,
                # Must re-request offline to keep getting refresh tokens.
                "scope": "offline",
            },
            timeout=30,
        )
        if resp.status_code != 200:
            raise WhoopAuthError(
                f"Whoop token refresh failed ({resp.status_code}): {resp.text[:300]}. "
                "You may need to re-run `python -m wearables_mcp.authorize_whoop`."
            )
        data = resp.json()
        new_token = {
            "access_token": data["access_token"],
            # Whoop rotates refresh tokens; fall back to the old one if absent.
            "refresh_token": data.get("refresh_token", token["refresh_token"]),
            "expires_at": time.time() + int(data.get("expires_in", 3600)),
            "scope": data.get("scope", token.get("scope", "")),
            "token_type": data.get("token_type", "bearer"),
        }
        self._token = new_token
        self.token_store.save(new_token)

    # -- HTTP ---------------------------------------------------------------

    def _get(self, path: str, params: Optional[dict] = None) -> dict:
        url = f"{WHOOP_API_BASE}{path}"
        headers = {"Authorization": f"Bearer {self._access_token()}"}
        resp = self._session.get(url, headers=headers, params=params, timeout=30)
        if resp.status_code == 401:
            # Access token may have been revoked; try one forced refresh.
            log.info("Whoop returned 401; forcing a token refresh and retrying.")
            self._refresh()
            headers = {"Authorization": f"Bearer {self._access_token()}"}
            resp = self._session.get(url, headers=headers, params=params, timeout=30)
        if resp.status_code == 429:
            raise RuntimeError(
                "Whoop rate limit hit (429). Try again later or rely on the cache."
            )
        resp.raise_for_status()
        return resp.json()

    def _paginate(
        self, path: str, start: str, end: str, limit: int = _MAX_LIMIT
    ) -> list[dict]:
        """Collect all records across pages for a collection endpoint.

        `start`/`end` are ISO-8601 timestamps (e.g. 2026-07-01T00:00:00Z).
        """
        records: list[dict] = []
        next_token: Optional[str] = None
        limit = max(1, min(limit, _MAX_LIMIT))
        while True:
            params: dict[str, Any] = {"start": start, "end": end, "limit": limit}
            if next_token:
                params["nextToken"] = next_token
            page = self._get(path, params=params)
            records.extend(page.get("records", []))
            next_token = page.get("next_token")
            if not next_token:
                break
        return records

    # -- read-only endpoints ------------------------------------------------

    def get_profile(self) -> dict:
        return self._get("/v2/user/profile/basic")

    def get_recovery(self, start: str, end: str) -> list[dict]:
        """Recovery collection: recovery score, HRV (rmssd), resting heart rate."""
        return self._paginate("/v2/recovery", start, end)

    def get_sleep(self, start: str, end: str) -> list[dict]:
        """Sleep collection: stages, performance/efficiency/consistency, durations."""
        return self._paginate("/v2/activity/sleep", start, end)

    def get_cycles(self, start: str, end: str) -> list[dict]:
        """Physiological cycles: daily strain, average/max HR, energy expenditure."""
        return self._paginate("/v2/cycle", start, end)

    def get_workouts(self, start: str, end: str) -> list[dict]:
        """Workout collection: per-workout strain, HR, distance, zone durations."""
        return self._paginate("/v2/activity/workout", start, end)
