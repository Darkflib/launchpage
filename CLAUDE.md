# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

**Astro API** is a FastAPI application that provides astronomical data (sun/moon times, twilight periods, timezone resolution) based on latitude/longitude coordinates. It includes a single-page HTML dashboard served from `/web/template.html`.

## Core Commands

### Development
```bash
# Install dependencies (uses uv package manager)
uv pip install -e .

# Run development server with auto-reload
uvicorn app.main:app --reload

# Run on specific host/port
uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
```

### Code Quality
```bash
# Format code
black .

# Lint with ruff
ruff check .

# Type checking
mypy app/

# Sort imports
isort .
```

### Docker
```bash
# Build container
docker build -t astro-api .

# Run container
docker run -p 8000:8000 astro-api
```

## Architecture

### Application Structure

- **`app/main.py`**: Core FastAPI application with all route handlers and astronomical computation logic
- **`app/models.py`**: Pydantic models for request/response validation (`AstroQuery`, `AstroResponse`, `SunTimes`, `MoonInfo`, `LinkItem`)
- **`app/settings.py`**: Configuration via Pydantic Settings, loads from `.env`
- **`web/template.html`**: Single-page dashboard HTML served at root `/`

### Key Design Patterns

1. **Privacy-first**: Timezone resolution uses `timezonefinder` library (offline, in-memory) - coordinates never leave the server
2. **Graceful degradation**: Polar regions where sun events don't occur return `null` fields instead of raising errors
3. **Performance profiling**: Optional `profiling_ms` dict in responses tracks computation time for each astronomical calculation
4. **CORS configuration**: Controlled via `ALLOWED_ORIGINS` in settings for production security

### Astronomical Computations

All astronomy calculations use the **Astral** library:

- **Sun events**: Computed in `compute_sun_times()` (lines 175-312 in main.py)
  - Returns civil/nautical/astronomical twilight times (dawn/dusk at -6°, -12°, -18° depression)
  - Includes blue hour and golden hour periods (using `astral.sun.blue_hour`, `astral.sun.golden_hour`)
  - Generates hourly solar elevation series for the full day

- **Moon data**: Computed in `compute_moon()` (lines 315-353)
  - Phase day (0-29) mapped to readable names via `moon_phase_name()`
  - Illumination fraction uses simple heuristic `approx_illumination()` (not precise photometry)
  - Generates hourly lunar elevation series

- **Timezone resolution**: `resolve_timezone()` (lines 76-91) uses TimezoneFinder with fallback to UTC

### Response Models

- **`AstroResponse`**: Main response containing `query`, `timezone`, `now_local`, `sun` (SunTimes), `moon` (MoonInfo), optional `profiling_ms`
- **`SunTimes`**: Comprehensive sun data including all twilight variants, day length, current daylight status, and hourly elevation series
- **`MoonInfo`**: Phase day, phase name, estimated illumination, and hourly elevation series
- **`TimePeriod`**: Simple start/end wrapper for blue/golden hours

## API Endpoints

- **`GET /`**: Dashboard HTML (served from `web/template.html`)
- **`GET /health`**: Health check returning status and UTC timestamp
- **`GET /astro`**: Core astronomical data endpoint
  - Required: `lat`, `lon`
  - Optional: `date_str` (YYYY-MM-DD), `tz_override`, `elevation_m`
- **`GET /links`**: Personal service links loaded from `app/sample_links.yaml`
- **`GET /feeds`**: Stub endpoint for future discovery pipeline integration

## Configuration

Environment variables (`.env` file):
- `APP_NAME`: Application name (default: "Astro API")
- `DEBUG`: Enable debug logging (default: false)
- `ALLOWED_ORIGINS`: CORS allowed origins (default includes localhost:5173, localhost:3000, and "*")
- `LINKS_FILE`: Path to YAML file with personal links (default: "app/sample_links.yaml")

## Dependencies

Key libraries:
- **FastAPI**: Web framework
- **Astral**: Astronomical calculations (sun/moon)
- **timezonefinder**: Offline IANA timezone resolution from coordinates
- **Pydantic**: Data validation and settings management
- **uvicorn**: ASGI server

## Testing Notes

- Interactive API docs available at `/docs` (Swagger UI) when server is running
- Test polar edge cases: Tromsø (69.6492, 18.9553) on summer/winter solstice dates
- Default response includes performance profiling metrics for optimization

## Extension Points

- **`/feeds` endpoint**: Currently a stub; intended for discovery pipeline integration (SQLite/Redis/JSONL)
- **Links YAML**: Replace `app/sample_links.yaml` with dynamic config (S3/GCS, database, etc.)
- **Caching**: No caching currently implemented; consider adding for repeated queries
- **Rate limiting**: Not implemented; use `slowapi` or reverse proxy for production