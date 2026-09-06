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

## Non-goals (v1)

Time windows on pickups; peak/partial-load capacity modelling; standalone
pickup-only or delivery-only jobs; driver manual confirm buttons (progress
stays automatic as the vehicle passes a stop, matching the current simulator).

---

## Data model

Parcels are JSON blobs in SQLite — no migration, just new optional fields.

```
parcel.pickup       : {lat, lng, label} | null   # null ⇒ loaded at the depot (today's behaviour)
parcel.pickedUpAt   : float | null               # timestamp, mirrors deliveredAt
parcel.status       : pending | assigned | in_transit | picked_up | delivered
                      # picked_up only occurs when pickup != null
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
    if p.pickup:  append {**p.pickup,       label: "Collect · <name>", parcelId: p.id, kind: "pickup"}
    append              {**p.destination,   label: "<name>",           parcelId: p.id, kind: "delivery"}
```

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
the planner agent returns `ordered_stop_labels`; the service maps it back to
stop indices and **validates** (every pickup index < its delivery index). If a
parcel violates it, repair by moving that delivery to just after its pickup;
if the whole ordering is unusable, fall back to the v1 heuristic. Then
`compute_route(ordered_stops, optimize=False)`.

`FAKE_ROUTES` path (`_fake_route`) already keeps identity order — it just
receives the pre-ordered stops.

---

## Planner agent (`app/planner/`)

- `agent.py` prompt gains: *some stops are pickups (label "Collect · …"); a
  parcel's pickup must be visited before its delivery; `compute_routes` returns
  a precedence-valid order — use it; describe collections and drop-offs in the
  briefing.*
- `tools.py` `compute_routes`: stops from session state already carry `kind`;
  no signature change. `compute_route` branches on `any(s.kind == "pickup")`.
- Output schema unchanged. `ordered_stop_labels` stays descriptive.

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
- Trip **end / manual abort**: `in_transit` **and** `picked_up` → back to
  `pending`. *Known simplification — a real system needs a return-to-depot or
  hand-off flow for already-collected parcels. See open questions.*
- Trip summary gains `collectedCount` beside `deliveredCount`.

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
  search-or-map UX as destination.
- Each location field gets a small **"set on map"** button that arms map-click
  for *that* field (replaces the current implicit "click sets destination").
- `saveParcel` POSTs `pickup`. `POST /api/parcels` model: `pickup: Stop | None`.

### Parcel rows (`renderParcels` / `parcelRow`)

- With a pickup: meta shows `Collect · <pickup> → <destination>` (truncated),
  plus a small **P** chip.
- "Out for delivery" group's match extends to include `picked_up`; row meta
  shows the sub-status ("collected — en route to drop").

### Map markers (`common.js` `stopMarker`; `admin.js`, `driver.js`)

- `stopMarker(map, pos, index, total, kind)` — pickups render as an **outlined
  pin with "P"** (or a box-arrow-up glyph); deliveries stay numbered; depot is
  the warehouse "S".

### Manifest (run detail card, `renderManifest`)

- One row per stop in route order: **Collect — <label>** / **Deliver —
  <label>**, each with its ETA and a tick when done ("collected" / "delivered"
  / "at risk").

### Driver nav (`static/js/driver.js`, `_flatten_steps`)

- ARRIVE pseudo-step text keyed off `stop.kind`: "Collect at X" vs "Deliver at
  X". `sw.js` `CACHE` bump.

---

## Open questions

1. **Aborted run with collected parcels** — v1 sends them back to `pending`
   (loses the "physically on the van" fact). Acceptable for the demo, or add an
   `awaiting_redelivery` state?
2. **Pickup timing** — just "before delivery", or is there a "collect after
   HH:MM" constraint to model?
3. **Driver confirmation** — keep auto-progress as the van passes each stop, or
   add "Confirm pickup / delivery" buttons in the driver PWA?

---

## Phasing

| Phase | Contents |
|---|---|
| **1** | Data model · `build_run_stops` + coincident-stop dedupe · deterministic precedence order · `_check_stop_progress` + `picked_up` · form / rows / markers / manifest / driver wording. Runs work end-to-end with pickups. |
| **2** | Planner agent proposes the pickup→delivery order; server validate + repair; briefing describes collections. |
| **3** | Suggest-runs agent pickup-aware · reroute precedence guard · resolve the open questions. |
