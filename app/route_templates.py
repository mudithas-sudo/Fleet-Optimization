"""Predefined routes (route templates).

Parcel carriers usually run on standing routes: a courier is assigned a service area
(zone) and a set of recurring pickups (contract customers collected on set days), and the
day's parcels are fitted onto that route rather than planned from scratch. A template here
is exactly that: a code and name, a zone (centre + radius), a default driver, the service
days and start time, and the recurring pickups.

"Run today's route" turns a template into a real run: it creates today's recurring pickups
(once per day), gathers the pending parcels inside the zone, and plans them deterministically
(no AI, no keys) with the same sequencing, capacity and driver checks as any other run.
Nothing is hard-wired — the dispatcher sees what will be included first (`preview`).
"""

import time

from . import db, delivery, routing

TABLE = "route_templates"
MAX_RADIUS_KM = 100.0


def _hhmm_today(value: str | None, now: float) -> float | None:
    mins = delivery._hhmm(value) if value else None
    if mins is None:
        return None
    t = time.localtime(now)
    return time.mktime((t.tm_year, t.tm_mon, t.tm_mday, mins // 60, mins % 60, 0, 0, 0, -1))


def day_key(now: float | None = None) -> str:
    return time.strftime("%Y-%m-%d", time.localtime(now or time.time()))


def list_templates() -> list[dict]:
    return sorted(db.all_entities(TABLE), key=lambda t: t["code"].lower())


def get(template_id: str) -> dict | None:
    return db.load_entity(TABLE, template_id)


def save(data: dict, template_id: str | None = None) -> dict:
    existing = get(template_id) if template_id else None
    t = {
        "id": template_id or delivery._new_id("rt"),
        "code": data["code"].strip(),
        "name": data["name"].strip(),
        "zone": data["zone"],                       # {lat, lng, radiusKm, label}
        "driverId": data.get("driverId") or None,
        "days": sorted(set(data.get("days") or [0, 1, 2, 3, 4])),   # Monday = 0
        "startTime": data.get("startTime") or None,
        "pickups": [{**p, "id": p.get("id") or delivery._new_id("rp")} for p in data.get("pickups") or []],
        "notes": (data.get("notes") or "").strip() or None,
        "createdAt": (existing or {}).get("createdAt") or time.time(),
    }
    db.save_entity(TABLE, t)
    return t


def delete(template_id: str) -> None:
    db.delete_entity(TABLE, template_id)


def runs_today(t: dict) -> bool:
    return time.localtime().tm_wday in t["days"]


def zone_parcels(t: dict) -> list[dict]:
    """Pending parcels that belong to this zone: a delivery whose address is inside it, or a
    pickup request whose customer is inside it."""
    z = t["zone"]
    out = []
    for p in db.all_entities("parcels"):
        if not delivery.is_plannable(p) or p.get("templateId"):
            continue
        spot = p.get("pickup") if p.get("requestType") == "pickup" else p["destination"]
        if spot and routing._haversine_m(z, spot) <= z["radiusKm"] * 1000:
            out.append(p)
    return out


def _pickup_parcel_today(t: dict, stop: dict, today: str) -> dict | None:
    return next((p for p in db.all_entities("parcels")
                 if p.get("templateId") == t["id"] and p.get("templateStopId") == stop["id"]
                 and p.get("templateDay") == today), None)


def preview(t: dict, now: float | None = None) -> dict:
    now = now or time.time()
    today = day_key(now)
    pickups = []
    for s in t["pickups"]:
        existing = _pickup_parcel_today(t, s, today)
        pickups.append({"stopId": s["id"], "label": s["label"], "readyBy": s.get("readyBy"), "collectBy": s.get("collectBy"),
                        "state": ("on a run" if existing and not delivery.is_plannable(existing) else
                                  "created" if existing else "will be created")})
    zp = zone_parcels(t)
    return {"templateId": t["id"], "runsToday": runs_today(t), "zoneParcels": [
        {"id": p["id"], "name": p["name"], "trackingNumber": p.get("trackingNumber"),
         "requestType": p.get("requestType") or "delivery"} for p in zp], "pickups": pickups}


def _make_pickup(t: dict, stop: dict, now: float) -> dict:
    ready = _hhmm_today(stop.get("readyBy"), now)
    by = _hhmm_today(stop.get("collectBy"), now) or ((ready or now) + 2 * 3600)
    by = max(by, now + 3600)                        # a late call still gets a realistic collect-by time
    parcel = delivery.new_parcel({
        "name": f"{stop.get('name') or 'Scheduled pickup'} — {stop['label']}", "size": stop.get("size") or "small",
        "requestType": "pickup", "deadline": by, "notes": stop.get("note"),
        "destination": {k: delivery.get_depot()[k] for k in ("lat", "lng", "label")},
        "pickup": {"lat": stop["lat"], "lng": stop["lng"], "label": stop["label"],
                   "earliest": ready if ready and ready > now else None, "latest": by}})
    parcel.update({"templateId": t["id"], "templateStopId": stop["id"], "templateDay": day_key(now)})
    db.save_entity("parcels", parcel)
    return parcel


async def run_today(t: dict, driver_id: str | None = None, now: float | None = None) -> dict:
    """Create today's run from the template. Everything is checked before anything is created."""
    now = now or time.time()
    driver_id = driver_id or t.get("driverId")
    if not driver_id:
        raise delivery.RunError("Choose a driver for this route.", 400)
    if not db.load_entity("drivers", driver_id):
        raise delivery.RunError("Driver not found", 404)
    today = day_key(now)

    ids = [p["id"] for p in zone_parcels(t)]
    todays = []
    for s in t["pickups"]:
        existing = _pickup_parcel_today(t, s, today)
        if existing and not delivery.is_plannable(existing):
            continue                                # already on a run
        todays.append((s, existing))
    if not ids and not todays:
        raise delivery.RunError("Nothing to run: no pending parcels in this zone and no recurring pickups due.")

    created = []
    try:
        for s, existing in todays:
            parcel = existing or _make_pickup(t, s, now)
            if not existing:
                created.append(parcel)
            ids.append(parcel["id"])
        trip = await delivery.create_run(ids, driver_id, deterministic=True)
    except Exception:
        for p in created:                           # don't leave orphan pickups behind a failed run
            db.delete_entity("parcels", p["id"])
        raise
    trip.update({"templateId": t["id"], "templateCode": t["code"], "templateName": t["name"]})
    from . import store
    store.save_trip(trip)
    return trip
