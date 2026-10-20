"""Rides (runs) at a point in time.

Answers "what was every run doing at 14:30?" from what is already recorded: a
run's started/ended times, its alert log, the per-second position log, and each
parcel's history. `at=None` means now, using the live state.

States at a moment:
  scheduled  the run exists but the driver hasn't started it yet
  en_route   started and not finished (or `deviating` while off the planned route)
  finished   the driver ended it
"""

import time

from . import db, delivery, store


def day_start(ts: float) -> float:
    t = time.localtime(ts)
    return time.mktime((t.tm_year, t.tm_mon, t.tm_mday, 0, 0, 0, 0, 0, -1))


def _label(stops: list[dict]) -> str:
    first = stops[0]
    origin = "Depot" if (first.get("depot") or first.get("kind") == "depot") else first.get("label", "Start")
    last = stops[-1]
    return f"{origin} → {last.get('place') or last.get('label', '')}"


def _state_at(trip: dict, at: float, live: bool) -> str:
    if live:
        return {"planned": "scheduled", "active": "en_route", "deviating": "deviating",
                "completed": "finished"}.get(trip["status"], "scheduled")
    ended, started = trip.get("endedAt"), trip.get("startedAt")
    if ended and at >= ended:
        return "finished"
    if started and at >= started:
        # the last deviation / back-on-route alert before `at` decides
        state = "en_route"
        for a in sorted(trip.get("alerts", []), key=lambda a: a["ts"]):
            if a["ts"] > at:
                break
            if a["type"] == "deviation":
                state = "deviating"
            elif a["type"] in ("back_on_route", "reroute"):
                state = "en_route"
        return state
    return "scheduled"


def _parcel_facts(parcels: list[dict]) -> dict:
    """trip id -> {delivered: {parcel id: ts}, failed: [ts]} from parcel histories."""
    facts: dict[str, dict] = {}
    for p in parcels:
        for e in p.get("events", []):
            tid = e.get("tripId")
            if not tid:
                continue
            f = facts.setdefault(tid, {"delivered": {}, "failed": []})
            if e["type"] == "delivered":
                f["delivered"][p["id"]] = e["ts"]
            elif e["type"] == "failed":
                f["failed"].append(e["ts"])
    return facts


def rides_at(at: float | None = None) -> dict:
    now = time.time()
    live = at is None or abs(at - now) < 5
    at = now if live else at
    start = day_start(at)
    drivers = {d["id"]: d for d in db.all_entities("drivers")}
    vehicles = {v["id"]: v for v in db.all_entities("vehicles")}
    facts = _parcel_facts(db.all_entities("parcels"))

    rides = []
    for t in store.list_trips():
        created, started, ended = t.get("createdAt") or 0, t.get("startedAt"), t.get("endedAt")
        if created > at:
            continue                                   # didn't exist yet
        if ended and ended < start:
            continue                                   # finished before this day began
        if not started and not live and created < start - 86400:
            continue                                   # a stale never-started run
        state = _state_at(t, at, live)
        f = facts.get(t["id"], {"delivered": {}, "failed": []})
        delivered_ids = {pid for pid, ts in f["delivered"].items() if ts <= at}
        failed = sum(1 for ts in f["failed"] if ts <= at)
        stops = t["route"]["orderedStops"]
        nxt = next((s for s in stops[1:] if delivery.stop_kind(s) == "delivery"
                    and s.get("parcelId") not in delivered_ids), None)
        eta = None
        if nxt and state in ("en_route", "deviating") and live:
            eta = next((e["eta"] for e in t.get("plannedEtaPerStop") or []
                        if e.get("parcelId") == nxt.get("parcelId") and e.get("kind") == "delivery"), None)

        if state == "scheduled":
            pos = {"lat": stops[0]["lat"], "lng": stops[0]["lng"], "ts": None, "atStart": True}
        else:
            row = (t.get("lastPosition") if live and state != "finished" else None) or db.position_at(t["id"], at)
            pos = ({"lat": row["lat"], "lng": row["lng"], "ts": row.get("ts")} if row else None)

        driver = drivers.get(t.get("driverId"), {})
        vehicle = vehicles.get(t.get("vehicleId"), {})
        rides.append({
            "id": t["id"], "label": _label(stops), "state": state,
            "driverId": t.get("driverId"), "driverName": driver.get("name"),
            "vehicleName": vehicle.get("name"), "vehiclePlate": vehicle.get("plate"),
            "vehicleType": t.get("vehicleType", "car"),
            "createdAt": created, "startedAt": started, "endedAt": ended,
            "parcelCount": len(t.get("parcelIds") or []), "parcelIds": list(t.get("parcelIds") or []),
            "delivered": len(delivered_ids), "failed": failed,
            "nextStop": ({"label": nxt.get("place") or nxt.get("label"), "parcel": nxt.get("parcelName")}
                         if nxt else None),
            "nextEta": eta, "position": pos,
            "distanceKm": round((t["route"].get("totalDistanceMeters") or 0) / 1000, 1),
            "polyline": t["route"].get("polyline"),
        })

    order = {"deviating": 0, "en_route": 1, "scheduled": 2, "finished": 3}
    rides.sort(key=lambda r: (order[r["state"]], r["startedAt"] or r["createdAt"]))
    return {
        "at": at, "live": live, "now": now,
        "dayStart": start, "dayEnd": start + 86400,
        "counts": {s: sum(1 for r in rides if r["state"] == s)
                   for s in ("en_route", "deviating", "scheduled", "finished")},
        "rides": rides,
    }


def trail(trip_id: str, until: float | None, max_points: int = 300) -> list[dict]:
    """The path a run had driven up to `until` (downsampled for drawing)."""
    rows = db.positions_until(trip_id, until)
    if len(rows) > max_points:
        step = len(rows) / max_points
        rows = [rows[int(i * step)] for i in range(max_points)] + [rows[-1]]
    return [{"lat": r[0], "lng": r[1]} for r in rows]
