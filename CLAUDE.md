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

Tests (added with the pickup & delivery feature): `venv/bin/python tests/test_pickup_delivery.py` — deterministic routing/progress checks, no keys (uses `FAKE_ROUTES`). `eval/` holds ADK `AgentEvaluator` eval sets for the LLM agents (`eval/run.py`, needs a Gemini key — see `eval/README.md`). No linter or frontend build step. Broader verification is by exercising the running app: `curl` against the API and Playwright driving the real pages headless (import from a sibling project, e.g. `/hms/apps/test-automation/node_modules/playwright/index.mjs`; `google-chrome --headless=new --screenshot` for single shots). Restart the server after backend edits — `run.sh` does not auto-reload.

## Environment / keys

All config is env vars loaded from `.env` (see `.env.example`). Two Google keys: `GOOGLE_API_KEY` (Gemini, for the ADK agent) and `MAPS_API_KEY` (Maps JS + Routes API v2 + Places API New). Key quirks that shape the code:

- On an unbilled Maps key, Routes v2 and Places API (New) work but **legacy** Geocoding/Places do not — which is why place search/reverse-naming go through backend proxies (`/api/geocode`, `/api/revgeocode`) hitting Places New, and why toll estimates stay empty until a billed key is used.
- `FAKE_ROUTES=1` swaps the Routes API for straight-line routes so the entire pipeline (agent, simulator, deviation, reroute, SSE) works with no usable Maps key.

## Architecture

Single FastAPI app (`app/main.py`) serves the REST API, SSE streams, and the static frontend (same origin, no CORS, no auth). Storage is SQLite (`app/db.py`: trip JSON blobs + a per-tick position log) with runtime-only state (deviation counters, reroute guards, SSE queues) held in memory in `app/store.py`.

**Route planning is agent-mediated; rerouting is not.** `app/routing.py` owns the actual Routes API v2 call (field mask, waypoint optimization, traffic/toll extras, FAKE_ROUTES fallback) and returns the canonical route dict (`orderedStops/polyline/path/legs/steps/traffic/tollPrice/totals`). Two callers:

- Planning: `app/planner/service.py` runs the ADK `Agent` via `AgentRun` (`app/agent_runtime.py` — the shared `Runner` + `InMemorySessionService` wrapper both agents use; fresh session per request, plain `await`, never `asyncio.run()` in handlers). The agent calls the `compute_routes` tool (`planner/tools.py`), which wraps `routing.compute_route`. Prefer ADK components over hand-rolled equivalents: both agents use `PlanReActPlanner` (reason before acting), `output_schema` (Pydantic structured output) + `output_key`, and expose `root_agent` for `adk run`/`web`/`eval`.
- Auto-reroute (`_do_reroute` in `main.py`): calls `routing.compute_route` directly with `optimize=False` — deterministic, no LLM in the loop.

**Trusted data never round-trips through the LLM.** The tool stashes the full route in `tool_context.state["routes_api_raw"]` and returns only a small summary dict to the model (no polylines — token waste). The service reads geometry from session state and takes only the validated `output_schema` result (stop order + briefing) from the model; a bad generation can break the briefing, never the map. Deterministic inputs like avoid-tolls flow through session `state=` at `create_session`, not through the prompt.

**Deviation detection** (`app/deviation.py`) is rule-based: point-to-polyline distance with a threshold + consecutive-tick debounce. Matching is **windowed around the vehicle's known progress** (`runtime["along"]`, frozen while off-route, ahead-window bounded by physically plausible movement per tick). Do not "simplify" this back to min-distance-over-the-whole-polyline: routes that loop or double back (constant in Colombo) pass within meters of an off-route vehicle and mask real deviations.

**Reroute protocol**: a confirmed deviation kicks off a background task that recomputes from the current position through the remaining stops (found via leg-distance boundaries vs `along`), bumps `trip["routeVersion"]`, and broadcasts. The driver doesn't hold an SSE connection — it notices the version bump in its next `POST /position` response and refetches the trip mid-drive.

**Live updates are SSE, not WebSockets.** `store.broadcast(trip_id, event, data)` fans out to per-trip subscribers (`/api/trips/{id}/stream`) and a global stream (`/api/stream`, used by the admin fleet dashboard); every event carries `tripId`.

## Frontend

Plain HTML/JS in `static/`, no build. `admin.html`/`admin.js` is the dispatch console (Plan/Fleet tabs, global SSE, all vehicles on one map); `driver.html`/`driver.js` is the navigator-style PWA whose "driving" is a client-side simulator interpolating along the decoded polyline (the Deviate button applies a growing perpendicular offset). Shared helpers live in `static/js/common.js` (API fetch, polyline decode, markers, traffic overlays, theming) and `maps-loader.js` (fetches the key from `/api/config`, official bootstrap loader).

Conventions: light theme is default with dark as a toggle — all colors are CSS custom properties on `:root` overridden under `[data-theme="dark"]`, and map style/route colors follow via `themeAwareMap`/`mapThemeStyles()`. The driver map uses `mapId: "DEMO_MAP_ID"` (vector: tilt/heading camera + AdvancedMarker, falls back gracefully); the admin map uses a classic styled raster map, so `styles:` works there but not on the driver map. Bump the `CACHE` version in `static/sw.js` when changing driver-page shell files, or the PWA serves stale ones.

Design system: slate header + white sidebar + light stage, deep-navy actions, **Inter** — measured from the reference dashboard the client supplied (header `#364152`, page `#f3f5f7`, white cards, navy `#1a2234` for the active nav pill and primary buttons, route blue `#4f7fd6`). Tokens live in the `<style>` block of `admin.html` (source of truth, light + dark); the shell and components are in `static/css/console.css` (loaded after). Layout: `#app` grid = top bar / hero band (profile, greeting, 4 KPI cards, dashboard only) / sidebar / `#stage`. `body[data-page]` (dashboard | parcels | fleet | attention | registry) switches the stage grid via CSS; `switchTab()` is the only page switcher. The map (`#map-wrap`) and the floating cards (`#parcel-detail`, `#trip-detail`, `#dispatch-card`, `#run-picker`) are direct children of `#stage`; on the dashboard `#trip-detail` docks in the right column. Extra information goes in accordions (`.acc` + `.acc-head`, `accHtml()` in `common.js`, one delegated click handler). The dashboard's "rides at a time" board is `static/js/dashboard.js` over `GET /api/rides?at=` (`app/rides.py`: state per run at any moment from started/ended times, alert log, position log and parcel history). The dashboard dock (`#dock-empty`, rendered by `renderDock()`) holds "Needs attention" + the unassigned queue (draggable onto `.ride-row` drop targets) or a first-run checklist; `palette.js` is the Ctrl/Cmd+K palette, shortcuts and compact-view toggle; the research behind these choices is in `docs/ui-research.md`. Sentence case, no all-caps labels, no emoji — icons come from `ICONS`/`iconSvg()`. The older `design-system/fleetops/` docs describe an earlier look.

## Dispatcher features (added on the Delivery-Management branch)

- `app/delivery.py` owns parcels/vehicles/drivers: eligibility (`check_eligibility`: count, size, dimensions, weight, volume, then driver availability/limits), parcel history (`add_event`, centralised in `set_parcel_status`), proof of delivery (images in the `pods` table, never in the parcel JSON) and failed attempts (3 strikes or a refusal -> `returned`).
- `app/dispatcher/rules.py` is the deterministic, key-free "Suggest runs" (sweep + largest vehicle first, express first, skips drivers on leave/off shift). The Gemini agent is still available as `engine: "ai"`.
- Runs with express/priority parcels or windows bypass the AI planner: `routing.sequence_stops` + `delivery.plan_with_priorities`. `app/replan.py` edits existing runs (add/remove/move/reassign) via `routing.compute_route`; a live run re-plans from `trip["lastPosition"]` and bumps `routeVersion`.
- `app/notify.py` records customer messages and posts to `NOTIFY_WEBHOOK_URL` if set; public tracking is `GET /api/track/{no}` (rate limited, no PII beyond what the customer should see).
- `app/auth.py`: opt-in (`AUTH_ENABLED=1`) cookie sessions + roles enforced in one middleware (`required_role`). Driver endpoints (`DRIVER_ROUTES`) accept the driver's `k` key instead of a session — keep new driver-app endpoints in that list. Never return `token` fields to roles below dispatcher.
- `app/reports.py`: reporting + CSV (cells starting `= + - @` are neutralised).
- Tests are plain scripts, no keys: `tests/test_{pickup_delivery,capacity_dispatch,pod_failures,priority_shifts,replan,tracking_notify,reports,auth}.py`. Each sets its own temp `DB_PATH`.

## Routes API gotchas (learned the hard way)

- The `X-Goog-FieldMask` header is mandatory; omitting it is a 400.
- `optimizeWaypointOrder` needs ≥2 intermediates and is incompatible with `TRAFFIC_AWARE_OPTIMAL` (plain `TRAFFIC_AWARE` is fine).
- Durations arrive as strings like `"3600s"`; route-level `speedReadingIntervals` indices map directly onto the overview polyline points.
- The router optimizes **time, not distance** — a 25 km expressway loop legitimately beats a 13 km urban crawl. If a route "looks wrong", compare alternatives before assuming a bug (`avoidTolls` usually flips it).

## Git

Branch `basic` pins the baseline demo (single-trip, no persistence/reroute/dashboard/traffic — and the naive pre-window deviation check); ongoing work happens on `master`. Keep `basic` demo-ready; don't merge master into it casually.
