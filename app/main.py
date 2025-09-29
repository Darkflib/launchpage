from __future__ import annotations

import logging
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Optional

import uvicorn
import yaml
from astral import moon,LocationInfo
from astral.sun import sun
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from zoneinfo import ZoneInfo
from timezonefinder import TimezoneFinder

try:
    from app.models import (
        AstroQuery,
        AstroResponse,
        SunTimes,
        MoonInfo,
        HealthResponse,
        LinksResponse,
        LinkItem,
    )
    from app.settings import settings
except ModuleNotFoundError:
    # Allow running as a script by adding the project root to sys.path.
    project_root = Path(__file__).resolve().parent.parent
    if str(project_root) not in sys.path:
        sys.path.insert(0, str(project_root))
    from app.models import (
        AstroQuery,
        AstroResponse,
        SunTimes,
        MoonInfo,
        HealthResponse,
        LinksResponse,
        LinkItem,
    )
    from app.settings import settings

# ---------- Logging ----------
LOG_LEVEL = logging.DEBUG if settings.debug else logging.INFO
logging.basicConfig(
    level=LOG_LEVEL,
    format="%(asctime)s %(levelname)s %(name)s - %(message)s",
)
logger = logging.getLogger("astro-api")

# ---------- App ----------
app = FastAPI(title=settings.app_name)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

_tzf = TimezoneFinder(in_memory=True)

# ---------- Utilities ----------


def resolve_timezone(lat: float, lon: float) -> str:
    """
    Find IANA timezone for the given lat/lon.
    Tries exact first, then nearest. Raises HTTPException if not found.
    """
    tz = _tzf.timezone_at(lat=lat, lng=lon)
    if not tz:
        # Set to UTC if no timezone found
        logger.warning("Exact TZ lookup failed for lat=%s, lon=%s - Using UTC", lat, lon)
        tz = 'UTC'
    if not tz:
        logger.warning("Failed to resolve timezone for lat=%s, lon=%s", lat, lon)
        raise HTTPException(
            status_code=400, detail="Unable to resolve timezone for the given location."
        )
    return tz


def moon_phase_name(phase_day_0_29: int) -> str:
    """
    Map Astral’s 0..29 phase day to a human-readable name.
    The boundaries are conventional; there’s no single canonical mapping.
    """
    d = phase_day_0_29 % 30
    if d == 0:
        return "New Moon"
    if 1 <= d <= 6:
        return "Waxing Crescent"
    if d == 7:
        return "First Quarter"
    if 8 <= d <= 13:
        return "Waxing Gibbous"
    if d == 14:
        return "Full Moon"
    if 15 <= d <= 20:
        return "Waning Gibbous"
    if d == 21:
        return "Last Quarter"
    return "Waning Crescent"  # 22-29


def approx_illumination(phase_day_0_29: int) -> float:
    """
    Simple, smooth heuristic for fractional illumination from phase day.
    0..29 mapped onto 0..2π, illumination ≈ (1 - cos(θ)) / 2
    This isn’t precise astronomy, but close enough for a dashboard.
    """
    import math

    theta = (phase_day_0_29 % 30) * (2 * math.pi / 29.53)  # synodic month ~29.53 days
    return max(0.0, min(1.0, (1 - math.cos(theta)) / 2))


def compute_sun_times(
    lat: float, lon: float, tz_name: str, on_date: date, elevation_m: float
) -> SunTimes:
    """
    Uses Astral to compute sun event times for a location/date, including
    civil/nautical/astronomical twilight boundaries. Handles polar edge-cases by
    returning None where events do not occur instead of raising.
    """
    tzinfo = ZoneInfo(tz_name)
    # Astral's LocationInfo: region/city unused; tz is critical
    loc = LocationInfo(
        name="Here", region="", timezone=tz_name, latitude=lat, longitude=lon
    )
    try:
        base = sun(observer=loc.observer, date=on_date, tzinfo=tzinfo)
        civil = base  # Astral default: civil twilight (sun at -6°)
        nautical = sun(
            observer=loc.observer,
            date=on_date,
            tzinfo=tzinfo,
            dawn_dusk_depression=12,
        )
        astronomical = sun(
            observer=loc.observer,
            date=on_date,
            tzinfo=tzinfo,
            dawn_dusk_depression=18,
        )

        # day length might be negative/KeyError at poles; calculate defensively
        sunrise = civil.get("sunrise")
        sunset = civil.get("sunset")
        day_len = (
            int((sunset - sunrise).total_seconds()) if sunrise and sunset else None
        )
        now_local = datetime.now(tz=tzinfo)
        is_day = (
            sunrise is not None
            and sunset is not None
            and sunrise <= now_local <= sunset
        )
        return SunTimes(
            timezone=tz_name,
            date=on_date,
            dawn=civil.get("dawn"),
            sunrise=sunrise,
            solar_noon=civil.get("noon"),
            sunset=sunset,
            dusk=civil.get("dusk"),
            day_length_seconds=day_len,
            is_daylight_now=is_day,
            civil_dawn=civil.get("dawn"),
            civil_dusk=civil.get("dusk"),
            nautical_dawn=nautical.get("dawn"),
            nautical_dusk=nautical.get("dusk"),
            astronomical_dawn=astronomical.get("dawn"),
            astronomical_dusk=astronomical.get("dusk"),
        )
    except Exception as e:
        logger.exception("Astral sun computation failed: %s", e)
        # Return graceful nulls rather than 500; caller still gets moon + tz
        return SunTimes(timezone=tz_name, date=on_date)


def compute_moon(on_date: date) -> MoonInfo:
    """
    Compute simple moon info via Astral: phase day number and a readable name.
    Adds a smooth illumination heuristic.
    """
    try:
        phase_day = int(round(moon.phase(on_date)))
        return MoonInfo(
            phase_day_0_29=phase_day,
            phase_name=moon_phase_name(phase_day),
            illumination_fraction_est=round(approx_illumination(phase_day), 4),
        )
    except Exception as e:
        logger.exception("Astral moon computation failed: %s", e)
        raise HTTPException(status_code=500, detail="Moon calculation failed.")


def load_links_yaml(path: str) -> list[LinkItem]:
    p = Path(path)
    if not p.exists():
        logger.warning("Links file not found at %s; returning empty list.", path)
        return []
    try:
        data = yaml.safe_load(p.read_text(encoding="utf-8")) or []
        items = []
        for row in data:
            try:
                items.append(LinkItem(**row))
            except Exception as e:
                logger.error("Invalid link row %s: %s", row, e)
        return items
    except Exception as e:
        logger.exception("Failed to read links yaml: %s", e)
        raise HTTPException(status_code=500, detail="Failed to read links file.")


# ---------- Routes ----------


@app.get("/health", response_model=HealthResponse, tags=["meta"])
def health() -> HealthResponse:
    return HealthResponse(
        status="ok", app=settings.app_name, time_utc=datetime.now(timezone.utc)
    )


@app.get("/links", response_model=LinksResponse, tags=["links"])
def get_links() -> LinksResponse:
    """
    Returns personal service links from a YAML file.
    Replace `sample_links.yaml` with your discovery-pipeline output if desired.
    """
    return LinksResponse(links=load_links_yaml(settings.links_file))


@app.get("/astro", response_model=AstroResponse, tags=["astro"])
def get_astro(
    lat: float = Query(..., ge=-90, le=90, description="Latitude in degrees."),
    lon: float = Query(..., ge=-180, le=180, description="Longitude in degrees."),
    date_str: Optional[str] = Query(
        default=None,
        description="ISO date (YYYY-MM-DD). Defaults to 'today' in local TZ.",
    ),
    tz_override: Optional[str] = Query(
        default=None, description="Force a specific IANA TZ."
    ),
    elevation_m: float = Query(default=0.0, ge=-430.0, le=9000.0),
) -> AstroResponse:
    """
    Core endpoint. Given lat/lon, compute:
    - IANA timezone
    - 'now' in that timezone
    - Sun times (dawn, sunrise, noon, sunset, dusk, day length, is it day right now?)
    - Moon phase and heuristic illumination

    Notes:
      * Astral dawn/dusk are civil twilight (~ -6°) by default.
      * Polar edge cases return nulls for events that do not occur.
    """
    try:
        tz_name = tz_override or resolve_timezone(lat, lon)
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Unexpected TZ resolution error: %s", e)
        raise HTTPException(
            status_code=500, detail="Internal timezone resolution error."
        )

    try:
        if date_str:
            on_date = date.fromisoformat(date_str)
        else:
            on_date = datetime.now(ZoneInfo(tz_name)).date()
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Invalid date: {e}")

    sun_times = compute_sun_times(lat, lon, tz_name, on_date, elevation_m)
    moon_info = compute_moon(on_date)
    now_local = datetime.now(ZoneInfo(tz_name))

    query_model = AstroQuery(
        lat=lat, lon=lon, date=on_date, tz_override=tz_override, elevation_m=elevation_m
    )

    return AstroResponse(
        query=query_model,
        timezone=tz_name,
        now_local=now_local,
        sun=sun_times,
        moon=moon_info,
    )


# Optional: Feeds stub (wire your discovery pipeline here)
@app.get("/feeds", tags=["feeds"])
def feeds_stub():
    """
    Minimal stub. Replace with your pipeline (e.g., read from SQLite, Redis, or JSON export).
    Returning static structure to keep the starter self-contained.
    """
    return {
        "status": "ok",
        "sources": ["hn", "github_trending", "google_trends", "rss"],
        "items": [],
        "note": "Plug your discovery pipeline here (DB or file).",
    }


def create_app() -> FastAPI:
    return app


if __name__ == "__main__":
    # For local dev; in containers prefer: uvicorn app.main:app --host 0.0.0.0 --port 8000
    uvicorn.run("app.main:app", host="127.0.0.1", port=8000, reload=True)
