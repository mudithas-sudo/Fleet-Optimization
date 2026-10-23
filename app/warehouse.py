"""The loading bay and the receiving desk, for warehouse / hub staff.

Loading: for every run that has not left yet, the parcels to hand to that driver — in the
order to put them on the vehicle (the last stop goes in first, so the first stop is at the door)
— with a tick per parcel. Parcels that are collected from a customer on the way (pickup requests,
collect-and-deliver) are not loaded at the hub, so they are not on the list.

Receiving: what is coming back to the hub and needs checking in: collected pickups, parcels the
driver could not deliver, parcels returned to the sender.
"""

import time

from . import db, delivery, rides, store

LOAD_STATUSES = ("assigned",)           # on a planned run, not yet out for delivery


def _loadable(trip: dict, parcel: dict) -> bool:
    if parcel.get("requestType") == "pickup":
        return False
    return not delivery.route_has_pickup(trip["route"], parcel["id"])


def run_loading(trip: dict, by_id: dict, drivers: dict, vehicles: dict) -> dict | None:
    stops = trip["route"]["orderedStops"]
    stop_of = {}
    for i, s in enumerate(stops):
        if s.get("parcelId") and delivery.stop_kind(s) == "delivery" and not s.get("hubReturn"):
            stop_of.setdefault(s["parcelId"], i)
    rows = []
    for pid in trip.get("parcelIds") or []:
        p = by_id.get(pid)
        if not p or p["status"] not in LOAD_STATUSES + ("in_transit",) or not _loadable(trip, p):
            continue
        rows.append({
            "id": p["id"], "trackingNumber": p.get("trackingNumber"), "name": p["name"], "size": p.get("size"),
            "weightKg": p.get("weightKg"), "fragile": bool(p.get("fragile")), "priority": p.get("priority") or "standard",
            "to": (p["destination"].get("label") or "").strip(), "receiver": p.get("receiverName"),
            "notes": p.get("notes"), "stopIndex": stop_of.get(pid), "loaded": bool(p.get("loadedAt")),
            "loadedAt": p.get("loadedAt"), "loadedBy": p.get("loadedBy")})
    if not rows:
        return None
    rows.sort(key=lambda r: -(r["stopIndex"] or 0))            # last stop first onto the vehicle
    for i, r in enumerate(rows, 1):
        r["loadOrder"] = i
    d, v = drivers.get(trip.get("driverId"), {}), vehicles.get(trip.get("vehicleId"), {})
    loaded = sum(1 for r in rows if r["loaded"])
    return {
        "tripId": trip["id"], "label": rides._label(stops), "status": trip["status"],
        "driverName": d.get("name"), "driverPhone": d.get("phone"), "vehicleName": v.get("name"),
        "plate": v.get("plate"), "vehicleType": trip.get("vehicleType", "car"),
        "templateCode": trip.get("templateCode"), "createdAt": trip.get("createdAt"),
        "total": len(rows), "loaded": loaded, "ready": loaded == len(rows), "parcels": rows,
    }


def loading_board() -> dict:
    by_id = {p["id"]: p for p in db.all_entities("parcels")}
    drivers = {d["id"]: d for d in db.all_entities("drivers")}
    vehicles = {v["id"]: v for v in db.all_entities("vehicles")}
    runs = []
    for t in store.list_trips():
        if t["status"] != "planned":
            continue
        r = run_loading(t, by_id, drivers, vehicles)
        if r:
            runs.append(r)
    runs.sort(key=lambda r: (r["ready"], r["createdAt"] or 0))      # what still needs doing first
    return {"runs": runs, "totals": {"runs": len(runs), "parcels": sum(r["total"] for r in runs),
                                     "loaded": sum(r["loaded"] for r in runs),
                                     "readyRuns": sum(1 for r in runs if r["ready"])}}


def receiving_board() -> dict:
    items = []
    trips = {t["id"]: t for t in store.list_trips()}
    drivers = {d["id"]: d for d in db.all_entities("drivers")}
    for p in db.all_entities("parcels"):
        if p.get("receivedAt"):
            continue
        kind = None
        if p.get("requestType") == "pickup" and p["status"] in ("picked_up", "delivered"):
            kind = "collected"
        elif p["status"] in ("failed", "awaiting_redelivery"):
            kind = "not_delivered"
        elif p["status"] == "returned":
            kind = "returned"
        if not kind:
            continue
        f = p.get("lastFailure") or {}
        t = trips.get(p.get("tripId") or f.get("tripId") or "")
        when = p.get("deliveredAt") or p.get("pickedUpAt") or f.get("ts") or p.get("createdAt")
        items.append({
            "id": p["id"], "trackingNumber": p.get("trackingNumber"), "name": p["name"], "size": p.get("size"),
            "weightKg": p.get("weightKg"), "fragile": bool(p.get("fragile")), "kind": kind, "status": p["status"],
            "reason": f.get("label") if kind != "collected" else None, "customer": p.get("receiverName"),
            "from": (p.get("pickup") or {}).get("label") if kind == "collected" else None,
            "driverName": (drivers.get((t or {}).get("driverId")) or {}).get("name"), "since": when})
    items.sort(key=lambda r: r["since"] or 0)
    return {"items": items, "counts": {k: sum(1 for i in items if i["kind"] == k) for k in ("collected", "not_delivered", "returned")}}


def set_loaded(parcel: dict, loaded: bool, actor: str) -> dict:
    if parcel["status"] not in LOAD_STATUSES + ("in_transit",):
        raise ValueError("This parcel isn't waiting to be loaded.")
    trip = store.get_trip(parcel.get("tripId") or "")
    if trip and not _loadable(trip, parcel):
        raise ValueError("This parcel is collected on the route, not loaded at the hub.")
    if loaded == bool(parcel.get("loadedAt")):
        return parcel
    parcel["loadedAt"], parcel["loadedBy"] = (time.time(), actor) if loaded else (None, None)
    v = db.load_entity("vehicles", (db.load_entity("drivers", (trip or {}).get("driverId") or "") or {}).get("vehicleId") or "")
    delivery.add_event(parcel, "loaded" if loaded else "unloaded",
                       (f"Loaded onto {v['name']}" if v else "Loaded onto the vehicle") + f" by {actor}" if loaded
                       else f"Taken off the vehicle by {actor}", by=actor)
    db.save_entity("parcels", parcel)
    store.broadcast_global("parcel", {"parcel": parcel, "deleted": False})
    return parcel


def check_in(parcel: dict, actor: str, note: str | None = None) -> dict:
    if parcel["status"] not in ("picked_up", "delivered", "failed", "awaiting_redelivery", "returned"):
        raise ValueError("This parcel isn't expected back at the hub.")
    if parcel.get("receivedAt"):
        return parcel
    parcel["receivedAt"], parcel["receivedBy"] = time.time(), actor
    delivery.add_event(parcel, "received", f"Checked in at the hub by {actor}" + (f" — {note}" if note else ""), by=actor)
    db.save_entity("parcels", parcel)
    store.broadcast_global("parcel", {"parcel": parcel, "deleted": False})
    return parcel
