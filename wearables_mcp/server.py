"""Local, read-only MCP server exposing Whoop and Garmin health data.

Transport is stdio only: Claude Code launches this as a subprocess. No network
listener, no remote hosting. The only outbound traffic is direct HTTPS to
Whoop's and Garmin's own APIs.

Tools (all read-only):
    get_whoop_recovery, get_whoop_sleep, get_whoop_strain,
    get_garmin_sleep, get_garmin_daily_summary, get_garmin_activities
"""

from __future__ import annotations

import logging
import sys
from datetime import date, datetime, timedelta
from typing import Any, Optional

from mcp.server.fastmcp import FastMCP

from .cache import Cache
from .config import load_config
from .garmin_client import GarminClient
from .whoop_client import WhoopClient

# IMPORTANT: log to stderr only. Anything on stdout corrupts the MCP protocol.
logging.basicConfig(
    level=logging.INFO,
    stream=sys.stderr,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger("wearables_mcp")

config = load_config()
cache = Cache(config.cache_path)
whoop = WhoopClient(config)
garmin = GarminClient(config)

mcp = FastMCP("wearables")


# --- date helpers ---------------------------------------------------------


def _resolve_range(start_date: Optional[str], end_date: Optional[str]) -> tuple[str, str]:
    """Normalize an optional (start_date, end_date) pair of YYYY-MM-DD strings.

    Defaults to the last 7 days (inclusive of today) when either is omitted.
    Returns (start, end) as YYYY-MM-DD.
    """
    today = date.today()
    end = _parse_day(end_date) if end_date else today
    start = _parse_day(start_date) if start_date else end - timedelta(days=7)
    if start > end:
        start, end = end, start
    return start.isoformat(), end.isoformat()


def _parse_day(value: str) -> date:
    try:
        return datetime.strptime(value.strip(), "%Y-%m-%d").date()
    except ValueError as exc:
        raise ValueError(
            f"Invalid date {value!r}; expected YYYY-MM-DD (e.g. 2026-07-25)."
        ) from exc


def _iso_window(start_day: str, end_day: str) -> tuple[str, str]:
    """Convert an inclusive YYYY-MM-DD day range to the ISO-8601 UTC timestamp
    window Whoop expects (end is pushed to the end of that day)."""
    start = f"{start_day}T00:00:00.000Z"
    end_dt = _parse_day(end_day) + timedelta(days=1)
    end = f"{end_dt.isoformat()}T00:00:00.000Z"
    return start, end


def _each_day(start_day: str, end_day: str) -> list[str]:
    d = _parse_day(start_day)
    last = _parse_day(end_day)
    out = []
    while d <= last:
        out.append(d.isoformat())
        d += timedelta(days=1)
    return out


def _cached(key: str, producer) -> Any:
    """Return cached value for key or compute+store it. Honors CACHE_TTL_HOURS."""
    hit = cache.get(key, config.cache_ttl_hours)
    if hit is not None:
        return hit
    value = producer()
    cache.set(key, value)
    return value


# --- Whoop tools ----------------------------------------------------------


@mcp.tool()
def get_whoop_recovery(
    start_date: Optional[str] = None, end_date: Optional[str] = None
) -> dict:
    """Whoop recovery over a date range: recovery score %, HRV (RMSSD, ms),
    and resting heart rate.

    Args:
        start_date: Range start, YYYY-MM-DD. Defaults to 7 days before end_date.
        end_date: Range end (inclusive), YYYY-MM-DD. Defaults to today.
    """
    start_day, end_day = _resolve_range(start_date, end_date)
    iso_start, iso_end = _iso_window(start_day, end_day)
    records = _cached(
        f"whoop:recovery:{iso_start}:{iso_end}",
        lambda: whoop.get_recovery(iso_start, iso_end),
    )
    return {
        "source": "whoop",
        "metric": "recovery",
        "range": {"start": start_day, "end": end_day},
        "count": len(records),
        "records": records,
    }


@mcp.tool()
def get_whoop_sleep(
    start_date: Optional[str] = None, end_date: Optional[str] = None
) -> dict:
    """Whoop sleep over a date range: sleep stages (light/deep/REM/awake),
    durations, and sleep performance/efficiency/consistency percentages.

    Args:
        start_date: Range start, YYYY-MM-DD. Defaults to 7 days before end_date.
        end_date: Range end (inclusive), YYYY-MM-DD. Defaults to today.
    """
    start_day, end_day = _resolve_range(start_date, end_date)
    iso_start, iso_end = _iso_window(start_day, end_day)
    records = _cached(
        f"whoop:sleep:{iso_start}:{iso_end}",
        lambda: whoop.get_sleep(iso_start, iso_end),
    )
    return {
        "source": "whoop",
        "metric": "sleep",
        "range": {"start": start_day, "end": end_day},
        "count": len(records),
        "records": records,
    }


@mcp.tool()
def get_whoop_strain(
    start_date: Optional[str] = None, end_date: Optional[str] = None
) -> dict:
    """Whoop strain over a date range: daily strain (from physiological cycles)
    plus individual workouts (per-workout strain, heart rate, distance).

    Args:
        start_date: Range start, YYYY-MM-DD. Defaults to 7 days before end_date.
        end_date: Range end (inclusive), YYYY-MM-DD. Defaults to today.
    """
    start_day, end_day = _resolve_range(start_date, end_date)
    iso_start, iso_end = _iso_window(start_day, end_day)
    cycles = _cached(
        f"whoop:cycle:{iso_start}:{iso_end}",
        lambda: whoop.get_cycles(iso_start, iso_end),
    )
    workouts = _cached(
        f"whoop:workout:{iso_start}:{iso_end}",
        lambda: whoop.get_workouts(iso_start, iso_end),
    )
    return {
        "source": "whoop",
        "metric": "strain",
        "range": {"start": start_day, "end": end_day},
        "daily_cycles": cycles,
        "workouts": workouts,
    }


# --- Garmin tools ---------------------------------------------------------


@mcp.tool()
def get_garmin_sleep(
    start_date: Optional[str] = None, end_date: Optional[str] = None
) -> dict:
    """Garmin sleep over a date range: sleep stages, duration, and sleep
    scores for each day.

    Args:
        start_date: Range start, YYYY-MM-DD. Defaults to 7 days before end_date.
        end_date: Range end (inclusive), YYYY-MM-DD. Defaults to today.
    """
    start_day, end_day = _resolve_range(start_date, end_date)
    days: dict[str, Any] = {}
    for day in _each_day(start_day, end_day):
        days[day] = _cached(
            f"garmin:sleep:{day}", lambda d=day: garmin.get_sleep(d)
        )
    return {
        "source": "garmin",
        "metric": "sleep",
        "range": {"start": start_day, "end": end_day},
        "days": days,
    }


@mcp.tool()
def get_garmin_daily_summary(
    start_date: Optional[str] = None, end_date: Optional[str] = None
) -> dict:
    """Garmin daily summary over a date range. Per day: the user daily summary
    (steps, calories, resting HR, average stress), Body Battery, HRV, resting
    heart rate, and stress.

    Args:
        start_date: Range start, YYYY-MM-DD. Defaults to 7 days before end_date.
        end_date: Range end (inclusive), YYYY-MM-DD. Defaults to today.
    """
    start_day, end_day = _resolve_range(start_date, end_date)
    days: dict[str, Any] = {}
    for day in _each_day(start_day, end_day):
        days[day] = _cached(
            f"garmin:summary:{day}",
            lambda d=day: {
                "summary": garmin.get_daily_summary(d),
                "body_battery": garmin.get_body_battery(d, d),
                "hrv": garmin.get_hrv(d),
                "resting_heart_rate": garmin.get_resting_hr(d),
                "stress": garmin.get_stress(d),
            },
        )
    return {
        "source": "garmin",
        "metric": "daily_summary",
        "range": {"start": start_day, "end": end_day},
        "days": days,
    }


@mcp.tool()
def get_garmin_activities(
    start_date: Optional[str] = None, end_date: Optional[str] = None
) -> dict:
    """Garmin activities (workouts) within a date range: type, duration,
    distance, heart rate, calories, and other per-activity metrics.

    Args:
        start_date: Range start, YYYY-MM-DD. Defaults to 7 days before end_date.
        end_date: Range end (inclusive), YYYY-MM-DD. Defaults to today.
    """
    start_day, end_day = _resolve_range(start_date, end_date)
    activities = _cached(
        f"garmin:activities:{start_day}:{end_day}",
        lambda: garmin.get_activities(start_day, end_day),
    )
    count = len(activities) if isinstance(activities, list) else None
    return {
        "source": "garmin",
        "metric": "activities",
        "range": {"start": start_day, "end": end_day},
        "count": count,
        "activities": activities,
    }


def main() -> None:
    log.info("Starting wearables MCP server (stdio). Data dir: %s", config.data_dir)
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
