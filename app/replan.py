"""Dispatcher edits to a run that already exists: add parcels (even to a run
that is under way), take parcels off, move a parcel to another run, or hand a
planned run to another driver.

Every change re-routes deterministically through the Routes API (no LLM):
 - a planned run is re-planned from the hub;
 - a run in progress is re-planned from the vehicle's current position through
   the stops that are still to do. The route version is bumped, so the driver app
   picks the new route up on its next position update (same path as auto-reroute).
"""

import time
import uuid

from . import db, delivery, routing, store
from .delivery import RunError

OPEN = ("assigned", "in_transit", "picked_up")     # on the run and not finished


def _alert(trip_id: str, kind: str, message: str) -> dict:
    alert = {"id": f"a-{uuid.uuid4().hex[:6]}", "tripId": trip_id, "type": kind,
             "distanceMeters": 0, "position": None, "ts": time.time(), "message": message}
    return alert


def _load_parcels(ids: list[str]) -> list[dict]:
    out = []
    for pid in ids:
        p = db.load_entity("parcels", pid)
        if not p:
            raise RunError(f"Parcel {pid} not found", 404)
        out.append(p)
    return out


async def replan(trip_id: str, add_ids: list[str] | None = None,
                 remove_ids: list[str] | None = None, driver_id: str | None = None) -> dict:
    add_ids, remove_ids = list(add_ids or []), list(remove_ids or [])
    trip = store.get_trip(trip_id)
    if not trip:
        raise RunError("Run not found", 404)
    if trip["status"] == "completed":
        raise RunError("This run is finished — it can't be changed.")
    live = trip["status"] in ("active", "deviating")

    # ---- who drives
    driver = db.load_entity("drivers", trip.get("driverId") or "")
    if driver_id and driver_id != trip.get("driverId"):
        if live:
            raise RunError("A run that is under way can't change driver.")
        driver = db.load_entity("drivers", driver_id)
        if not driver:
            raise RunError("Driver not found", 404)
        if delivery.entity_in_use("drivers", driver["id"]):
            raise RunError(f"{driver['name']} already has an unfinished run.")
    vehicle = driver and db.load_entity("vehicles", driver.get("vehicleId") or "")
    if not driver or not vehicle:
        raise RunError("The run needs a driver with a vehicle.")

    # ---- which parcels
    members = _load_parcels(trip.get("parcelIds") or [])
    by_id = {p["id"]: p for p in members}
    for pid in remove_ids:
        p = by_id.get(pid)
        if not p:
            raise RunError("That parcel isn't on this run.")
        if p["status"] not in OPEN:
            raise RunError(f"'{p['name']}' is already {p['status'].replace('_', ' ')} — it can't be taken off.")
    added = _load_parcels(add_ids)
    for p in added:
        if p["id"] in by_id:
            raise RunError(f"'{p['name']}' is already on this run.")
        if not delivery.is_plannable(p):
            raise RunError(f"'{p['name']}' is not available to add (it is {p['status'].replace('_', ' ')}).")

    open_now = [p for p in members if p["status"] in OPEN and p["id"] not in remove_ids]
    new_open = open_now + added
    if not new_open:
        raise RunError("A run needs at least one parcel — delete the run instead.")

    reason = delivery.check_eligibility(vehicle, new_open, None if live else driver)
    if reason:
        raise RunError(f"{vehicle['name']} can't take that: {reason}")

    # ---- re-route
    stops = delivery.build_run_stops(new_open)
    if live:
        pos = trip.get("lastPosition") or {}
        origin = {"lat": pos.get("lat", stops[0]["lat"]), "lng": pos.get("lng", stops[0]["lng"]),
                  "label": "Current location"}
        stops = [origin] + stops[1:]
    special = routing.has_special_handling(stops)
    if special:
        stops = routing.sequence_stops(stops)
    try:
        route = await routing.compute_route(
            stops, avoid_tolls=trip.get("avoidTolls", False), optimize=not special,
            vehicle_type=vehicle["type"], presequenced=special)
    except routing.RoutingError as e:
        raise RunError(f"Couldn't re-plan the route: {e}", 502)

    parcels_by_id = {p["id"]: p for p in new_open}
    now = time.time()
    changes = []
    if added:
        changes.append(f"+{len(added)} parcel{'s' if len(added) != 1 else ''}")
    if remove_ids:
        changes.append(f"−{len(remove_ids)} parcel{'s' if len(remove_ids) != 1 else ''}")
    if driver_id and driver_id != trip.get("driverId"):
        changes.append(f"driver → {driver['name']}")

    trip.update({
        "stops": stops,
        "route": {**route, "briefing": f"Updated by dispatch ({', '.join(changes)}). "
                                       f"{len(route['orderedStops']) - 1} stops to go."},
        "routeVersion": trip["routeVersion"] + 1,
        "driverId": driver["id"], "vehicleId": vehicle["id"], "vehicleType": vehicle["type"],
        "parcelIds": [p["id"] for p in members if p["id"] not in remove_ids] + [p["id"] for p in added],
        "plannedEtaPerStop": delivery.compute_planned_etas(route, parcels_by_id, now),
    })
    if live:
        trip["status"] = "active"
        store.runtime(trip_id).update(
            consecutive=0, alerting=False, along=0.0, stall_along=0.0,
            stall_since=now, stall_fired=False, pickup_risk_fired=set())

    # ---- parcels follow the run
    for pid in remove_ids:
        p = by_id[pid]
        back = "awaiting_redelivery" if p["status"] == "picked_up" else "pending"
        delivery.set_parcel_status(p, back, trip_id=None, note=f"Taken off run {trip_id} by dispatch")
    for p in added:
        delivery.set_parcel_status(p, "in_transit" if live else "assigned", trip_id=trip_id,
                                   note=f"Added to run {trip_id} by dispatch"
                                        + (" (run already under way)" if live else ""))

    alert = _alert(trip_id, "replanned", f"Dispatch changed the run ({', '.join(changes)}) — route recalculated")
    trip["alerts"].append(alert)
    store.save_trip(trip)
    store.broadcast(trip_id, "alert", alert)
    store.broadcast(trip_id, "reroute", {"routeVersion": trip["routeVersion"]})
    return trip


async def move_parcel(parcel_id: str, to_trip_id: str) -> tuple[dict, dict | None]:
    """Take a parcel off its current run (if any) and put it on `to_trip_id`.
    Returns (target trip, source trip or None if that run was emptied and
    cancelled)."""
    parcel = db.load_entity("parcels", parcel_id)
    if not parcel:
        raise RunError("Parcel not found", 404)
    from_id = parcel.get("tripId")
    if from_id == to_trip_id:
        raise RunError("The parcel is already on that run.")
    target = store.get_trip(to_trip_id)
    if not target:
        raise RunError("Run not found", 404)
    # check the target can take it before touching the source run
    driver = db.load_entity("drivers", target.get("driverId") or "")
    vehicle = driver and db.load_entity("vehicles", driver.get("vehicleId") or "")
    if not vehicle:
        raise RunError("The target run has no vehicle.")
    on_board = [p for p in _load_parcels(target.get("parcelIds") or []) if p["status"] in OPEN]
    reason = delivery.check_eligibility(vehicle, on_board + [parcel], None)
    if reason:
        raise RunError(f"{vehicle['name']} can't take it: {reason}")

    source = None
    if from_id:
        src = store.get_trip(from_id)
        if src and src["status"] != "completed":
            remaining = [p for p in _load_parcels(src.get("parcelIds") or [])
                         if p["status"] in OPEN and p["id"] != parcel_id]
            if remaining:
                source = await replan(from_id, remove_ids=[parcel_id])
            elif src["status"] == "planned":
                # moving the last parcel empties the run — cancel it
                delivery.set_parcel_status(parcel, "pending", trip_id=None,
                                           note=f"Taken off run {from_id} by dispatch")
                store.delete_trip(from_id)
                store.broadcast(from_id, "removed", {})
            else:
                raise RunError("It's the last parcel on a run under way — it can't be moved.")
    parcel = db.load_entity("parcels", parcel_id)
    try:
        target = await replan(to_trip_id, add_ids=[parcel_id])
    except RunError:
        if source is not None and from_id:       # put it back where it was
            try:
                await replan(from_id, add_ids=[parcel_id])
            except RunError:
                pass
        raise
    return target, source


async def breakdown(trip_id: str, apply: bool = False) -> dict:
    """A vehicle breaks down: give its remaining parcels to the other runs that can
    take them (best fit first, express first), or report what can't be placed.
    With apply=False it only previews; with apply=True it does it, re-planning every
    run that receives a parcel. A parcel already collected is assumed recovered to
    the hub by dispatch: a pickup request is received there, a delivery is put back
    in the queue and re-planned like the rest."""
    from . import fit
    trip = store.get_trip(trip_id)
    if not trip:
        raise RunError("Run not found", 404)
    if trip["status"] == "completed":
        raise RunError("This run is already finished.")
    members = _load_parcels(trip.get("parcelIds") or [])
    on_van = [p for p in members if p["status"] == "picked_up"]
    movable = [p for p in members if p["status"] in ("assigned", "in_transit")]
    collected = {p["id"] for p in on_van}
    recovered = [{"parcelId": p["id"], "name": p["name"], "note": "received at the hub"}
                 for p in on_van if p.get("requestType") == "pickup"]
    to_place = movable + [p for p in on_van if p.get("requestType") != "pickup"]
    rank = lambda p: routing.PRIORITY_RANK.get(p.get("priority") or "standard", 2)
    to_place.sort(key=lambda p: (rank(p), p["deadline"]))

    if not apply:
        moved, unplaced = [], []
        for p in to_place:
            best = next((o for o in fit.suggest(p, exclude=trip_id) if o["feasible"]), None)
            if best:
                moved.append({"parcelId": p["id"], "name": p["name"], "toTripId": best["tripId"],
                              "toLabel": best["label"], "toDriver": best["driverName"],
                              "extraMinutes": best["extraMinutes"], "collected": p["id"] in collected})
            else:
                unplaced.append({"parcelId": p["id"], "name": p["name"],
                                 "reason": "No other run can take it in time"})
        return {"tripId": trip_id, "applied": False, "moved": moved, "unplaced": unplaced,
                "recovered": recovered,
                "note": "Preview only. Each parcel is checked against the runs as they are now."}

    # apply: close this run first so it is never offered as a destination
    for p in on_van:
        if p.get("requestType") == "pickup":
            delivery.set_parcel_status(p, "delivered", note="Recovered to the hub after a vehicle breakdown")
        else:
            delivery.set_parcel_status(p, "awaiting_redelivery", trip_id=None,
                                       note=f"Back at the hub after the breakdown on run {trip_id}")
    for p in movable:
        delivery.set_parcel_status(p, "pending", trip_id=None, note=f"Vehicle breakdown on run {trip_id}")
    trip["status"], trip["endedAt"] = "completed", time.time()
    alert = _alert(trip_id, "breakdown", f"Vehicle breakdown — {len(to_place)} parcel(s) being reassigned")
    trip["alerts"].append(alert)
    store.save_trip(trip)
    store.broadcast(trip_id, "alert", alert)
    store.broadcast(trip_id, "status", {"status": "completed"})

    moved, unplaced = [], []
    for p in to_place:
        placed = False
        for o in [o for o in fit.suggest(db.load_entity("parcels", p["id"]), exclude=trip_id) if o["feasible"]]:
            try:
                await replan(o["tripId"], add_ids=[p["id"]])
            except RunError:
                continue
            moved.append({"parcelId": p["id"], "name": p["name"], "toTripId": o["tripId"], "toLabel": o["label"],
                          "toDriver": o["driverName"], "extraMinutes": o["extraMinutes"],
                          "collected": p["id"] in collected})
            placed = True
            break
        if not placed:
            unplaced.append({"parcelId": p["id"], "name": p["name"],
                             "reason": "No other run could take it — it is back on the pending list"})
    return {"tripId": trip_id, "applied": True, "moved": moved, "unplaced": unplaced, "recovered": recovered}
