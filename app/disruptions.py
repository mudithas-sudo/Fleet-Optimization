"""Things that go wrong outside the plan: a flooded or blocked area, a hartal, a rider who doesn't turn up,
or a peak day with more parcels than riders.

* Area holds: parcels inside a flood / hartal / road-closure zone are kept out of new runs until it clears,
  and customers are told why, so nobody drives into it and nobody calls to ask.
* Absent rider: their planned runs go back to the queue the moment they are marked absent.
* Capacity forecast: demand against the riders available, day by day, and the extra capacity needed.
* Fuel quota: when fuel is rationed, how much of each vehicle's weekly allowance has been used.
"""

import math
import time
import uuid

from . import db, store

KINDS = {"flood": "Flood", "hartal": "Hartal or protest", "closure": "Road closure", "weather": "Severe weather", "other": "Other"}
_cache: dict = {"at": 0.0, "rows": []}


def _all() -> list[dict]:
    if time.time() - _cache["at"] > 2:
        _cache["rows"] = db.all_entities("disruptions")
        _cache["at"] = time.time()
    return _cache["rows"]


def _drop_cache():
    _cache["at"] = 0.0


def _km(a: dict, b: dict) -> float:
    dlat = (a["lat"] - b["lat"]) * 111.0
    dlng = (a["lng"] - b["lng"]) * 111.0 * math.cos(math.radians(a["lat"]))
    return math.hypot(dlat, dlng)


def active(now: float | None = None) -> list[dict]:
    now = now or time.time()
    return [d for d in _all() if d.get("active") and (not d.get("until") or d["until"] > now)]


def blocking(parcel: dict, now: float | None = None) -> dict | None:
    """The active disruption that stops this parcel from being planned, if any."""
    if parcel.get("requestType") == "pickup":
        point = parcel.get("pickup")
    else:
        point = parcel.get("destination")
    if not point or point.get("lat") is None:
        return None
    return next((d for d in active(now) if _km(point, d) <= d["radiusKm"]), None)


def _inside(d: dict) -> list[dict]:
    return [p for p in db.all_entities("parcels") if p["status"] in ("pending", "assigned", "awaiting_redelivery", "in_transit")
            and (blocking(p) or {}).get("id") == d["id"]]


def list_all() -> list[dict]:
    out = []
    for d in sorted(_all(), key=lambda x: -x["createdAt"]):
        live = bool(d in active())
        out.append({**d, "isActive": live, "parcelsInside": len(_inside(d)) if live else 0})
    return out


async def create(kind: str, label: str, lat: float, lng: float, radius_km: float, until: float | None, actor: str) -> dict:
    """Open a hold. Parcels inside stop being plannable; those already on a planned run are taken off it."""
    from . import delivery, notify, replan
    if kind not in KINDS:
        raise ValueError("kind must be one of " + ", ".join(KINDS))
    rec = {"id": f"dz-{uuid.uuid4().hex[:6]}", "createdAt": time.time(), "kind": kind, "label": (label or KINDS[kind]).strip()[:80],
           "lat": lat, "lng": lng, "radiusKm": max(0.5, min(50.0, float(radius_km))), "until": until, "active": True, "by": actor}
    db.save_entity("disruptions", rec)
    _drop_cache()
    held, released, live_runs, told = 0, 0, set(), 0
    for p in _inside(rec):
        if p["status"] == "in_transit":
            if p.get("tripId"):
                live_runs.add(p["tripId"])
            continue
        if p["status"] == "assigned" and p.get("tripId"):
            trip = store.get_trip(p["tripId"])
            if trip and trip["status"] not in ("active", "deviating", "completed"):
                try:
                    await replan.replan(p["tripId"], remove_ids=[p["id"]])
                    released += 1
                    p = db.load_entity("parcels", p["id"]) or p
                except delivery.RunError:
                    pass
        delivery.add_event(p, "held", f"Held: {rec['label']} in the delivery area", by=actor)
        db.save_entity("parcels", p)
        held += 1
        try:
            if notify.notify(p, "delayed", reason=rec["label"].lower()):
                told += 1
        except Exception:
            notify.log.exception("notification failed")
    store.broadcast_global("disruption", {"disruption": rec})
    return {"disruption": rec, "held": held, "releasedFromRuns": released, "liveRunsAffected": sorted(live_runs), "customersTold": told}


def clear(disruption_id: str, actor: str) -> dict:
    d = db.load_entity("disruptions", disruption_id)
    if not d:
        raise KeyError(disruption_id)
    d["active"] = False
    d["clearedAt"] = time.time()
    d["clearedBy"] = actor
    db.save_entity("disruptions", d)
    _drop_cache()
    store.broadcast_global("disruption", {"disruption": d})
    return d


# ---------------------------------------------------------------- absent rider

async def mark_absent(driver_id: str, reason: str, actor: str) -> dict:
    """The rider isn't coming in. Mark them on leave and send their planned runs' parcels back to the queue."""
    from . import delivery
    driver = db.load_entity("drivers", driver_id)
    if not driver:
        raise KeyError(driver_id)
    driver["onLeave"] = True
    driver["absentNote"] = (reason or "Did not report for work").strip()[:120]
    db.save_entity("drivers", driver)
    released, live = 0, []
    for trip in store.list_trips():
        if trip.get("driverId") != driver_id:
            continue
        if trip["status"] in ("active", "deviating"):
            live.append(trip["id"])
            continue
        if trip["status"] == "completed":
            continue
        for pid in trip.get("parcelIds") or []:
            p = db.load_entity("parcels", pid)
            if p and p["status"] in delivery.PARCEL_ACTIVE_STATUSES:
                delivery.set_parcel_status(p, "pending", trip_id=None, note=f"Back in the queue: {driver['name']} is absent")
                released += 1
        store.broadcast(trip["id"], "removed", {})
        store.delete_trip(trip["id"])
    return {"driver": driver, "releasedParcels": released, "liveRuns": live}


# ---------------------------------------------------------------- capacity and fuel

STOPS_PER_DAY = {"bike": 18, "car": 24, "van": 36, "truck": 36}


def driver_capacity(driver: dict) -> int:
    v = db.load_entity("vehicles", driver.get("vehicleId") or "")
    base = STOPS_PER_DAY.get((v or {}).get("type"), 24)
    return min(base, int(driver["maxStops"]) * 3) if driver.get("maxStops") else base


def forecast(days: int = 5, now: float | None = None) -> dict:
    """Demand (parcels) against capacity (stops the available riders can serve) for each coming day."""
    from . import delivery
    now = now or time.time()
    drivers = db.all_entities("drivers")
    parcels = [p for p in db.all_entities("parcels") if p["status"] in ("pending", "assigned", "awaiting_redelivery")
               and p.get("requestType") != "pickup"]
    out = []
    for i in range(days):
        day0 = delivery.day_start(now + i * 86400)
        day1 = day0 + 86400
        if i == 0:
            demand = [p for p in parcels if not (p.get("holdUntil") and p["holdUntil"] >= day1)]
        else:
            demand = [p for p in parcels if p.get("holdUntil") and day0 <= p["holdUntil"] + 3600 * 3 < day1 + 3 * 3600
                      or (p.get("deadline") and day0 <= p["deadline"] < day1 and not p.get("holdUntil"))]
        riders = [d for d in drivers if not d.get("onLeave")]
        cap = sum(driver_capacity(d) for d in riders)
        blocked = sum(1 for p in demand if blocking(p, day0 + 8 * 3600))
        load = len(demand) - blocked
        gap = max(0, load - cap)
        out.append({"date": day0, "demand": len(demand), "blockedByDisruption": blocked, "riders": len(riders), "capacity": cap,
                    "utilisationPct": round(100 * load / cap) if cap else None, "shortfall": gap,
                    "extraRiders": math.ceil(gap / 20) if gap else 0,
                    "status": "short" if gap else "tight" if cap and load > 0.85 * cap else "ok"})
    return {"days": out, "note": "Capacity is an estimate from vehicle type and each rider's stop limit. Replace it with your own figures in a pilot."}


def fuel_quota() -> dict:
    """Weekly litres used per vehicle against the allowance (set in the fuel settings; 0 = no rationing)."""
    from . import impact
    cfg = db.get_setting("fuel_quota") or {"weeklyLitres": 0}
    fuel = impact.get_fuel()
    since = time.time() - 7 * 86400
    used: dict[str, float] = {}
    for t in store.list_trips():
        if (t.get("startedAt") or t.get("createdAt") or 0) < since:
            continue
        d = db.load_entity("drivers", t.get("driverId") or "")
        v = d and db.load_entity("vehicles", d.get("vehicleId") or "")
        if not v:
            continue
        km = impact._km(t["route"]["orderedStops"]) if (t.get("route") or {}).get("orderedStops") else 0
        used[v["id"]] = used.get(v["id"], 0.0) + km * fuel["l100"].get(v["type"], 10) / 100
    quota = float(cfg.get("weeklyLitres") or 0)
    rows = []
    for v in db.all_entities("vehicles"):
        u = round(used.get(v["id"], 0.0), 1)
        rows.append({"vehicle": v["name"], "plate": v.get("plate"), "type": v["type"], "litres": u,
                     "remaining": round(quota - u, 1) if quota else None,
                     "usedPct": round(100 * u / quota) if quota else None})
    rows.sort(key=lambda r: -(r["usedPct"] or 0) if quota else -r["litres"])
    return {"weeklyLitres": quota, "vehicles": rows}


def set_fuel_quota(weekly_litres: float) -> dict:
    db.set_setting("fuel_quota", {"weeklyLitres": max(0.0, float(weekly_litres))})
    return fuel_quota()
