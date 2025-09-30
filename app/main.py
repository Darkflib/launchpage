from __future__ import annotations

import logging
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from time import perf_counter
from typing import Any, Callable, Optional

import uvicorn
import yaml
import httpx
from astral import Observer, moon
from astral import sun as astral_sun
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
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
        WeatherResponse,
        WeatherCurrent,
        WeatherCondition,
        WeatherForecast,
        ForecastDay,
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
        WeatherResponse,
        WeatherCurrent,
        WeatherCondition,
        WeatherForecast,
        ForecastDay,
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
FAVICONS_DIR = WEB_ROOT / "favicons"

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Mount static files for favicons
app.mount("/favicons", StaticFiles(directory=FAVICONS_DIR), name="favicons")

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
    This isn't precise astronomy, but close enough for a dashboard.
    """
    import math

    theta = (phase_day_0_29 % 30) * (2 * math.pi / 29.53)  # synodic month ~29.53 days
    return max(0.0, min(1.0, (1 - math.cos(theta)) / 2))


def find_next_moon_phase(from_date: date, target_phase: int, max_days: int = 60) -> date:
    """
    Find the next occurrence of a specific moon phase.

    Args:
        from_date: Starting date to search from
        target_phase: Target phase day (0=new moon, 7=first quarter, 14=full moon, 21=last quarter)
        max_days: Maximum days to search ahead (default 60 = 2 lunar cycles)

    Returns:
        Date of the next occurrence of the target phase
    """
    current_date = from_date

    for _ in range(max_days):
        current_date = current_date + __import__('datetime').timedelta(days=1)
        phase = int(round(moon.phase(current_date)))

        # Check if we've hit the target phase
        # Account for phase wrapping (29 -> 0)
        if phase == target_phase:
            return current_date

    # Fallback: return approximate date based on lunar cycle
    # Average lunar cycle is 29.53 days
    from datetime import timedelta
    current_phase = int(round(moon.phase(from_date)))
    days_to_target = (target_phase - current_phase) % 30
    if days_to_target == 0 and current_phase != target_phase:
        days_to_target = 30
    return from_date + timedelta(days=days_to_target)


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
    Adds a smooth illumination heuristic and calculates next new/full moon dates.
    """
    overall_start = perf_counter()
    try:
        step = perf_counter()
        phase_day = int(round(moon.phase(on_date)))
        _record_metric(metrics, f"{prefix}.phase_ms", step)

        # Calculate next new moon and full moon
        step = perf_counter()
        next_new = find_next_moon_phase(on_date, 0)  # 0 = new moon
        next_full = find_next_moon_phase(on_date, 14)  # 14 = full moon
        _record_metric(metrics, f"{prefix}.next_phases_ms", step)

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
            next_new_moon=next_new,
            next_full_moon=next_full,
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


@app.get("/weather", response_model=WeatherResponse, tags=["weather"])
async def get_weather(
    lat: float = Query(..., ge=-90, le=90, description="Latitude in degrees."),
    lon: float = Query(..., ge=-180, le=180, description="Longitude in degrees."),
    days: int = Query(default=0, ge=0, le=10, description="Number of forecast days (0-10). 0 = current only."),
) -> WeatherResponse:
    """
    Weather proxy endpoint that normalizes data from weather providers.
    Currently supports WeatherAPI.com but abstracted for easy provider switching.

    Returns normalized weather data including:
    - Current temperature (C and F)
    - Feels like temperature
    - Humidity, wind, pressure
    - Precipitation
    - Weather condition with icon
    - UV index
    - Optional multi-day forecast (up to 10 days)
    """
    if not settings.weatherapi_key:
        raise HTTPException(
            status_code=503,
            detail="Weather service not configured. Set WEATHERAPI_KEY in environment.",
        )

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            # WeatherAPI.com endpoint - use forecast.json if days > 0, otherwise current.json
            if days > 0:
                url = f"{settings.weatherapi_url}forecast.json"
                params = {
                    "key": settings.weatherapi_key,
                    "q": f"{lat},{lon}",
                    "days": days,
                    "aqi": "no",
                }
            else:
                url = f"{settings.weatherapi_url}current.json"
                params = {
                    "key": settings.weatherapi_key,
                    "q": f"{lat},{lon}",
                    "aqi": "no",
                }

            response = await client.get(url, params=params)
            response.raise_for_status()
            data = response.json()

            # Normalize response to our format
            location_data = data.get("location", {})
            current_data = data.get("current", {})
            condition_data = current_data.get("condition", {})

            # Parse forecast data if available
            forecast = None
            if days > 0 and "forecast" in data:
                forecast_data = data["forecast"].get("forecastday", [])
                forecast_days = []
                for day_data in forecast_data:
                    day = day_data.get("day", {})
                    day_condition = day.get("condition", {})
                    forecast_days.append(ForecastDay(
                        date=day_data.get("date", ""),
                        date_epoch=day_data.get("date_epoch", 0),
                        max_temp_c=day.get("maxtemp_c", 0.0),
                        max_temp_f=day.get("maxtemp_f", 0.0),
                        min_temp_c=day.get("mintemp_c", 0.0),
                        min_temp_f=day.get("mintemp_f", 0.0),
                        avg_temp_c=day.get("avgtemp_c", 0.0),
                        avg_temp_f=day.get("avgtemp_f", 0.0),
                        max_wind_kph=day.get("maxwind_kph", 0.0),
                        max_wind_mph=day.get("maxwind_mph", 0.0),
                        total_precip_mm=day.get("totalprecip_mm", 0.0),
                        avg_humidity=day.get("avghumidity", 0),
                        condition=WeatherCondition(
                            text=day_condition.get("text", "Unknown"),
                            icon=day_condition.get("icon", ""),
                            code=day_condition.get("code", 0),
                        ),
                        uv=day.get("uv", 0.0),
                        daily_chance_of_rain=day.get("daily_chance_of_rain", 0),
                        daily_chance_of_snow=day.get("daily_chance_of_snow", 0),
                    ))
                forecast = WeatherForecast(days=forecast_days)

            return WeatherResponse(
                location=location_data.get("name", "Unknown"),
                region=location_data.get("region"),
                country=location_data.get("country", "Unknown"),
                localtime=location_data.get("localtime", ""),
                current=WeatherCurrent(
                    temp_c=current_data.get("temp_c", 0.0),
                    temp_f=current_data.get("temp_f", 0.0),
                    feels_like_c=current_data.get("feelslike_c", 0.0),
                    feels_like_f=current_data.get("feelslike_f", 0.0),
                    humidity=current_data.get("humidity", 0),
                    wind_kph=current_data.get("wind_kph", 0.0),
                    wind_mph=current_data.get("wind_mph", 0.0),
                    wind_dir=current_data.get("wind_dir", ""),
                    pressure_mb=current_data.get("pressure_mb", 0.0),
                    precip_mm=current_data.get("precip_mm", 0.0),
                    condition=WeatherCondition(
                        text=condition_data.get("text", "Unknown"),
                        icon=condition_data.get("icon", ""),
                        code=condition_data.get("code", 0),
                    ),
                    uv=current_data.get("uv", 0.0),
                ),
                forecast=forecast,
            )

    except httpx.HTTPStatusError as e:
        logger.error(f"Weather API HTTP error: {e.response.status_code}")
        raise HTTPException(
            status_code=e.response.status_code,
            detail=f"Weather API error: {e.response.text}",
        )
    except httpx.RequestError as e:
        logger.error(f"Weather API request error: {e}")
        raise HTTPException(
            status_code=503, detail="Unable to reach weather service"
        )
    except Exception as e:
        logger.exception(f"Unexpected weather API error: {e}")
        raise HTTPException(status_code=500, detail="Internal weather service error")


def create_app() -> FastAPI:
    return app


if __name__ == "__main__":
    # For local dev; in containers prefer: uvicorn app.main:app --host 0.0.0.0 --port 8000
    uvicorn.run("app.main:app", host="127.0.0.1", port=8000, reload=True)
