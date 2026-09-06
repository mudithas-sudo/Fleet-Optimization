# FleetOps — Delivery Management Demo (dev branch)

**This branch is the delivery-management variant**: parcels (name/type/size/
deadline) are the root entity, managed on a Parcels tab (manual add + CSV/Excel
import with client-side parsing and geocoding). A persistent Registry holds
vehicles (type, capacity, max parcel size) and drivers (with per-driver queue
links). Selecting pending parcels opens a driver picker that enforces
size/capacity eligibility; a **delivery run** is planned from a configurable
depot through the parcel destinations by the ADK agent. Parcels flow
pending → assigned → in transit → delivered, ticked off per-stop as the
vehicle's on-route progress passes each destination (late deliveries flagged
against deadlines); manifests in the run detail card show per-stop ETAs with
at-risk badges. Drivers open `driver.html?driver=<id>` to see their queue and
start runs in the navigator. **✨ Auto-assign** runs the ADK dispatch agent:
it inspects pending parcels and fleet availability, verifies candidate batches
against real routes (`evaluate_route`), and proposes parcel-to-driver batching
with per-batch rationale and risk notes; the proposal is validated
server-side (hallucinated ids, double assignments and eligibility violations
are stripped) and executed only on admin approval. `DISPATCH_MODEL` env
selects its model (gemini-3.5-flash recommended).

Built on the fleet-tracking prototype demonstrating
**Google ADK + Google Maps Platform**:

- **Admin dispatch console** (`/static/admin.html`) — click start, intermediate
  and destination stops on a map; a Google ADK agent plans the optimized route
  (Routes API v2 waypoint optimization) with per-leg distances/durations and
  writes a driver briefing; then monitor the trip live with deviation alerts.
- **Driver PWA** (`/static/driver.html?trip=<id>`) — installable web app styled
  like a real navigator: turn-by-turn instruction banner (real Routes API steps
  with maneuver icons), ETA bar with arrival time, speedometer bubble (tap to
  change simulated speed), chevron vehicle marker, traveled route grayed out,
  and a tilted heading-up camera on browsers with WebGL. Driving is
  **simulated** (floating Deviate button veers off-route) — no real GPS needed.
- **Deviation alerts** — rule-based on the backend (distance from the route
  polyline, 80 m threshold, 3-tick debounce, progress-windowed matching so
  routes that double back can't mask a deviation), pushed to the admin over SSE.
- **Fleet dashboard** — the admin monitors every trip at once: Plan/Fleet tabs,
  all active vehicles live on one map, per-trip status, deviation badges,
  planned-vs-driven stats, and a global alert banner.
- **Auto-reroute** — on a confirmed deviation the backend recomputes the route
  from the vehicle's current position through the remaining stops; the driver
  app picks up the new route seamlessly mid-drive and the admin map redraws.
- **Persistence** — trips and driven positions live in SQLite (`fleet.db`),
  so everything survives restarts; delete the file to reset demo data.
- **Live traffic & tolls** — routes use TRAFFIC_AWARE ETAs with the current
  delay called out in the plan and briefing; congested stretches are drawn
  amber/red on both maps; estimated toll cost shows in the trip detail
  (needs a billing-enabled Maps key).

## Architecture

```
Browser (admin.html) ── POST /api/trips/plan ──▶ FastAPI ──▶ ADK Agent (Gemini)
                                                              └─ compute_routes tool ─▶ Google Routes API v2
Browser (driver.html) ── POST position/sec ──▶ FastAPI ─ deviation check ─▶ SSE ──▶ admin live map + alerts
```

The agent is invoked programmatically (Runner + InMemorySessionService), not as
a chatbot. The Routes tool stashes trusted geometry in session state; the LLM
contributes the validated stop ordering and the briefing text — a bad generation
can never corrupt the map data.

## Run it

```bash
cp .env.example .env        # put your keys in .env
python3.11 -m venv venv
venv/bin/pip install -r requirements.txt
./run.sh                    # http://localhost:8000
```

Keys in `.env`:

- `GOOGLE_API_KEY` — Gemini (AI Studio) key for the ADK agent. Free tier is fine.
- `MAPS_API_KEY` — Google Maps Platform key. Maps JS works without billing
  (watermarked); **Routes API v2 requires billing**. Until then set
  `FAKE_ROUTES=1` — routing is replaced by straight lines with estimated
  distances so the whole demo still works. Set `FAKE_ROUTES=0` once the billed
  key is in place to get road-following, order-optimized routes.

## Deploy

FleetOps is **one long-lived process** — FastAPI serving REST + Server-Sent
Events + the static frontend, with SQLite on local disk and live counters in
memory. That rules out serverless/edge hosts like **Vercel** (read-only
filesystem, no long-lived connections, fresh process per request — the live
fleet map, deviation alerts and auto-reroute would all break). Use a container host.

### Render — free, no credit card

[`render.yaml`](render.yaml) is a blueprint: Render dashboard → **New →
Blueprint** → connect this repo → set `GOOGLE_API_KEY` and `MAPS_API_KEY` when
prompted → **Apply**. First build ~4 min; auto-deploys on every push to `main`.

Free-plan tradeoffs: the service sleeps after 15 min idle (~40 s cold start)
and has no persistent disk, so `fleet.db` resets on redeploy — same as
`rm fleet.db*` locally. Uncomment the `disk:` block in `render.yaml` for
durable storage (paid). Restrict `MAPS_API_KEY` to the `*.onrender.com` HTTP
referrer once you have the URL.

### Railway / Fly.io / a VM

Build the `Dockerfile`; provide `FAKE_ROUTES`, `GOOGLE_API_KEY`, `MAPS_API_KEY`,
and optionally `DB_PATH` pointing at a mounted volume. The container listens on
`$PORT` (default 8000).

## Demo script

1. Open `http://localhost:8000` (admin). Add 4–5 stops in a deliberately
   inefficient order — type a place name in the search box and press Enter, or
   click the map (clicked stops are auto-named via Places Nearby Search).
2. **Plan route** → agent returns the optimized order, legs table, totals and a
   driver briefing.
3. Open the driver link (second window or phone on the LAN; installable as a
   PWA on localhost/HTTPS). Press **Start** — the navigator view takes over:
   turn banner counts down to each maneuver, ETA bar shows arrival time, and
   the admin map tracks the vehicle live.
4. Tap the **⚠ button** — the vehicle veers off; the driver banner turns red
   ("Off route") and the admin gets a deviation alert within a few seconds.
5. Tap **⚠** again — vehicle returns, "back on route" alert fires, and the
   trip completes on arrival.

## Notes

- In-memory storage only; restarting the server clears trips.
- `DEVIATION_THRESHOLD_M` / `DEVIATION_CONSECUTIVE` tune alert sensitivity.
- Model is env-switchable via `MODEL` (default `gemini-3.5-flash-lite`).
