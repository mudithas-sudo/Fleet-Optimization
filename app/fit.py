"""Where could a new pickup request (or any pending parcel) go? A quick, key-free
estimate for the dispatcher: for every run that hasn't finished it finds the
cheapest place in the remaining route to add the stop, then checks the vehicle
and driver limits, the customer's cut-off and the deliveries that would be pushed
back. It only suggests — adding the parcel goes through the normal
`POST /api/runs/{id}/parcels`, which re-plans properly (from the vehicle's
current position on a live run)."""

import time

from . import db, delivery, routing, store

ROAD_FACTOR = 1.3          # straight line -> road distance
SPEED_KMH = 28.0           # urban average
STOP_MIN = 6.0             # service time at a stop


def _minutes(a: dict, b: dict) -> float:
    return routing._haversine_m(a, b) * ROAD_FACTOR / 1000 / SPEED_KMH * 60


def _remaining(trip: dict, by_id: dict) -> list[dict]:
    """Stops of this run still to be visited (the hub drop-offs are handled as the end)."""
    out = []
    for s in trip["route"]["orderedStops"][1:]:
        p = by_id.get(s.get("parcelId"))
        kind = delivery.stop_kind(s)
        if s.get("hubReturn"):
            continue
        if p is None or p["status"] in ("delivered", "failed", "returned", "pending", "awaiting_redelivery"):
            continue
        if kind == "pickup" and p["status"] in ("picked_up",):
            continue
        out.append(s)
    return out


def suggest(parcel: dict, now: float | None = None) -> list[dict]:
    now = now or time.time()
    request = parcel.get("requestType") == "pickup"
    point = parcel.get("pickup") if request else parcel["destination"]
    hub = delivery.get_depot()
    pk = parcel.get("pickup") or {}
    cutoff = pk.get("earliest")
    latest = pk.get("latest") if request else parcel["deadline"]
    out = []
    for trip in store.list_trips():
        if trip["status"] not in ("planned", "active", "deviating"):
            continue
        vehicle = db.load_entity("vehicles", trip.get("vehicleId") or "")
        driver = db.load_entity("drivers", trip.get("driverId") or "")
        if not vehicle:
            continue
        parcels = [p for pid in trip.get("parcelIds") or [] if (p := db.load_entity("parcels", pid))]
        by_id = {p["id"]: p for p in parcels}
        live = trip["status"] in ("active", "deviating")
        row = {"tripId": trip["id"], "label": trip.get("label") or trip["id"],
               "driverName": (driver or {}).get("name"), "vehicleName": vehicle.get("name"),
               "state": "under way" if live else "planned"}
        reason = delivery.check_eligibility(vehicle, [p for p in parcels if p["status"] in
                                            ("assigned", "in_transit", "picked_up")] + [parcel], driver)
        if reason:
            out.append({**row, "feasible": False, "reason": reason})
            continue
        start = (trip.get("lastPosition") if live and trip.get("lastPosition") else None) or hub
        rest = _remaining(trip, by_id)
        seq = [start] + rest + [hub]
        # cheapest insertion of the new stop, and when we would get there
        best, elapsed, at_eta = None, 0.0, None
        for i in range(len(seq) - 1):
            a, b = seq[i], seq[i + 1]
            extra = _minutes(a, point) + _minutes(point, b) - _minutes(a, b) + STOP_MIN
            if best is None or extra < best[0]:
                best, at_eta = (extra, i), now + (elapsed + _minutes(a, point)) * 60
            elapsed += _minutes(a, b) + (STOP_MIN if i < len(rest) else 0)
        extra_min, idx = best
        # deliveries after the insertion point arrive later by `extra_min`
        pushed = []
        t = now
        for i, s in enumerate(rest):
            t += _minutes(seq[i], s) * 60 + STOP_MIN * 60
            if i >= idx and delivery.stop_kind(s) == "delivery":
                p = by_id.get(s.get("parcelId"))
                if p and t + extra_min * 60 > p["deadline"] >= t - 1:
                    pushed.append(p["name"])
        problems = []
        if latest and at_eta and at_eta > latest:
            problems.append("would arrive after " + ("the collect-by time" if request else "the deadline"))
        wait = 0
        if request and cutoff and not parcel.get("earlyOk") and at_eta and at_eta < cutoff:
            wait = round((cutoff - at_eta) / 60)
        if pushed:
            problems.append("would make " + ", ".join(pushed[:3]) + " late")
        out.append({**row, "feasible": not problems, "extraMinutes": round(extra_min),
                    "eta": at_eta, "waitMinutes": wait, "pushesLate": pushed,
                    "reason": "; ".join(problems) or None,
                    "stopsAfter": max(0, len(rest) - idx)})
    out.sort(key=lambda r: (not r["feasible"], r.get("extraMinutes", 10 ** 6) + r.get("waitMinutes", 0)))
    return out
