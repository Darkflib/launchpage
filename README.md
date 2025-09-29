# Astro API

A small FastAPI app that, given latitude/longitude, returns:
- Timezone (IANA) at that location,
- “Now” in that timezone,
- Sun events (dawn, sunrise, solar noon, sunset, dusk), day length, and whether it’s daylight right now,
- Moon phase (0–29), a phase name, and a heuristic illumination fraction.

## Endpoints

- `GET /health`
- `GET /links` — returns YAML-defined personal service links
- `GET /astro?lat=51.5&lon=-0.13` — optional `date_str=YYYY-MM-DD`, `tz_override=Europe/London`, `elevation_m=0`
- `GET /feeds` — stub for your discovery pipeline

## Run locally

```bash
uv pip install -e .
uvicorn app.main:app --reload
```

Open http://127.0.0.1:8000/docs
 for Swagger UI.

## Notes

Astral’s default dawn/dusk are civil twilight (~ -6°). For nautical/astronomical variants, we can add parameters later.

Polar day/night: certain events may be null.

TZ resolution uses timezonefinder offline; no external calls, and privacy-friendly.

---

## Security, privacy, and robustness notes

- **No external calls**: timezone resolution is local (`timezonefinder`), so your coordinates never leave the box.
- **Input validation** with Pydantic and `Query` bounds; explicit 400s on bad input.
- **Polar edge-cases** handled: if Astral cannot compute an event, fields are `null`, not 500.
- **CORS** restrict or widen via `ALLOWED_ORIGINS`. For prod, avoid `"*"` unless you serve public data.
- **Non-root container**, minimal base, `tzdata` installed for correct conversions.
- **Observability**: add structured logging (JSON) if you’re pumping into Loki/ELK; attach Prometheus via `prometheus-fastapi-instrumentator` if desired.

## Extending for your feeds and links

- Replace `/feeds` with a reader over your discovery pipeline’s SQLite/Redis export, or a JSONL tailer.
- Swap `app/sample_links.yaml` for a config managed in Git (GitOps), or load from S3/GCS with short TTL caching.

## Alternatives / improvements

- **Astronomy accuracy:** for higher fidelity (rise/set at refraction, topocentric correction), consider `skyfield` + `jplephem` (heavier), or `pyephem` (older, fast C but legacy).
- **Sunlight variants:** expose `depression` query param (6°, 12°, 18°) to return civil/nautical/astronomical twilight in one call.
- **Batching:** add `POST /astro/batch` accepting an array of points for map UIs.
- **Rate limiting:** `slowapi` or a reverse proxy (Traefik/Nginx) with IP limiting for public exposure.
- **Schema-first:** publish an OpenAPI client; you can even host a typed SDK for your apps.

## Example calls

- `GET /astro?lat=52.831&lon=-1.285` (Kegworth-ish)
- `GET /astro?lat=69.6492&lon=18.9553&date_str=2025-06-21` (Tromsø solstice; expect 24h daylight and some nulls)

---

## Confidence

- Correctness of plumbing and API structure: **0.94**
- Astral phase naming and illumination heuristic (vs precise photometry): **0.85**
- TZ resolution accuracy (IANA ID coverage): **0.92**

If you want, I can add: `depression` parameter for twilight flavours, `/astro/batch`, a tiny in-memory cache, and a Prometheus `/metrics` endpoint.
