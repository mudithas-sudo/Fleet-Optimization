"""Fleet Transportation Planning demo — FastAPI backend."""

import asyncio
import os
import time
import uuid

from dotenv import load_dotenv

load_dotenv()  # must run before app modules read env vars

from fastapi import FastAPI, HTTPException  # noqa: E402
from fastapi.responses import RedirectResponse  # noqa: E402
from fastapi.staticfiles import StaticFiles  # noqa: E402
import httpx  # noqa: E402
from pydantic import BaseModel  # noqa: E402
from sse_starlette.sse import EventSourceResponse  # noqa: E402

from . import db, delivery, deviation, routing, store  # noqa: E402
from .planner.service import plan_route  # noqa: E402

app = FastAPI(title="Fleet Transportation Planning")

REROUTE_MIN_GAP_S = 10.0


class Stop(BaseModel):
    lat: float
    lng: float
    label: str


class PlanRequest(BaseModel):
    stops: list[Stop]
    avoidTolls: bool = False
    vehicleType: str = "car"


class Position(BaseModel):
    lat: float
    lng: float
    ts: float | None = None


class EndRequest(BaseModel):
    arrived: bool = False


def _lifecycle_alert(trip_id: str, kind: str, message: str) -> dict:
    return {
        "id": f"a-{uuid.uuid4().hex[:6]}",
        "tripId": trip_id,
        "type": kind,
        "distanceMeters": 0,
        "position": None,
        "ts": time.time(),
        "message": message,
    }


@app.get("/")
async def index():
    return RedirectResponse("/static/admin.html")


@app.get("/driver")
async def driver_page():
    return RedirectResponse("/static/driver.html")


@app.get("/api/config")
async def config():
    return {"mapsApiKey": os.environ.get("MAPS_API_KEY", "")}


# Place search uses the Places API (New) — like Routes v2 it works on this key,
# while the legacy Geocoding API requires billing.

@app.get("/api/geocode")
async def geocode(q: str):
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.post(
            "https://places.googleapis.com/v1/places:searchText",
            json={"textQuery": q, "pageSize": 1},
            headers={
                "X-Goog-Api-Key": os.environ.get("MAPS_SERVER_KEY") or os.environ["MAPS_API_KEY"],
                "X-Goog-FieldMask": "places.displayName,places.location",
            })
    places = resp.json().get("places") if resp.status_code == 200 else None
    if not places:
        raise HTTPException(404, f"No match for '{q}'")
    top = places[0]
    return {"label": top["displayName"]["text"],
            "lat": top["location"]["latitude"],
            "lng": top["location"]["longitude"]}


@app.get("/api/revgeocode")
async def revgeocode(lat: float, lng: float):
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.post(
            "https://places.googleapis.com/v1/places:searchNearby",
            json={
                "locationRestriction": {"circle": {
                    "center": {"latitude": lat, "longitude": lng},
                    "radius": 150.0,
                }},
                "maxResultCount": 1,
                "rankPreference": "DISTANCE",
            },
            headers={
                "X-Goog-Api-Key": os.environ.get("MAPS_SERVER_KEY") or os.environ["MAPS_API_KEY"],
                "X-Goog-FieldMask": "places.displayName",
            })
    places = resp.json().get("places") if resp.status_code == 200 else None
    return {"label": places[0]["displayName"]["text"] if places else None}


# ---------- delivery management: parcels / vehicles / drivers / depot ----------

class Pickup(BaseModel):
    lat: float
    lng: float
    label: str
    earliest: float | None = None   # epoch secs — collect no earlier than
    latest: float | None = None     # epoch secs — collect no later than


class ParcelIn(BaseModel):
    name: str
    type: str = "general"
    size: str
    destination: Stop
    pickup: Pickup | None = None
    deadline: float


class BulkParcels(BaseModel):
    parcels: list[ParcelIn]


class VehicleIn(BaseModel):
    name: str
    plate: str = ""
    type: str = "car"
    capacity: int | None = None
    maxParcelSize: str | None = None


class DriverIn(BaseModel):
    name: str
    phone: str = ""
    vehicleId: str | None = None


def _entity_or_404(table: str, entity_id: str) -> dict:
    obj = db.load_entity(table, entity_id)
    if not obj:
        raise HTTPException(404, f"{table[:-1]} not found")
    return obj


def _validate_parcel(p: ParcelIn):
    if p.size not in delivery.SIZE_ORDER:
        raise HTTPException(400, f"size must be one of {delivery.SIZES}")


@app.get("/api/parcels")
async def list_parcels():
    return delivery.flag_overdue(db.all_entities("parcels"))


@app.post("/api/parcels")
async def add_parcel(p: ParcelIn):
    _validate_parcel(p)
    parcel = delivery.new_parcel(p.model_dump())
    store.broadcast_global("parcel", {"parcel": parcel, "deleted": False})
    return parcel


@app.post("/api/parcels/bulk")
async def bulk_parcels(req: BulkParcels):
    created, errors = [], []
    for i, p in enumerate(req.parcels):
        try:
            _validate_parcel(p)
            parcel = delivery.new_parcel(p.model_dump())
            store.broadcast_global("parcel", {"parcel": parcel, "deleted": False})
            created.append(parcel)
        except HTTPException as e:
            errors.append({"index": i, "message": str(e.detail)})
    return {"created": created, "errors": errors}


@app.put("/api/parcels/{parcel_id}")
async def edit_parcel(parcel_id: str, p: ParcelIn):
    parcel = _entity_or_404("parcels", parcel_id)
    if parcel["status"] not in delivery.PARCEL_PLANNABLE_STATUSES:
        raise HTTPException(409, "This parcel is on a run — it can't be edited.")
    _validate_parcel(p)
    parcel.update(p.model_dump())
    parcel["deadlineMissed"] = False
    db.save_entity("parcels", parcel)
    store.broadcast_global("parcel", {"parcel": parcel, "deleted": False})
    return parcel


@app.delete("/api/parcels/{parcel_id}")
async def delete_parcel(parcel_id: str):
    parcel = _entity_or_404("parcels", parcel_id)
    if parcel["status"] not in delivery.PARCEL_PLANNABLE_STATUSES:
        raise HTTPException(409, "Parcel is on a delivery run — it can't be deleted.")
    db.delete_entity("parcels", parcel_id)
    store.broadcast_global("parcel", {"parcel": parcel, "deleted": True})
    return {"ok": True}


@app.get("/api/vehicles")
async def list_vehicles():
    return db.all_entities("vehicles")


@app.post("/api/vehicles")
async def add_vehicle(v: VehicleIn):
    return delivery.new_vehicle(v.model_dump())


@app.put("/api/vehicles/{vehicle_id}")
async def edit_vehicle(vehicle_id: str, v: VehicleIn):
    vehicle = _entity_or_404("vehicles", vehicle_id)
    if delivery.entity_in_use("vehicles", vehicle_id):
        raise HTTPException(409, "Vehicle is on a delivery run — end it first.")
    vehicle.update({k: val for k, val in v.model_dump().items() if val is not None})
    db.save_entity("vehicles", vehicle)
    return vehicle


@app.delete("/api/vehicles/{vehicle_id}")
async def delete_vehicle(vehicle_id: str):
    _entity_or_404("vehicles", vehicle_id)
    if delivery.entity_in_use("vehicles", vehicle_id):
        raise HTTPException(409, "Vehicle is on a delivery run — end it first.")
    if any(d.get("vehicleId") == vehicle_id for d in db.all_entities("drivers")):
        raise HTTPException(409, "A driver is assigned to this vehicle — reassign them first.")
    db.delete_entity("vehicles", vehicle_id)
    return {"ok": True}


@app.get("/api/drivers")
async def list_drivers():
    return db.all_entities("drivers")


@app.post("/api/drivers")
async def add_driver(d: DriverIn):
    if d.vehicleId:
        _entity_or_404("vehicles", d.vehicleId)
    return delivery.new_driver(d.model_dump())


@app.put("/api/drivers/{driver_id}")
async def edit_driver(driver_id: str, d: DriverIn):
    driver = _entity_or_404("drivers", driver_id)
    if delivery.entity_in_use("drivers", driver_id):
        raise HTTPException(409, "Driver is on a delivery run — end it first.")
    if d.vehicleId:
        _entity_or_404("vehicles", d.vehicleId)
    driver.update(d.model_dump())
    db.save_entity("drivers", driver)
    return driver


@app.delete("/api/drivers/{driver_id}")
async def delete_driver(driver_id: str):
    _entity_or_404("drivers", driver_id)
    if delivery.entity_in_use("drivers", driver_id):
        raise HTTPException(409, "Driver is on a delivery run — end it first.")
    db.delete_entity("drivers", driver_id)
    return {"ok": True}


@app.get("/api/settings/depot")
async def get_depot():
    return delivery.get_depot()


@app.put("/api/settings/depot")
async def set_depot(depot: Stop):
    db.set_setting("depot", depot.model_dump())
    return depot.model_dump()


class RunRequest(BaseModel):
    parcelIds: list[str]
    driverId: str
    avoidTolls: bool = False


class ProposeRequest(BaseModel):
    parcelIds: list[str] | None = None


class ApproveBatch(BaseModel):
    driverId: str
    parcelIds: list[str]


class ApproveRequest(BaseModel):
    batches: list[ApproveBatch]
    avoidTolls: bool = False


@app.post("/api/dispatch/propose")
async def dispatch_propose(req: ProposeRequest):
    from .dispatcher.service import propose_dispatch
    try:
        return await propose_dispatch(req.parcelIds or None)
    except RuntimeError as e:
        raise HTTPException(502, str(e))


@app.post("/api/dispatch/approve")
async def dispatch_approve(req: ApproveRequest):
    created, errors = [], []
    for i, b in enumerate(req.batches):
        try:
            trip = await delivery.create_run(b.parcelIds, b.driverId, req.avoidTolls)
            store.broadcast(trip["id"], "trip", _summary(trip))
            created.append(trip["id"])
        except (delivery.RunError, RuntimeError) as e:
            errors.append({"index": i, "message": str(e)})
    return {"created": created, "errors": errors}


@app.post("/api/runs")
async def create_run(req: RunRequest):
    try:
        trip = await delivery.create_run(req.parcelIds, req.driverId, req.avoidTolls)
    except delivery.RunError as e:
        raise HTTPException(e.code, str(e))
    except RuntimeError as e:
        raise HTTPException(502, str(e))
    store.broadcast(trip["id"], "trip", _summary(trip))
    return trip


@app.get("/api/drivers/{driver_id}/queue")
async def driver_queue(driver_id: str):
    driver = _entity_or_404("drivers", driver_id)
    vehicle = db.load_entity("vehicles", driver.get("vehicleId") or "")
    mine = [t for t in store.list_trips() if t.get("driverId") == driver_id]
    active = [t for t in mine if t["status"] in ("active", "deviating")]
    planned = sorted([t for t in mine if t["status"] == "planned"],
                     key=lambda t: t["createdAt"])
    done = sorted([t for t in mine if t["status"] == "completed"],
                  key=lambda t: t.get("endedAt") or 0, reverse=True)[:3]
    return {"driver": driver, "vehicle": vehicle,
            "runs": [_summary(t) for t in active + planned + done]}


def _trip_or_404(trip_id: str) -> dict:
    trip = store.get_trip(trip_id)
    if not trip:
        raise HTTPException(404, "Trip not found")
    return trip


def _summary(t: dict) -> dict:
    stops = t["route"]["orderedStops"]
    return {
        "id": t["id"],
        "status": t["status"],
        "label": f"{stops[0]['label']} → {stops[-1]['label']}",
        "stopCount": len(stops),
        "createdAt": t["createdAt"],
        "totalDistanceMeters": t["route"]["totalDistanceMeters"],
        "totalDurationSeconds": t["route"]["totalDurationSeconds"],
        "deviations": sum(1 for a in t["alerts"] if a["type"] == "deviation"),
        "lastPosition": t["lastPosition"],
        "routeVersion": t["routeVersion"],
        "vehicleType": t.get("vehicleType", "car"),
        "driverId": t.get("driverId"),
        "vehicleId": t.get("vehicleId"),
        "driverName": (db.load_entity("drivers", t["driverId"]) or {}).get("name")
                      if t.get("driverId") else None,
        "parcelCount": len(t.get("parcelIds") or []),
        "deliveredCount": t.get("deliveredCount", 0),
        "collectedCount": t.get("collectedCount", 0),
    }


@app.post("/api/trips/plan")
async def plan_trip(req: PlanRequest):
    if len(req.stops) < 2:
        raise HTTPException(400, "Pick at least a start and an end point.")
    try:
        route = await plan_route([s.model_dump() for s in req.stops],
                                 avoid_tolls=req.avoidTolls,
                                 vehicle_type=req.vehicleType)
    except RuntimeError as e:
        raise HTTPException(502, str(e))
    trip = store.new_trip([s.model_dump() for s in req.stops], route,
                          avoid_tolls=req.avoidTolls,
                          vehicle_type=req.vehicleType)
    store.broadcast(trip["id"], "trip", _summary(trip))
    return trip


@app.get("/api/trips")
async def list_trips():
    return [_summary(t) for t in store.list_trips()]


@app.post("/api/trips/{trip_id}/replan")
async def replan_trip(trip_id: str, req: PlanRequest):
    """Recompute an existing trip in place — the id and driver link survive."""
    trip = _trip_or_404(trip_id)
    if trip["status"] in LIVE_STATUSES:
        raise HTTPException(
            409, "Trip is live — the driver has started it. End the trip first.")
    if len(req.stops) < 2:
        raise HTTPException(400, "Pick at least a start and an end point.")
    try:
        route = await plan_route([s.model_dump() for s in req.stops],
                                 avoid_tolls=req.avoidTolls,
                                 vehicle_type=req.vehicleType)
    except RuntimeError as e:
        raise HTTPException(502, str(e))
    trip.update({
        "stops": [s.model_dump() for s in req.stops],
        "route": route,
        "avoidTolls": req.avoidTolls,
        "vehicleType": req.vehicleType,
        "status": "planned",
        "startedAt": None,
        "endedAt": None,
        "lastPosition": None,
        "alerts": [],
        "routeVersion": trip["routeVersion"] + 1,
    })
    store.reset_trip_runtime(trip_id)
    store.save_trip(trip)
    store.broadcast(trip_id, "trip", _summary(trip))
    store.broadcast(trip_id, "reroute", {"routeVersion": trip["routeVersion"]})
    store.broadcast(trip_id, "status", {"status": "planned"})
    return trip


LIVE_STATUSES = ("active", "deviating")


@app.delete("/api/trips/{trip_id}")
async def delete_trip(trip_id: str):
    trip = _trip_or_404(trip_id)
    if trip["status"] in LIVE_STATUSES:
        raise HTTPException(
            409, "Trip is live — the driver has started it. End the trip first.")
    # deleting a planned/ended run releases its parcels for re-dispatch;
    # already-collected parcels come back as awaiting_redelivery
    for pid in trip.get("parcelIds") or []:
        p = db.load_entity("parcels", pid)
        if not p:
            continue
        if p["status"] == "picked_up":
            delivery.set_parcel_status(p, "awaiting_redelivery", trip_id=None)
        elif p["status"] in delivery.PARCEL_ACTIVE_STATUSES:
            delivery.set_parcel_status(p, "pending", trip_id=None)
    store.broadcast(trip_id, "removed", {})
    store.delete_trip(trip_id)
    return {"ok": True}


@app.delete("/api/trips")
async def clear_trips():
    deleted = skipped = 0
    for t in store.list_trips():
        if t["status"] in LIVE_STATUSES:
            skipped += 1
            continue
        store.broadcast(t["id"], "removed", {})
        store.delete_trip(t["id"])
        deleted += 1
    return {"ok": True, "deleted": deleted, "skippedLive": skipped}


@app.get("/api/trips/{trip_id}")
async def get_trip(trip_id: str):
    trip = _trip_or_404(trip_id)
    trip["actualDistanceMeters"] = round(store.actual_distance_m(trip_id))
    return trip


@app.post("/api/trips/{trip_id}/start")
async def start_trip(trip_id: str):
    trip = _trip_or_404(trip_id)
    trip["status"] = "active"
    trip["startedAt"] = time.time()
    store.runtime(trip_id).update(consecutive=0, alerting=False, along=0.0)
    alert = _lifecycle_alert(trip_id, "started", "Driver started the trip")
    trip["alerts"].append(alert)
    for pid in trip.get("parcelIds") or []:
        p = db.load_entity("parcels", pid)
        if p and p["status"] == "assigned":
            delivery.set_parcel_status(p, "in_transit", trip_id=trip_id)
    store.save_trip(trip)
    store.broadcast(trip_id, "alert", alert)
    store.broadcast(trip_id, "status", {"status": "active"})
    return {"ok": True}


@app.post("/api/trips/{trip_id}/end")
async def end_trip(trip_id: str, req: EndRequest | None = None):
    trip = _trip_or_404(trip_id)
    trip["status"] = "completed"
    trip["endedAt"] = time.time()
    arrived = bool(req and req.arrived)
    alert = _lifecycle_alert(
        trip_id,
        "completed" if arrived else "ended",
        "Driver arrived — trip completed" if arrived else "Trip ended by the driver")
    trip["alerts"].append(alert)
    # arrived: any parcels not yet ticked off are delivered at the final stop.
    # manual end: not-yet-collected parcels go back to pending; already-collected
    # ones come back as awaiting_redelivery (assumed returned to the depot).
    for pid in trip.get("parcelIds") or []:
        p = db.load_entity("parcels", pid)
        if not p or p["status"] == "delivered":
            continue
        if arrived:
            delivery.set_parcel_status(p, "delivered")
            trip["deliveredCount"] = trip.get("deliveredCount", 0) + 1
        elif p["status"] == "picked_up":
            delivery.set_parcel_status(p, "awaiting_redelivery", trip_id=None)
        else:
            delivery.set_parcel_status(p, "pending", trip_id=None)
    store.save_trip(trip)
    store.broadcast(trip_id, "alert", alert)
    store.broadcast(trip_id, "status", {"status": "completed"})
    return {"ok": True}


@app.post("/api/trips/{trip_id}/position")
async def post_position(trip_id: str, pos: Position):
    trip = _trip_or_404(trip_id)
    ts = pos.ts or time.time()
    point = {"lat": pos.lat, "lng": pos.lng, "ts": ts}
    trip["lastPosition"] = point
    store.log_position(trip_id, pos.lat, pos.lng, ts)
    store.broadcast(trip_id, "position", point)

    runtime = store.runtime(trip_id)
    alert, along = deviation.check_position(trip, runtime, pos.lat, pos.lng)
    _check_stop_progress(trip, runtime)
    if alert:
        trip["alerts"].append(alert)
        trip["status"] = "deviating" if alert["type"] == "deviation" else "active"
        store.broadcast(trip_id, "alert", alert)
        store.broadcast(trip_id, "status", {"status": trip["status"]})
        if alert["type"] == "deviation":
            _maybe_reroute(trip, runtime, pos.lat, pos.lng, along)
    store.save_trip(trip)
    return {"ok": True, "alert": alert, "routeVersion": trip["routeVersion"]}


ARRIVE_RADIUS_M = 40


def _check_stop_progress(trip: dict, runtime: dict):
    """As the vehicle's on-route progress passes each parcel stop, mark the
    parcel collected (pickup stop) or delivered (delivery stop) — leg-boundary
    cums vs runtime 'along', plus a proximity confirmation so a window-fallback
    projection can't fake it."""
    if not trip.get("parcelIds") or trip["status"] not in ("active", "deviating"):
        return
    bounds, cum = [0.0], 0.0
    for leg in trip["route"]["legs"]:
        cum += leg["distanceMeters"]
        bounds.append(cum)
    route, pos = trip["route"], trip.get("lastPosition") or {}
    for i, stop in enumerate(route["orderedStops"]):
        pid = stop.get("parcelId")
        if i == 0 or not pid or runtime["along"] < bounds[i] - ARRIVE_RADIUS_M:
            continue
        # confirmation against window-fallback projection jumps: either the
        # vehicle is near the stop right now, or its on-route progress is
        # clearly past it (position samples may straddle the stop itself)
        near = pos and routing._haversine_m(pos, stop) <= 300
        well_past = runtime["along"] >= bounds[i] + 300
        if not (near or well_past):
            continue
        p = db.load_entity("parcels", pid)
        if not p:
            continue
        kind = delivery.stop_kind(stop)
        if kind == "pickup" and p["status"] == "in_transit":
            delivery.set_parcel_status(p, "picked_up")
            trip["collectedCount"] = trip.get("collectedCount", 0) + 1
            _emit(trip, "collected", f"Collected: {p['name']}")
        elif kind == "delivery":
            # a parcel collected en route must be picked up before it's dropped
            if delivery.route_has_pickup(route, pid) and p["status"] != "picked_up":
                continue
            if p["status"] not in ("in_transit", "picked_up"):
                continue
            delivery.set_parcel_status(p, "delivered")
            trip["deliveredCount"] = trip.get("deliveredCount", 0) + 1
            late = " (past deadline)" if p["deadlineMissed"] else ""
            _emit(trip, "delivered", f"Delivered: {p['name']}{late}")


def _emit(trip: dict, kind: str, message: str):
    alert = _lifecycle_alert(trip["id"], kind, message)
    trip["alerts"].append(alert)
    store.broadcast(trip["id"], "alert", alert)


def _maybe_reroute(trip: dict, runtime: dict, lat: float, lng: float, along: float):
    if runtime["rerouting"] or time.time() - runtime["last_reroute"] < REROUTE_MIN_GAP_S:
        return
    # which stops are still ahead? leg boundaries give each stop's position
    # along the route; keep every stop clearly beyond the projected position
    bounds, cum = [0.0], 0.0
    for leg in trip["route"]["legs"]:
        cum += leg["distanceMeters"]
        bounds.append(cum)
    remaining = [s for i, s in enumerate(trip["route"]["orderedStops"])
                 if i > 0 and bounds[i] > along + 100]
    if not remaining:
        return
    runtime["rerouting"] = True
    asyncio.create_task(_do_reroute(trip["id"], lat, lng, remaining))


async def _do_reroute(trip_id: str, lat: float, lng: float, remaining: list[dict]):
    runtime = store.runtime(trip_id)
    try:
        stops = [{"lat": lat, "lng": lng, "label": "Current location"}, *remaining]
        prior = store.get_trip(trip_id)
        route = await routing.compute_route(
            stops, avoid_tolls=prior.get("avoidTolls", False),
            optimize=False, vehicle_type=prior.get("vehicleType", "car"),
            presequenced=True)   # `remaining` keeps the planned (valid) order
    except routing.RoutingError:
        return  # keep the old route; deviation alerts continue
    finally:
        runtime["rerouting"] = False
        runtime["last_reroute"] = time.time()

    trip = store.get_trip(trip_id)
    if not trip or trip["status"] == "completed":
        return
    briefing = trip["route"].get("briefing", "")
    trip["route"] = {**route, "briefing": briefing}
    trip["routeVersion"] += 1
    trip["status"] = "active"
    runtime.update(consecutive=0, alerting=False, along=0.0)
    alert = {
        "id": f"a-rr{trip['routeVersion']}",
        "tripId": trip_id,
        "type": "reroute",
        "distanceMeters": 0,
        "position": {"lat": lat, "lng": lng},
        "ts": time.time(),
        "message": f"Route recalculated from current position "
                   f"({route['totalDistanceMeters'] / 1000:.1f} km remaining)",
    }
    trip["alerts"].append(alert)
    store.save_trip(trip)
    store.broadcast(trip_id, "alert", alert)
    store.broadcast(trip_id, "reroute", {"routeVersion": trip["routeVersion"]})
    store.broadcast(trip_id, "status", {"status": "active"})


def _stream(trip_id: str | None):
    async def events():
        q = store.subscribe(trip_id)
        try:
            while True:
                yield await q.get()
        finally:
            store.unsubscribe(trip_id, q)
    return EventSourceResponse(events(), ping=15)


@app.get("/api/stream")
async def stream_all():
    return _stream(None)


@app.get("/api/trips/{trip_id}/stream")
async def stream_trip(trip_id: str):
    _trip_or_404(trip_id)
    return _stream(trip_id)


app.mount("/static", StaticFiles(directory="static"), name="static")
