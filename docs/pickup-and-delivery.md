# Design — pickup points (Pickup & Delivery)

**Status:** proposal, awaiting review. Nothing implemented yet.

## Problem

Today every parcel has a single `destination` and is implicitly loaded at the
depot. Runs are `depot → drop → drop → …`. We need parcels that must be
**collected somewhere other than the depot** first, then delivered — mixed on
the same run with ordinary depot-origin parcels and with several parcels going
to the same destination.

This is the classic **Pickup & Delivery Problem**: each pickup parcel adds a
second stop, and its pickup must be visited **before** its delivery.

## Decisions (settled with the user)

1. **Aborted run with collected parcels** → a real `awaiting_redelivery`
   status. The parcel is assumed returned to the depot; re-dispatch treats it
   as a normal depot-origin delivery (its original `pickup` is kept for
   history but skipped).
2. **Pickup time windows** are supported: an optional `[earliest, latest]` on
   the pickup. Treated as a **soft constraint** like delivery deadlines —
   the planner sequences to hit it and the manifest flags "at risk" if the
   ETA falls outside it; never a hard reject.
3. **Progress stays automatic** — collected/delivered are marked as the
   vehicle passes each stop, no driver confirm buttons at this stage.

## Non-goals (v1)

Peak/partial-load capacity modelling; standalone pickup-only or delivery-only
jobs; a hard time-window scheduler; barcode/signature capture (that lands with
the separate proof-of-delivery feature).

---

## Data model

Parcels are JSON blobs in SQLite — no migration, just new optional fields.

```
parcel.pickup       : {lat, lng, label, earliest?: float, latest?: float} | null
                      # null ⇒ loaded at the depot (today's behaviour)
                      # earliest/latest ⇒ optional collection time window (epoch secs)
parcel.pickedUpAt   : float | null               # timestamp, mirrors deliveredAt
parcel.status       : pending | assigned | in_transit | picked_up | delivered
                                                 | awaiting_redelivery
                      # picked_up only when pickup != null
                      # awaiting_redelivery: was collected, run aborted, now back at the depot
```

Stop dicts (in `route.orderedStops`, built by `delivery.build_run_stops`):

```
stop.kind      : "depot" | "pickup" | "delivery"   # new; replaces the bare `depot: true` flag
stop.parcelId  : str                               # a parcel's pickup + delivery stops share it
```

Back-compat: a stop with no `kind` is read as `depot` if it has `depot: true`,
else `delivery`. Parcels with no `pickup` key are treated as `null`.

---

## Route building & ordering

`delivery.build_run_stops(parcels)` becomes:

```
[{**depot, kind: "depot"}]
for p in parcels:
    if p.pickup and p.status != "awaiting_redelivery":
        append {**p.pickup,     label: "Collect · <name>", parcelId: p.id, kind: "pickup"}
    append     {**p.destination, label: "<name>",          parcelId: p.id, kind: "delivery"}
```

(An `awaiting_redelivery` parcel is back at the depot, so it gets no pickup
stop — just a delivery, like an ordinary depot-origin parcel.)

Coincident stops (same lat/lng, e.g. 5 parcels to one address, or 2 collected
at one warehouse) are **deduped into one stop carrying a `parcelIds` list**.

### The ordering constraint

Google Routes `optimizeWaypointOrder` has **no precedence support** — it can
place a delivery before its pickup. So when any stop is a pickup,
`routing.compute_route` must **not** use `optimizeWaypointOrder`; the order is
decided before the Routes call and passed with `optimize=False`.

**v1 — deterministic order (in `routing`/`delivery`):**
nearest-neighbour from the depot, with the rule *never visit a delivery whose
pickup hasn't been visited yet*. Always precedence-valid, good enough for a
demo, no LLM in the ordering path.

**v2 — agent-proposed order (the AI story):**
the planner agent passes `order_json` — an array of opaque `ref` strings
(`"<parcelId>:<kind>"`, kept out of the prose) — and the tool maps it back to
stops and **validates** (every pickup before its delivery). If a parcel
violates it, repair by moving that delivery to just after its pickup; if the
whole ordering is unusable, fall back to the v1 heuristic. Then
`compute_route(ordered_stops, optimize=False)`.

The message the agent sees is ID- and coordinate-free: each stop is
`{action, parcel, at, ref, collectNoLaterThan?}`. Stop labels for delivery
stops are the drop-off address (not the parcel name), so briefings, leg
summaries and the trip label all read "… at <place>".

`FAKE_ROUTES` path (`_fake_route`) already keeps identity order — it just
receives the pre-ordered stops.

---

## Planner agent (`app/planner/`)

- `agent.py` prompt gains: *some stops are pickups (label "Collect · …"); a
  parcel's pickup must be visited before its delivery, and within its
  collection window if one is given; `compute_routes` returns a
  precedence-valid order — use it; call out any pickup/delivery the ETA puts at
  risk; describe collections and drop-offs in the briefing.*
- The stops passed via session state carry `kind` and, for pickups, the
  window (`earliest`/`latest`) so the agent can sequence around it.
- `tools.py` `compute_routes`: no signature change. `compute_route` branches on
  `any(s.kind == "pickup")` to use precedence ordering instead of Google's
  `optimizeWaypointOrder`. The result carries `ordered_stops` as ready-made
  "Collect / Drop off <parcel> at <place>" lines.
- Output schema: `ordered_stops` (friendly strings) + `briefing`.

### Pickup time windows

Google Routes v2 has no per-waypoint time windows, so this is a **soft
constraint**, handled exactly like delivery deadlines: `compute_planned_etas`
gets a row per pickup stop too, and marks `atRisk` when the projected ETA is
before `earliest` (too early — would wait) or after `latest` (too late). The
agent sequences to avoid it; the manifest shows the badge; nothing is
hard-rejected.

## Suggest runs agent (`app/dispatcher/`)

- `list_pending_parcels` output includes `pickup` so the agent can cluster by
  pickup geography too.
- `evaluate_route` uses the shared `build_run_stops` → gets pickup stops for
  free.
- Prompt: *parcels may have a pickup; each batch's route collects before
  delivering; `evaluate_route` handles ordering.*
- Capacity (`check_eligibility`): unchanged — total parcels ≤ vehicle capacity.
  Peak-load modelling deferred.

---

## Progress tracking (`app/main.py`)

`_check_deliveries` → `_check_stop_progress`. As `runtime["along"]` passes each
stop (same leg-boundary + proximity confirmation as today):

| stop kind | parcel status | → |
|---|---|---|
| `pickup`   | `in_transit` | `picked_up`, alert "Collected: X" |
| `delivery` | `picked_up` (had a pickup) or `in_transit` (no pickup) | `delivered`, alert "Delivered: X" |
| `delivery` | `in_transit` but parcel *has* a pickup | skip + log (passed delivery before pickup — shouldn't happen with a valid order) |

- Trip **start**: all run parcels → `in_transit` (unchanged).
- Trip **end / arrived**: undelivered (incl. `picked_up`) → `delivered` at the
  final stop.
- Trip **end / manual abort**:
  - `in_transit` (pickup not yet done) → `pending` — still needs collecting
    from its original pickup point.
  - `picked_up` → `awaiting_redelivery` — assumed returned to the depot with
    the driver. Re-dispatch treats it as a depot-origin delivery (skips the
    `pickup` stop); the original `pickup` stays on the record for history.
- Trip summary gains `collectedCount` beside `deliveredCount`.

### Live risk alerts (dispatcher-facing, `post_position`)

Two checks run every position tick alongside `_check_stop_progress`; both emit
a plain alert (no trip-status change) that flashes the admin banner and lands
in the run's alert log. State lives in `store.runtime`.

- **`pickup_risk`** — `_check_pickup_risk`. For each not-yet-collected pickup
  stop with a `latest`, project the arrival from `runtime["along"]` using the
  remaining legs' traffic-aware durations (`_eta_seconds_to_stop`). If it lands
  after `latest` (+60 s slack), warn once per parcel: *"At risk of missing the
  pickup window for 'X' at <place> — projected ~N min late"*.
- **`stalled`** — `_check_stall`. Tracks on-route progress; if `along` gains
  &lt; 12 m for `STALL_SECONDS` (90 s) while the vehicle is on-route (not a
  deviation), not within 70 m of any stop, and not inside a known
  SLOW/TRAFFIC_JAM stretch (`route["traffic"]` vs the path index), warn: *"Vehicle
  has not moved for ~N min and it isn't traffic"*. Clears with `moving_again`
  once progress resumes; re-arms afterwards. Reset on start and on reroute.

## Reroute (`_maybe_reroute` / `_do_reroute`)

`remaining` stops keep `orderedStops` order and `_do_reroute` calls
`compute_route(optimize=False)`, so precedence is preserved as long as the
original order was valid (it is, by construction). Add one guard: a `delivery`
never enters `remaining` unless its `pickup` is also in `remaining` or already
done.

---

## Frontend

### Add / edit parcel form (`static/admin.html`, `static/js/admin.js`)

- Below Destination: a toggle **"Collect from a pickup location"** (off by
  default = loaded at depot).
- When on: a second place field (`pf-pickup` + `pf-pickup-label`), same
  search-or-map UX as destination, plus two optional `datetime-local` inputs
  for the collection window (from / until).
- Each location field gets a small **"set on map"** button that arms map-click
  for *that* field (replaces the current implicit "click sets destination").
- `saveParcel` POSTs `pickup: {lat,lng,label,earliest?,latest?}`.
  `POST /api/parcels` model: `pickup: Pickup | None`.

### Parcel rows (`renderParcels` / `parcelRow`)

- With a pickup: meta shows `Collect · <pickup> → <destination>` (truncated),
  plus a small **P** chip; if a window is set, show it.
- Groups (`PARCEL_GROUPS`): `awaiting_redelivery` joins the **Pending** group's
  match (it's re-plannable) with a "redelivery" chip; `picked_up` joins the
  **Out for delivery** match, row meta shows "collected — en route to drop".
- `create_run` / `delivery.create_run`: accept `pending` **and**
  `awaiting_redelivery` parcels (currently pending-only).

### Map markers (`common.js` `stopMarker`; `admin.js`, `driver.js`)

- `stopMarker(map, pos, index, total, kind)` — pickups render as an **outlined
  pin with "P"** (or a box-arrow-up glyph); deliveries stay numbered; depot is
  the warehouse "S".

### Manifest (run detail card, `renderManifest`)

- One row per stop in route order: **Collect — <label>** / **Deliver —
  <label>**, each with its ETA and a tick when done ("collected" / "delivered"
  / "at risk" — at risk also covers a pickup ETA outside its window).

### Driver nav (`static/js/driver.js`, `_flatten_steps`)

- ARRIVE pseudo-step text keyed off `stop.kind`: "Collect at X" vs "Deliver at
  X". `sw.js` `CACHE` bump.

---

## Phasing

| Phase | Status | Contents |
|---|---|---|
| **1** | ✅ done | Data model (`pickup` + window, `picked_up`, `awaiting_redelivery`) · `build_run_stops` · deterministic precedence order · `_check_stop_progress` · abort → `awaiting_redelivery` · form / rows / markers / manifest / driver wording. Runs work end-to-end with pickups. |
| **2** | ✅ done | Planner agent proposes the visiting order via `compute_routes(order_json=…)`; `routing.repair_precedence` guarantees pickup-before-delivery; the tool returns per-stop ETA + at-risk (window / deadline); `compute_route(presequenced=…)` keeps the agent's order; briefing narrates collections and flags at-risk. |
| **3** | ✅ done | Dispatch agent prompt covers pickups (batch by pickup proximity, read `perStop`); `evaluate_route` accepts `awaiting_redelivery`. Reroute runs `repair_precedence` on the remaining stops so a straddled progress estimate can't produce an invalid order. Coincident stops: `_flatten_steps` drops the 0 m leg's driving steps and the duplicate arrival announcement (one "Collect at WH" instead of three); the data model stays one stop per parcel-action — full stop-merge (`parcelIds` lists everywhere) is not worth the precedence-logic risk for the demo. |
