"""Tiny SQLite response cache with a refresh-if-older-than-N-hours policy.

Keyed by an opaque string (source + endpoint + params). Values are JSON blobs.
This exists so repeated questions to Claude don't hammer the Whoop/Garmin APIs
and trip their rate limits.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import time
from pathlib import Path
from typing import Any, Optional

log = logging.getLogger(__name__)


class Cache:
    def __init__(self, path: Path):
        self.path = str(path)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        con = sqlite3.connect(self.path, timeout=10)
        con.execute("PRAGMA journal_mode=WAL")
        return con

    def _init_db(self) -> None:
        with self._connect() as con:
            con.execute(
                """
                CREATE TABLE IF NOT EXISTS cache (
                    key        TEXT PRIMARY KEY,
                    value      TEXT NOT NULL,
                    fetched_at REAL NOT NULL
                )
                """
            )

    def get(self, key: str, max_age_hours: float) -> Optional[Any]:
        """Return the cached value if present and fresher than max_age_hours,
        else None."""
        with self._connect() as con:
            row = con.execute(
                "SELECT value, fetched_at FROM cache WHERE key = ?", (key,)
            ).fetchone()
        if row is None:
            return None
        value, fetched_at = row
        age_hours = (time.time() - fetched_at) / 3600.0
        if age_hours > max_age_hours:
            log.debug("Cache stale for %s (%.1fh old)", key, age_hours)
            return None
        log.debug("Cache hit for %s (%.1fh old)", key, age_hours)
        return json.loads(value)

    def set(self, key: str, value: Any) -> None:
        with self._connect() as con:
            con.execute(
                "INSERT OR REPLACE INTO cache (key, value, fetched_at) "
                "VALUES (?, ?, ?)",
                (key, json.dumps(value), time.time()),
            )
