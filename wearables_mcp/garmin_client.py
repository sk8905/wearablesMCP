"""Garmin Connect client (read-only) built on the `garminconnect` library.

Authentication is email/password (read from .env). To avoid re-logging in on
every process start — Garmin rate-limits and can temporarily lock accounts on
frequent logins — the garth session tokens are persisted to a local directory
and reused until they expire.

Only read methods are exposed here; nothing writes back to Garmin.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from garminconnect import Garmin

from .config import Config

log = logging.getLogger(__name__)


class GarminAuthError(RuntimeError):
    pass


class GarminClient:
    def __init__(self, config: Config):
        self.config = config
        self._api: Optional[Garmin] = None

    def _client(self) -> Garmin:
        if self._api is not None:
            return self._api

        if not self.config.garmin_email or not self.config.garmin_password:
            raise GarminAuthError(
                "GARMIN_EMAIL / GARMIN_PASSWORD missing from .env."
            )

        session_dir = str(self.config.garmin_session_dir)
        api = Garmin(self.config.garmin_email, self.config.garmin_password)

        # Try to resume a persisted session first; fall back to a fresh login.
        try:
            api.login(session_dir)
            log.info("Resumed Garmin session from %s", session_dir)
        except Exception:  # noqa: BLE001 - garminconnect raises various types
            log.info("No valid Garmin session; performing a fresh login.")
            api.login()
            try:
                api.garth.dump(session_dir)
                log.info("Persisted Garmin session to %s", session_dir)
            except Exception as exc:  # noqa: BLE001
                log.warning("Could not persist Garmin session: %s", exc)

        self._api = api
        return api

    # -- read-only wrappers -------------------------------------------------
    # Each is wrapped so a single failing sub-call (e.g. HRV not available on a
    # given day) degrades gracefully instead of failing the whole request.

    def _safe(self, fn, *args) -> Any:
        try:
            return fn(*args)
        except Exception as exc:  # noqa: BLE001
            log.warning("Garmin call %s%s failed: %s", getattr(fn, "__name__", fn), args, exc)
            return {"error": str(exc)}

    def get_sleep(self, cdate: str) -> Any:
        """Detailed sleep data for a single day (YYYY-MM-DD)."""
        return self._safe(self._client().get_sleep_data, cdate)

    def get_daily_summary(self, cdate: str) -> Any:
        """User daily summary (steps, calories, resting HR, stress avg, etc.)."""
        return self._safe(self._client().get_user_summary, cdate)

    def get_body_battery(self, start_date: str, end_date: str) -> Any:
        return self._safe(self._client().get_body_battery, start_date, end_date)

    def get_hrv(self, cdate: str) -> Any:
        return self._safe(self._client().get_hrv_data, cdate)

    def get_resting_hr(self, cdate: str) -> Any:
        return self._safe(self._client().get_rhr_day, cdate)

    def get_stress(self, cdate: str) -> Any:
        return self._safe(self._client().get_stress_data, cdate)

    def get_activities(self, start_date: str, end_date: str) -> Any:
        """All activities within the inclusive date range (YYYY-MM-DD)."""
        return self._safe(
            self._client().get_activities_by_date, start_date, end_date
        )
