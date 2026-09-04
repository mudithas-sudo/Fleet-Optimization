# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

FleetOps — a demo prototype showcasing Google ADK + Google Maps Platform. An admin plans multi-stop fleet routes on a map (an ADK agent does the planning), a driver PWA simulates driving them navigator-style, and the backend raises deviation alerts and auto-reroutes. See README.md for the feature list and demo script.

## Commands

```bash
python3.11 -m venv venv && venv/bin/pip install -r requirements.txt   # once
cp .env.example .env                                                   # fill in keys
./run.sh                          # serve at http://localhost:8000 (admin UI at /)
rm fleet.db*                      # reset all demo data (trips, position log)
```

There is no test suite, linter, or frontend build step. Verification is done by exercising the running app: `curl` against the API (plan a trip, POST positions, watch for alerts/reroute) and Playwright driving the real pages headless (import it from a sibling project, e.g. `/hms/apps/test-automation/node_modules/playwright/index.mjs`; `google-chrome --headless=new --screenshot` works for single screenshots). Restart the server after backend edits — `run.sh` does not auto-reload.

## Environment / keys

All config is env vars loaded from `.env` (see `.env.example`). Two Google keys: `GOOGLE_API_KEY` (Gemini, for the ADK agent) and `MAPS_API_KEY` (Maps JS + Routes API v2 + Places API New). Key quirks that shape the code:

- On an unbilled Maps key, Routes v2 and Places API (New) work but **legacy** Geocoding/Places do not — which is why place search/reverse-naming go through backend proxies (`/api/geocode`, `/api/revgeocode`) hitting Places New, and why toll estimates stay empty until a billed key is used.
- `FAKE_ROUTES=1` swaps the Routes API for straight-line routes so the entire pipeline (agent, simulator, deviation, reroute, SSE) works with no usable Maps key.

## Architecture

Single FastAPI app (`app/main.py`) serves the REST API, SSE streams, and the static frontend (same origin, no CORS, no auth). Storage is SQLite (`app/db.py`: trip JSON blobs + a per-tick position log) with runtime-only state (deviation counters, reroute guards, SSE queues) held in memory in `app/store.py`.

**Route planning is agent-mediated; rerouting is not.** `app/routing.py` owns the actual Routes API v2 call (field mask, waypoint optimization, traffic/toll extras, FAKE_ROUTES fallback) and returns the canonical route dict (`orderedStops/polyline/path/legs/steps/traffic/tollPrice/totals`). Two callers:

- Planning: `app/planner/service.py` runs the ADK `Agent` (module-level `Runner` + `InMemorySessionService`, fresh session per request, plain `await` — never `asyncio.run()` in handlers). The agent calls the `compute_routes` tool (`planner/tools.py`), which wraps `routing.compute_route`.
- Auto-reroute (`_do_reroute` in `main.py`): calls `routing.compute_route` directly with `optimize=False` — deterministic, no LLM in the loop.

**Trusted data never round-trips through the LLM.** The tool stashes the full route in `tool_context.state["routes_api_raw"]` and returns only a small summary dict to the model (no polylines — token waste). The service reads geometry from session state and takes only the validated `output_schema` result (stop order + briefing) from the model; a bad generation can break the briefing, never the map. Deterministic inputs like avoid-tolls flow through session `state=` at `create_session`, not through the prompt.

**Deviation detection** (`app/deviation.py`) is rule-based: point-to-polyline distance with a threshold + consecutive-tick debounce. Matching is **windowed around the vehicle's known progress** (`runtime["along"]`, frozen while off-route, ahead-window bounded by physically plausible movement per tick). Do not "simplify" this back to min-distance-over-the-whole-polyline: routes that loop or double back (constant in Colombo) pass within meters of an off-route vehicle and mask real deviations.

**Reroute protocol**: a confirmed deviation kicks off a background task that recomputes from the current position through the remaining stops (found via leg-distance boundaries vs `along`), bumps `trip["routeVersion"]`, and broadcasts. The driver doesn't hold an SSE connection — it notices the version bump in its next `POST /position` response and refetches the trip mid-drive.

**Live updates are SSE, not WebSockets.** `store.broadcast(trip_id, event, data)` fans out to per-trip subscribers (`/api/trips/{id}/stream`) and a global stream (`/api/stream`, used by the admin fleet dashboard); every event carries `tripId`.

## Frontend

Plain HTML/JS in `static/`, no build. `admin.html`/`admin.js` is the dispatch console (Plan/Fleet tabs, global SSE, all vehicles on one map); `driver.html`/`driver.js` is the navigator-style PWA whose "driving" is a client-side simulator interpolating along the decoded polyline (the Deviate button applies a growing perpendicular offset). Shared helpers live in `static/js/common.js` (API fetch, polyline decode, markers, traffic overlays, theming) and `maps-loader.js` (fetches the key from `/api/config`, official bootstrap loader).

Conventions: light theme is default with dark as a toggle — all colors are CSS custom properties on `:root` overridden under `[data-theme="dark"]`, and map style/route colors follow via `themeAwareMap`/`mapThemeStyles()`. The driver map uses `mapId: "DEMO_MAP_ID"` (vector: tilt/heading camera + AdvancedMarker, falls back gracefully); the admin map uses a classic styled raster map, so `styles:` works there but not on the driver map. Bump the `CACHE` version in `static/sw.js` when changing driver-page shell files, or the PWA serves stale ones.

## Routes API gotchas (learned the hard way)

- The `X-Goog-FieldMask` header is mandatory; omitting it is a 400.
- `optimizeWaypointOrder` needs ≥2 intermediates and is incompatible with `TRAFFIC_AWARE_OPTIMAL` (plain `TRAFFIC_AWARE` is fine).
- Durations arrive as strings like `"3600s"`; route-level `speedReadingIntervals` indices map directly onto the overview polyline points.
- The router optimizes **time, not distance** — a 25 km expressway loop legitimately beats a 13 km urban crawl. If a route "looks wrong", compare alternatives before assuming a bug (`avoidTolls` usually flips it).

## Git

Branch `basic` pins the baseline demo (single-trip, no persistence/reroute/dashboard/traffic — and the naive pre-window deviation check); ongoing work happens on `master`. Keep `basic` demo-ready; don't merge master into it casually.
