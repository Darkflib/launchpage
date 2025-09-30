from __future__ import annotations

import logging
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from time import perf_counter
from typing import Any, Callable, Optional

import uvicorn
import yaml
from astral import Observer, moon
from astral import sun as astral_sun
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse
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
        TimePeriod,
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
        TimePeriod,
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

WEB_ROOT = Path(__file__).resolve().parent.parent / "web"

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

_tzf = TimezoneFinder(in_memory=True)
SunDirection = getattr(astral_sun, "SunDirection")

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


def _record_metric(
    metrics: Optional[dict[str, float]], key: str, start_time: float
) -> None:
    if metrics is None:
        return
    metrics[key] = round((perf_counter() - start_time) * 1000.0, 4)


def build_hourly_elevation_series(
    observer: Observer,
    tzinfo: ZoneInfo,
    on_date: date,
    compute_fn: Callable[[Observer, datetime], float],
    metrics: Optional[dict[str, float]] = None,
    prefix: Optional[str] = None,
) -> dict[str, float]:
    """
    Produce a mapping of ISO timestamps (hourly) to elevation angles in degrees.
    Uses timezone-aware datetimes aligned to the provided tzinfo.
    """
    series_start = perf_counter()
    label = prefix or compute_fn.__name__
    series: dict[str, float] = {}
    for hour in range(24):
        sample = datetime(
            on_date.year,
            on_date.month,
            on_date.day,
            hour,
            tzinfo=tzinfo,
        )
        try:
            value = compute_fn(observer, sample)
        except Exception as err:
            logger.debug(
                "Elevation sample failed for %s at %s: %s",
                compute_fn.__name__,
                sample.isoformat(),
                err,
            )
            continue
        series[sample.isoformat()] = round(float(value), 4)
    _record_metric(metrics, f"{label}.total_ms", series_start)
    return series


def compute_sun_times(
    lat: float,
    lon: float,
    tz_name: str,
    on_date: date,
    elevation_m: float,
    metrics: Optional[dict[str, float]] = None,
    prefix: str = "sun",
) -> SunTimes:
    """
    Uses Astral to compute sun event times for a location/date, including
    civil/nautical/astronomical twilight boundaries. Handles polar edge-cases by
    returning None where events do not occur instead of raising.
    """
    overall_start = perf_counter()
    tzinfo = ZoneInfo(tz_name)
    observer = Observer(latitude=lat, longitude=lon, elevation=elevation_m)
    try:
        step = perf_counter()
        base = astral_sun.sun(observer=observer, date=on_date, tzinfo=tzinfo)
        _record_metric(metrics, f"{prefix}.sun_civil_ms", step)
        civil = base  # Astral default: civil twilight (sun at -6°)
        step = perf_counter()
        nautical = astral_sun.sun(
            observer=observer,
            date=on_date,
            tzinfo=tzinfo,
            dawn_dusk_depression=12,
        )
        _record_metric(metrics, f"{prefix}.sun_nautical_ms", step)
        step = perf_counter()
        astronomical = astral_sun.sun(
            observer=observer,
            date=on_date,
            tzinfo=tzinfo,
            dawn_dusk_depression=18,
        )
        _record_metric(metrics, f"{prefix}.sun_astronomical_ms", step)

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
        def safe_period(
            factory: Callable[..., tuple[datetime, datetime]],
            direction: Any,
        ) -> Optional[TimePeriod]:
            label = getattr(direction, "name", str(direction))
            period_start = perf_counter()
            try:
                start, end = factory(
                    observer=observer,
                    date=on_date,
                    direction=direction,
                    tzinfo=tzinfo,
                )
                _record_metric(
                    metrics,
                    f"{prefix}.{factory.__name__}_{label.lower()}_ms",
                    period_start,
                )
                return TimePeriod(start=start, end=end)
            except ValueError:
                # Twilight window may not exist at high latitudes on some dates.
                logger.debug(
                    "%s twilight (%s) unavailable for lat=%s lon=%s",
                    factory.__name__,
                    label,
                    lat,
                    lon,
                )
                _record_metric(
                    metrics,
                    f"{prefix}.{factory.__name__}_{label.lower()}_ms",
                    period_start,
                )
                return None
            except Exception as err:
                logger.debug(
                    "%s twilight computation failed: %s", factory.__name__, err
                )
                _record_metric(
                    metrics,
                    f"{prefix}.{factory.__name__}_{label.lower()}_ms",
                    period_start,
                )
                return None

        blue_morning = safe_period(astral_sun.blue_hour, SunDirection.RISING)
        blue_evening = safe_period(astral_sun.blue_hour, SunDirection.SETTING)
        golden_morning = safe_period(astral_sun.golden_hour, SunDirection.RISING)
        golden_evening = safe_period(astral_sun.golden_hour, SunDirection.SETTING)

        solar_series = build_hourly_elevation_series(
            observer=observer,
            tzinfo=tzinfo,
            on_date=on_date,
            compute_fn=astral_sun.elevation,
            metrics=metrics,
            prefix=f"{prefix}.elevation_series",
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
            blue_hour_morning=blue_morning,
            blue_hour_evening=blue_evening,
            golden_hour_morning=golden_morning,
            golden_hour_evening=golden_evening,
            solar_elevation_series=solar_series or None,
        )
    except Exception as e:
        logger.exception("Astral sun computation failed: %s", e)
        # Return graceful nulls rather than 500; caller still gets moon + tz
        return SunTimes(timezone=tz_name, date=on_date)
    finally:
        _record_metric(metrics, f"{prefix}.total_ms", overall_start)


def compute_moon(
    lat: float,
    lon: float,
    tz_name: str,
    on_date: date,
    elevation_m: float,
    metrics: Optional[dict[str, float]] = None,
    prefix: str = "moon",
) -> MoonInfo:
    """
    Compute simple moon info via Astral: phase day number and a readable name.
    Adds a smooth illumination heuristic.
    """
    overall_start = perf_counter()
    try:
        step = perf_counter()
        phase_day = int(round(moon.phase(on_date)))
        _record_metric(metrics, f"{prefix}.phase_ms", step)
        tzinfo = ZoneInfo(tz_name)
        observer = Observer(latitude=lat, longitude=lon, elevation=elevation_m)
        elevation_series = build_hourly_elevation_series(
            observer=observer,
            tzinfo=tzinfo,
            on_date=on_date,
            compute_fn=moon.elevation,
            metrics=metrics,
            prefix=f"{prefix}.elevation_series",
        )
        return MoonInfo(
            phase_day_0_29=phase_day,
            phase_name=moon_phase_name(phase_day),
            illumination_fraction_est=round(approx_illumination(phase_day), 4),
            elevation_series=elevation_series or None,
        )
    except Exception as e:
        logger.exception("Astral moon computation failed: %s", e)
        raise HTTPException(status_code=500, detail="Moon calculation failed.")
    finally:
        _record_metric(metrics, f"{prefix}.total_ms", overall_start)


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


@app.get("/", response_class=HTMLResponse, tags=["dashboard"])
def dashboard() -> HTMLResponse:
    """Serve the static dashboard HTML that lives under /web."""
    template_path = WEB_ROOT / "template.html"
    if not template_path.exists():
        logger.error("Dashboard template missing at %s", template_path)
        raise HTTPException(status_code=500, detail="Dashboard template missing.")
    try:
        return HTMLResponse(template_path.read_text(encoding="utf-8"))
    except Exception as exc:  # pragma: no cover - defensive logging
        logger.exception("Failed to read dashboard template: %s", exc)
        raise HTTPException(status_code=500, detail="Failed to load dashboard template.")


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
    profiling: dict[str, float] = {}
    request_start = perf_counter()

    try:
        if tz_override:
            tz_name = tz_override
        else:
            tz_timer = perf_counter()
            tz_name = resolve_timezone(lat, lon)
            _record_metric(profiling, "resolve_timezone_ms", tz_timer)
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Unexpected TZ resolution error: %s", e)
        raise HTTPException(
            status_code=500, detail="Internal timezone resolution error."
        )

    try:
        date_timer = perf_counter()
        if date_str:
            on_date = date.fromisoformat(date_str)
        else:
            on_date = datetime.now(ZoneInfo(tz_name)).date()
        _record_metric(profiling, "resolve_date_ms", date_timer)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Invalid date: {e}")

    sun_times = compute_sun_times(
        lat,
        lon,
        tz_name,
        on_date,
        elevation_m,
        metrics=profiling,
        prefix="sun",
    )
    moon_info = compute_moon(
        lat,
        lon,
        tz_name,
        on_date,
        elevation_m,
        metrics=profiling,
        prefix="moon",
    )
    now_local = datetime.now(ZoneInfo(tz_name))

    query_model = AstroQuery(
        lat=lat, lon=lon, date=on_date, tz_override=tz_override, elevation_m=elevation_m
    )

    _record_metric(profiling, "total_request_ms", request_start)

    return AstroResponse(
        query=query_model,
        timezone=tz_name,
        now_local=now_local,
        sun=sun_times,
        moon=moon_info,
        profiling_ms=profiling or None,
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
