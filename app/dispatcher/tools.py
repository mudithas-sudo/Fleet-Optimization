"""Dispatch agent tools: the fleet's world state and route evaluation.

Returns are compact (minutes not epochs, 4-dp coords, no geometry) — they go
into the model's context. All authority stays in code: eligibility is checked
deterministically and ETAs come from the Routes API via routing.compute_route.
"""

import json
import time

from google.adk.tools import ToolContext

from .. import db, delivery, routing

MAX_PARCELS = 25


def list_pending_parcels(tool_context: ToolContext) -> dict:
    """List the parcels waiting to be dispatched.

    Returns:
        dict with `parcels`: rows of {id, name, size, destinationLabel, lat,
        lng, deadlineInMinutes} (negative deadlineInMinutes = already overdue),
        sorted most-urgent first.
    """
    scope = tool_context.state.get("scope_parcel_ids")
    now = time.time()
    rows = []
    for p in delivery.flag_overdue(db.all_entities("parcels")):
        if p["status"] not in delivery.PARCEL_PLANNABLE_STATUSES:
            continue
        if scope and p["id"] not in scope:
            continue
        pk = p.get("pickup") if p["status"] != "awaiting_redelivery" else None
        rows.append({
            "id": p["id"],
            "name": p["name"],
            "size": p["size"],
            "destinationLabel": p["destination"]["label"],
            "lat": round(p["destination"]["lat"], 4),
            "lng": round(p["destination"]["lng"], 4),
            "deadlineInMinutes": round((p["deadline"] - now) / 60),
            "pickup": ({"label": pk["label"],
                        "lat": round(pk["lat"], 4), "lng": round(pk["lng"], 4)}
                       if pk else None),
        })
    rows.sort(key=lambda r: r["deadlineInMinutes"])
    return {"parcels": rows[:MAX_PARCELS], "count": len(rows[:MAX_PARCELS])}


def list_fleet_availability(tool_context: ToolContext) -> dict:
    """List drivers with their vehicle constraints and current workload.

    Returns:
        dict with `drivers`: rows of {driverId, name, vehicleType, capacity,
        maxParcelSize, queueDepth, busyForMinutes} — queueDepth is how many
        not-yet-completed runs they already have; busyForMinutes estimates how
        long until they are free (0 = idle now).
    """
    from .. import store
    trips = [t for t in store.list_trips() if t["status"] != "completed"]
    now = time.time()
    rows = []
    for d in db.all_entities("drivers"):
        v = db.load_entity("vehicles", d.get("vehicleId") or "")
        if not v:
            continue
        mine = [t for t in trips if t.get("driverId") == d["id"]]
        busy_until = max(
            (e["eta"] for t in mine for e in t.get("plannedEtaPerStop") or []),
            default=now)
        rows.append({
            "driverId": d["id"],
            "name": d["name"],
            "vehicleType": v["type"],
            "capacity": v["capacity"],
            "maxParcelSize": v["maxParcelSize"],
            "queueDepth": len(mine),
            "busyForMinutes": max(0, round((busy_until - now) / 60)),
        })
    return {"drivers": rows}


async def evaluate_route(driver_id: str, parcel_ids_json: str,
                         tool_context: ToolContext) -> dict:
    """Evaluate a candidate batch: can this driver deliver these parcels, and
    what would the route look like?

    Args:
        driver_id: the driver to evaluate.
        parcel_ids_json: JSON array of parcel ids for the batch.

    Returns:
        {status:"ineligible", reason} if the vehicle can't take the batch;
        else {status:"ok", totalKm, totalMinutes, perParcel:[{parcelId,
        etaInMinutes, deadlineInMinutes, atRisk}]} — these ETAs are the only
        valid timing source.
    """
    driver = db.load_entity("drivers", driver_id)
    if not driver:
        return {"status": "error", "message": f"unknown driver {driver_id}"}
    vehicle = db.load_entity("vehicles", driver.get("vehicleId") or "")
    if not vehicle:
        return {"status": "ineligible", "reason": "driver has no vehicle"}

    parcels = []
    for pid in json.loads(parcel_ids_json):
        p = db.load_entity("parcels", pid)
        if not p or p["status"] != "pending":
            return {"status": "error", "message": f"parcel {pid} not pending"}
        parcels.append(p)
    if not parcels:
        return {"status": "error", "message": "empty batch"}

    reason = delivery.check_eligibility(vehicle, parcels)
    if reason:
        return {"status": "ineligible", "reason": reason}

    try:
        route = await routing.compute_route(
            delivery.build_run_stops(parcels), vehicle_type=vehicle["type"])
    except routing.RoutingError as e:
        return {"status": "error", "message": str(e)}

    now = time.time()
    etas = delivery.compute_planned_etas(route, {p["id"]: p for p in parcels}, now)
    return {
        "status": "ok",
        "totalKm": round(route["totalDistanceMeters"] / 1000, 1),
        "totalMinutes": round(route["totalDurationSeconds"] / 60),
        "perParcel": [{
            "parcelId": e["parcelId"],
            "etaInMinutes": round((e["eta"] - now) / 60),
            "deadlineInMinutes": round((e["deadline"] - now) / 60),
            "atRisk": e["atRisk"],
        } for e in etas],
    }
