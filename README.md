# FleetOps — Delivery Management Demo (dev branch)

**This branch is the delivery-management variant**: parcels (name/type/size/
deadline) are the root entity, managed on a Parcels tab (manual add + CSV/Excel
import with client-side parsing and geocoding). A persistent Registry holds
vehicles (type, capacity, max parcel size) and drivers (with per-driver queue
links). Selecting pending parcels opens a driver picker that enforces
size/capacity eligibility; a **delivery run** is planned from a configurable
hub through the parcel destinations by the ADK agent. Parcels flow
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

### Daily-operations features

- **Manifest import** — CSV/Excel with tracking number, receiver name/phone,
  destination address (geocoded), size or package dimensions (cm), weight,
  fragile, notes and deadline. Column headers are matched by common aliases;
  duplicate tracking numbers are rejected; size is derived from dimensions when
  blank. Blank tracking numbers on manual adds are generated (`FO-XXXXXXXX`).
- **Capacity** — vehicles carry a cargo space (L×W×H cm) and payload (kg) on top
  of parcel count and max size class; parcels carry optional dimensions and
  weight. Eligibility checks fit (any orientation), total weight and total
  volume (85% packing efficiency). Parcels no vehicle can carry are flagged
  *unassigned* with the reason.
- **Suggest runs** — deterministic and key-free: parcels are swept by bearing
  around the hub and packed onto the largest free vehicles first, so the fewest
  vehicles are used; each run is then routed from the hub. **AI plan** still
  calls the Gemini dispatch agent (`engine: "ai"`).
- **Run sheet** — `static/runsheet.html?trip=<id>` (linked from the run detail
  card) is a printable back-office sheet: driver, vehicle, ordered stops with
  tracking numbers, receivers, addresses, package details, ETAs and status.

- **Dispatcher overview** — a today strip (pending / out now / delivered /
  late / on-time %), parcel search and driver/date filters, and an Attention
  tab listing late, at-risk, off-route, failed and un-carriable items by urgency.
- **Proof of delivery** — the driver's **My stops** sheet (list button in the
  navigator) confirms a drop with who received it, a photo and/or the **receipt
  reference** from the existing e-signature process (signatures are captured
  there, not in this app), or "left in a safe place". Evidence is stored apart from
  the parcel so lists stay light; dispatch can also record a delivery by hand.
- **Pickup requests** — customers ask for a collection during the day. A pickup
  request is a parcel with the customer's address, their **cut-off time** and a
  collect-by time; it is collected and brought back to the **hub** (the last stop
  of the run), on the same vehicle and route as the deliveries. Pickups follow the
  customer's cut-off: before it they wait behind ready stops, unless dispatch records
  that the shipment is ready *and* the customer confirmed early collection. The
  parcel detail suggests the best run (extra minutes, arrival time, cut-off and
  vehicle limits, deliveries it would push late); "Add to run" re-plans a live
  run from the vehicle's position. Drivers tick **Collected** themselves — driving
  past never collects a pickup. All last-mile work starts at the local hub.
- **Failed deliveries and pickups** — the driver picks a reason (nobody home, wrong
  address, refused, damaged, no access; for pickups: not ready, customer
  unavailable, cancelled…). The shipment stops in **Failed — needs review**; nothing
  is rescheduled or returned automatically. A dispatcher reviews it, then
  reschedules it (typically for the next day if the customer asks, with an
  optional corrected address — a later day keeps it out of today's runs) or
  returns/cancels it. A third failure only flags it as a candidate for return.
- **Parcel timeline** — every parcel keeps a history (created, assigned, out for
  delivery, collected, failed, rescheduled, proof recorded, delivered); open it
  by clicking a parcel in the admin list.

- **Dashboard and ride time-travel** — the console opens on a dashboard (profile,
  greeting, four KPI cards, live map, rides table). The **time bar** shows every
  ride as it was at any moment of any day: drag the slider or pick a date and the
  table (status, parcels delivered, next stop, a day timeline), the vehicle
  markers and the selected run's panel all go back to that time; **Live** returns.
  Extra detail lives in accordions (stops, briefing, alerts, customer updates).
- **Dispatcher workflow** (from [docs/ui-research.md](docs/ui-research.md)) — an unassigned queue
  and "needs attention" list beside the live board, drag a parcel onto a ride, per-ride load
  meter and overdue/at-risk badges, a sortable rides table, a command palette (Ctrl/Cmd+K) with
  single-key shortcuts, compact view, and a first-run checklist.
- **Priorities and time windows** — parcels can be Standard, Priority or
  Express and can have a "not before" delivery time. Runs that contain any of
  these are sequenced deterministically (express first, windows respected, pickups
  before their drops) and "Suggest runs" gives them vehicle space first.
- **Driver shifts and limits** — shift hours, on-leave, max stops and max hours
  per run (plus a break allowance). Enforced when suggesting runs, in the driver
  picker and when a run is created.
- **Dispatcher edits** — add parcels to a planned or in-progress run, take them
  off, move a parcel between runs (or drag it onto a run), and hand a planned
  run to another driver. Every edit re-routes without the AI planner; a run under
  way is re-planned from the vehicle's current position.
- **Customer tracking** — `static/track.html?no=<tracking number>` shows status,
  ETA, stops before yours and history, without phone numbers, notes or driver
  surnames. SMS/email messages (out for delivery, nearby, delivered, failed,
  rescheduled, returned) are always recorded; set `NOTIFY_WEBHOOK_URL` to send
  them through your provider.
- **Reports** — header **Reports** button: on-time and first-attempt rates,
  failures by reason, per-driver productivity, distance, cost per delivery
  (editable per-km / per-hour rates) and CSV exports for parcels, runs, drivers.
- **Users and roles** — set `AUTH_ENABLED=1` to require sign-in. Roles: viewer
  (read), dispatcher (plan and change deliveries), supervisor (+ fleet, hub,
  reports), admin (+ people). Driver links carry a secret key (`&k=…`), so the
  driver app needs no login but guessed ids can't mark deliveries.

### Demo data

One command starts the console on a separate, fully populated demo database (your
normal `fleet.db` is untouched) at http://localhost:8001:

```powershell
.\samples\start-demo.bat            # add -Users for demo staff accounts
# (or: powershell -ExecutionPolicy Bypass -File .\samples\start-demo.ps1)
```

It loads a working day relative to *now*: 4 runs (2 finished earlier today, 1 live,
1 scheduled), 38 parcels (on-time and late deliveries, a failed delivery waiting
for review, one rescheduled for tomorrow after review, one returned to the sender,
express / priority / window parcels, collect-and-deliver, one no vehicle can carry,
three customer pickup requests that arrived during the day, eight ready-to-assign pending parcels), receipt references and
photos, customer messages, and vehicle position trails you can replay with the
dashboard time bar. Re-run it before a demo so the times are fresh. Seeding makes
no API calls. To load the same data into your normal database instead, run
`venv\Scripts\python samples\seed_demo.py --reset` (this replaces your parcels,
fleet and runs). Try the tracking page with `FO-100112` or `FO-100114`.

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
