# Astro API Dashboard

A FastAPI application with an interactive dashboard that provides astronomical and weather data for any location on Earth.

## Features

### Astronomical Data
- **Sun times**: Dawn, sunrise, solar noon, sunset, dusk with civil/nautical/astronomical twilight
- **Golden & blue hours**: Photography-optimized time windows
- **Moon phases**: Current phase, illumination fraction, next new/full moon dates
- **Solar elevation**: Hourly elevation series for the entire day
- **Real-time sun position**: Interactive arc visualization showing current sun position
- **Timezone resolution**: Offline IANA timezone lookup (privacy-friendly, no external calls)

### Weather Data
- **Current conditions**: Temperature, feels-like, humidity, wind, pressure, precipitation, UV index
- **3-day forecast**: Daily high/low temps, conditions, rain/snow chance, wind speeds
- **Auto-refresh**: Weather updates every 15 minutes
- **Location-aware**: Automatically detects browser location or accepts manual coordinates

### Dashboard Features
- **Responsive design**: Works on desktop, tablet, and mobile
- **Real-time updates**: Clock, sun position, and "last updated" timestamps refresh every second
- **24-hour caching**: Smart caching for astronomical data with background refresh
- **Service links**: Customizable quick links from YAML configuration
- **Interactive controls**: Collapsible location picker, manual coordinate entry, date selection

## Screenshot

![Dashboard Screenshot](https://raw.githubusercontent.com/darkflib/launchpage/main/screenshot.png)

## API Endpoints

- `GET /` — Interactive dashboard (HTML)
- `GET /health` — Health check with timestamp
- `GET /astro?lat={lat}&lon={lon}` — Astronomical data
  - Optional: `date_str=YYYY-MM-DD`, `tz_override=Europe/London`, `elevation_m=0`
- `GET /weather?lat={lat}&lon={lon}&days={0-10}` — Weather data with optional forecast
- `GET /links` — Personal service links from YAML
- `GET /feeds` — Stub for discovery pipeline integration

## Quick Start

### Prerequisites
- Python 3.11+
- [uv](https://github.com/astral-sh/uv) package manager
- WeatherAPI.com API key (free tier available at https://www.weatherapi.com/)

### Installation

```bash
# Install dependencies
uv pip install -e .

# Create .env file with your API key
echo "WEATHERAPI_KEY=your_key_here" > .env

# Run development server
uvicorn app.main:app --reload
```

Open http://127.0.0.1:8000 for the dashboard or http://127.0.0.1:8000/docs for API documentation.

## Configuration

Create a `.env` file in the project root:

```env
# Required for weather functionality
WEATHERAPI_KEY=your_api_key_here

# Optional settings
APP_NAME=Astro API
DEBUG=false
ALLOWED_ORIGINS=["http://localhost:5173", "http://localhost:3000", "*"]
LINKS_FILE=app/sample_links.yaml
```

## Architecture

### Backend (FastAPI)
- **`app/main.py`**: Core application with all route handlers and astronomical computations
- **`app/models.py`**: Pydantic models for request/response validation
- **`app/settings.py`**: Configuration via environment variables
- **`app/sample_links.yaml`**: Personal service links configuration

### Frontend (Single Page)
- **`web/template.html`**: Complete dashboard with vanilla JavaScript
- **Real-time updates**: Sun position calculated every second using cached sunrise/sunset times
- **Smart caching**: 24-hour cache for astronomical data, 15-minute refresh for weather
- **Local storage**: Persistent cache survives page reloads

### Key Libraries
- **FastAPI**: Web framework
- **Astral**: Sun/moon calculations
- **timezonefinder**: Offline timezone resolution
- **httpx**: Async HTTP client for weather API
- **Pydantic**: Data validation and settings

---

## Security, privacy, and robustness notes

- **No external calls**: timezone resolution is local (`timezonefinder`), so your coordinates never leave the box.
- **Input validation** with Pydantic and `Query` bounds; explicit 400s on bad input.
- **Polar edge-cases** handled: if Astral cannot compute an event, fields are `null`, not 500.
- **CORS** restrict or widen via `ALLOWED_ORIGINS`. For prod, avoid `"*"` unless you serve public data.
- **Non-root container**, minimal base, `tzdata` installed for correct conversions.
- **Observability**: add structured logging (JSON) if you’re pumping into Loki/ELK; attach Prometheus via `prometheus-fastapi-instrumentator` if desired.

## Customization

### Service Links
Edit `app/sample_links.yaml` to add your personal links:

```yaml
- name: GitHub
  url: https://github.com
  icon: fab fa-github
  group: dev

- name: Gmail
  url: https://gmail.com
  icon: fas fa-envelope
  group: personal
```

### Discovery Feed
The `/feeds` endpoint is a stub for integration with content discovery pipelines:
- SQLite/Redis export reader
- JSONL log tailer
- RSS aggregator
- Custom content sources

## Alternatives / improvements

- **Astronomy accuracy:** for higher fidelity (rise/set at refraction, topocentric correction), consider `skyfield` + `jplephem` (heavier), or `pyephem` (older, fast C but legacy).
- **Sunlight variants:** expose `depression` query param (6°, 12°, 18°) to return civil/nautical/astronomical twilight in one call.
- **Batching:** add `POST /astro/batch` accepting an array of points for map UIs.
- **Rate limiting:** `slowapi` or a reverse proxy (Traefik/Nginx) with IP limiting for public exposure.
- **Schema-first:** publish an OpenAPI client; you can even host a typed SDK for your apps.

## Example API Calls

### Astronomical Data
```bash
# Current location
curl "http://localhost:8000/astro?lat=52.831&lon=-1.285"

# Specific date (Tromsø summer solstice - midnight sun)
curl "http://localhost:8000/astro?lat=69.6492&lon=18.9553&date_str=2025-06-21"

# With timezone override
curl "http://localhost:8000/astro?lat=40.7128&lon=-74.0060&tz_override=America/New_York"
```

### Weather Data
```bash
# Current weather only
curl "http://localhost:8000/weather?lat=40.7128&lon=-74.0060"

# With 3-day forecast
curl "http://localhost:8000/weather?lat=40.7128&lon=-74.0060&days=3"
```

## Deployment

### Docker
```bash
# Build
docker build -t astro-api .

# Run
docker run -p 8000:8000 -e WEATHERAPI_KEY=your_key astro-api
```

### Production Considerations
- Use a reverse proxy (Nginx/Traefik) for SSL termination
- Set `ALLOWED_ORIGINS` to your production domains
- Consider rate limiting with `slowapi` or at the proxy level
- Add monitoring with Prometheus/Grafana
- Enable structured logging for centralized log aggregation

## Edge Cases & Notes

- **Polar regions**: Sun events may be `null` during polar day/night
- **Twilight**: Returns civil (-6°), nautical (-12°), and astronomical (-18°) twilight times
- **Moon illumination**: Heuristic calculation, not precise photometry
- **Privacy**: Timezone resolution is 100% offline - coordinates never leave your server
- **Caching**: Astronomical data cached for 24h, weather for 15min

## Future Enhancements

- [ ] Hourly weather forecast (currently daily only)
- [ ] Air quality index integration
- [ ] Moon rise/set times
- [ ] Batch endpoint for multiple locations (`POST /astro/batch`)
- [ ] Customizable twilight depression angles
- [ ] Astronomy image of the day widget
- [ ] Dark mode toggle
