# Astro API Dashboard - AI Agent Guide

## Project Overview

**Astro API** is a FastAPI application providing astronomical data (sun/moon times, twilight periods, weather) via REST API with an embedded single-page dashboard. The app prioritizes privacy (offline timezone resolution), graceful degradation (polar regions), and performance (with optional profiling metrics).

**Author**: Mike (darkflib) | **Tech Stack**: Python 3.12+, FastAPI, Astral, uv package manager

## Architecture

### Backend Structure (FastAPI)
- **`app/main.py`** (754 lines): Core application with all route handlers, astronomical computations, and weather proxy
  - Sun calculations: `compute_sun_times()` - handles civil/nautical/astronomical twilight, golden/blue hours
  - Moon calculations: `compute_moon()` - phase, illumination (using next new moon method), next new/full moon
  - Moon illumination: `approx_illumination()` - calculates accurate illumination from next new moon datetime (NOT using Astral's phase() directly)
  - Privacy-first: `resolve_timezone()` uses offline `TimezoneFinder` - coordinates never leave server
  - Weather proxy: `get_weather()` - async endpoint normalizing WeatherAPI.com data
- **`app/models.py`**: Pydantic models (`AstroQuery`, `AstroResponse`, `SunTimes`, `MoonInfo`, `WeatherResponse`, etc.)
- **`app/settings.py`**: Pydantic Settings with dotenv support (API keys, CORS, cache TTL)
- **`app/sample_links.yaml`**: Personal service links configuration (YAML format)

### Frontend (Single Page)
- **`web/template.html`** (2044 lines): Complete vanilla JavaScript dashboard served at `/`
  - Real-time sun position arc calculated every second from cached sunrise/sunset
  - Dark/light theme toggle with localStorage persistence
  - Smart caching: 24h for astro data, 15min for weather
  - CSS variable-based theming (`[data-theme="dark"]`) with smooth transitions

## Critical Development Patterns

### 1. Graceful Polar Edge Cases
Sun/moon events may not occur in polar regions. **Always return `None`** rather than raising errors:
```python
# Example from compute_sun_times()
sunrise = civil.get("sunrise")  # Returns None if no sunrise
day_len = int((sunset - sunrise).total_seconds()) if sunrise and sunset else None
```

### 2. Performance Profiling
Optional `profiling_ms` dict tracks computation time. Use helper `_record_metric()`:
```python
def compute_sun_times(..., metrics: Optional[dict[str, float]] = None):
    step = perf_counter()
    # ... computation ...
    _record_metric(metrics, "sun.sun_civil_ms", step)
```

### 3. Timezone Resolution
**Never send coordinates externally**. Use offline `TimezoneFinder`:
```python
_tzf = TimezoneFinder(in_memory=True)
tz = _tzf.timezone_at(lat=lat, lng=lon)  # Offline lookup
```

### 4. Hourly Elevation Series
Generate 24-hour solar/lunar elevation data for visualization:
```python
series = build_hourly_elevation_series(
    observer, tzinfo, on_date, 
    astral_sun.elevation,  # or moon.elevation
    metrics=profiling, prefix="sun.elevation_series"
)
# Returns: {"2025-10-18T00:00:00+01:00": -12.5, ...}
```

### 5. Moon Illumination Calculation
**Critical**: Don't use Astral's `moon.phase()` directly for illumination - it returns incorrect values. Instead, calculate moon age from next new moon datetime:

```python
def approx_illumination(now: datetime, next_new_moon: date, tz_name: str) -> float:
    """
    Calculate accurate moon illumination using next new moon datetime.
    Age = synodic_days - days_until_next_new_moon
    Illumination = (1 - cos(2π × age / synodic_days)) / 2
    """
    SYNODIC_MONTH_DAYS = 29.530588853
    
    # Convert next new moon to datetime at midnight
    tzinfo = ZoneInfo(tz_name)
    next_new_datetime = datetime.combine(next_new_moon, datetime.min.time()).replace(tzinfo=tzinfo)
    
    # Calculate moon age from next new moon
    days_to_next = (next_new_datetime - now).total_seconds() / 86400.0
    age = SYNODIC_MONTH_DAYS - days_to_next
    age %= SYNODIC_MONTH_DAYS
    
    # Standard cosine illumination formula
    D = 2.0 * pi * (age / SYNODIC_MONTH_DAYS)
    return (1.0 - cos(D)) / 2.0
```

**Why this matters**: 
- Astral's `moon.phase()` returns 25.39 for Oct 18, 2025 → produces 18-28% illumination (wrong)
- Using next new moon (Oct 21) → age 27.03 → produces 6-7% illumination (correct)
- Accuracy: ±2% compared to external astronomy sites (variance due to using midnight vs exact new moon time)

**Frontend visualization**: `updateMoonPhaseVisual()` in `template.html` uses elliptical overlay with `scaleX()` transform to render crescent based on illumination percentage.

## Essential Commands

### Development (uv package manager)
```bash
# Install deps
uv pip install -e .

# Run dev server (auto-reload)
uvicorn app.main:app --reload

# Run with specific host/port
uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
```

### Code Quality (pre-commit configured)
```bash
black .                 # Format code
ruff check .            # Lint
mypy app/              # Type checking
isort .                # Sort imports
pre-commit run --all-files  # Run all hooks
```

### Docker
```bash
docker build -t astro-api .
docker run -p 8000:8000 -e WEATHERAPI_KEY=your_key astro-api
```

## Key API Endpoints

- **`GET /`** - Dashboard HTML (`web/template.html`)
- **`GET /astro?lat={lat}&lon={lon}`** - Astronomical data
  - Optional: `date_str=YYYY-MM-DD`, `tz_override=Europe/London`, `elevation_m=0`
- **`GET /weather?lat={lat}&lon={lon}&days={0-10}`** - Current + forecast weather
- **`GET /health`** - Health check with timestamp
- **`GET /links`** - Personal service links from YAML
- **`GET /feeds`** - Stub for discovery pipeline integration

## Configuration (.env file)

Required:
- `WEATHERAPI_KEY` - WeatherAPI.com key (free tier at https://www.weatherapi.com/)

Optional:
- `APP_NAME` (default: "Astro API")
- `DEBUG` (default: false)
- `ALLOWED_ORIGINS` (default: `["http://localhost:5173", "http://localhost:3000", "*"]`)
- `LINKS_FILE` (default: "app/sample_links.yaml")
- `WEATHERAPI_URL` (default: "http://api.weatherapi.com/v1/")

## Testing Edge Cases

**No pytest suite exists yet.** When adding tests:
- Test polar regions: Tromsø (69.6492, 18.9553) on summer/winter solstice
- Verify `None` handling for missing sun events
- Check timezone resolution with edge coordinates (oceans, poles)
- Validate weather proxy error handling (missing API key, network errors)
- Test interactive API docs at `/docs` (Swagger UI)

## Deployment

**GitHub Actions**: `.github/workflows/docker-publish.yml`
- Builds multi-arch Docker images (amd64, arm64)
- Pushes to `ghcr.io/{owner}/launchpage` on main/tags
- Uses GitHub Actions cache for faster builds

**Production considerations**:
- Set `ALLOWED_ORIGINS` to specific domains (avoid `"*"`)
- Add rate limiting (`slowapi` or reverse proxy)
- Enable structured JSON logging
- Run as non-root user (Dockerfile: `USER 65532:65532`)

## Extension Points

1. **`/feeds` endpoint** - Currently stub; intended for discovery pipeline (SQLite/Redis/JSONL)
2. **Links YAML** - Replace with dynamic config (database, S3, etc.)
3. **Caching** - Consider Redis for shared cache across instances
4. **Monitoring** - Add `prometheus-fastapi-instrumentator` for metrics

## Python Environment Notes

- **Package manager**: `uv` (fast pip alternative)
- **Python version**: 3.12+ required
- **Virtual env**: Managed by `uv` (`.venv/`)
- **Dependencies**: Locked in `uv.lock`, defined in `pyproject.toml`
- **Docker**: Uses system Python (no venv) with `uv pip install --system`

## Important Library Usage

- **Astral**: Sun/moon calculations (use `Observer` for lat/lon/elevation)
  - **Moon phase caveat**: `moon.phase()` returns days since new moon, but this should NOT be used directly for illumination calculations - use the next new moon datetime method instead (see Critical Pattern #5)
- **FastAPI**: Async web framework (note async `httpx` for weather API)
- **Pydantic**: Data validation and settings (use `Query` for parameter validation)
- **timezonefinder**: Offline timezone resolution (load with `in_memory=True`)
- **Math**: Use `cos` and `pi` from `math` module for accurate moon illumination formula

## Conventions

- **Error handling**: Return 400 for invalid input, 503 for external service failures, 500 for internal errors
- **Logging**: Use `logger.debug()` for polar edge cases, `logger.exception()` for unexpected errors
- **Type hints**: Required on all functions (enforced by mypy)
- **Docstrings**: Use for all public functions (Google or rST style)
- **Line length**: 100 characters (configured in ruff/black)
