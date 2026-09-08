"""Delivery-management domain: parcels, fleet registry, eligibility, depot.

Run creation lives here too (added with the runs endpoint) so phase 2's
dispatch agent can reuse it programmatically.
"""

import os
import time
import uuid

from . import db, store

SIZES = ["small", "medium", "large"]
SIZE_ORDER = {s: i for i, s in enumerate(SIZES)}

# registry form defaults per vehicle type (editable per record)
VEHICLE_DEFAULTS = {
    "bike": {"maxParcelSize": "small", "capacity": 2},
    "car": {"maxParcelSize": "medium", "capacity": 4},
    "van": {"maxParcelSize": "large", "capacity": 8},
    "truck": {"maxParcelSize": "large", "capacity": 20},
}

# a parcel committed to a run (can't be edited/deleted or picked for a new run)
PARCEL_ACTIVE_STATUSES = ("assigned", "in_transit", "picked_up")
# a parcel that can be selected for a (new) run
PARCEL_PLANNABLE_STATUSES = ("pending", "awaiting_redelivery")


def _new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:6]}"


def stop_kind(stop: dict) -> str:
    """Back-compat: pre-pickup stops have no `kind`."""
    return stop.get("kind") or ("depot" if stop.get("depot") else "delivery")


def new_parcel(data: dict) -> dict:
    parcel = {
        "id": _new_id("p"),
        "name": data["name"],
        "type": data.get("type", "general"),
        "size": data["size"],
        "destination": data["destination"],
        "pickup": data.get("pickup"),      # {lat,lng,label,earliest?,latest?} or None
        "pickedUpAt": None,
        "deadline": data["deadline"],
        "weightKg": data.get("weightKg"),        # optional — kg, or None
        "fragile": bool(data.get("fragile")),
        "recipientPhone": (data.get("recipientPhone") or "").strip() or None,
        "notes": (data.get("notes") or "").strip() or None,
        "status": "pending",
        "deadlineMissed": False,
        "tripId": None,
        "deliveredAt": None,
        "createdAt": time.time(),
    }
    db.save_entity("parcels", parcel)
    return parcel


def new_vehicle(data: dict) -> dict:
    defaults = VEHICLE_DEFAULTS.get(data.get("type", "car"), VEHICLE_DEFAULTS["car"])
    vehicle = {
        "id": _new_id("v"),
        "name": data["name"],
        "plate": data.get("plate", ""),
        "type": data.get("type", "car"),
        "capacity": int(data.get("capacity") or defaults["capacity"]),
        "maxParcelSize": data.get("maxParcelSize") or defaults["maxParcelSize"],
        "createdAt": time.time(),
    }
    db.save_entity("vehicles", vehicle)
    return vehicle


def new_driver(data: dict) -> dict:
    driver = {
        "id": _new_id("d"),
        "name": data["name"],
        "phone": data.get("phone", ""),
        "vehicleId": data.get("vehicleId"),
        "createdAt": time.time(),
    }
    db.save_entity("drivers", driver)
    return driver


def check_eligibility(vehicle: dict, parcels: list[dict]) -> str | None:
    """None if the vehicle can take the parcel set, else a human reason."""
    if len(parcels) > vehicle["capacity"]:
        return f"capacity {vehicle['capacity']} < {len(parcels)} parcels"
    limit = SIZE_ORDER.get(vehicle.get("maxParcelSize", "large"), 2)
    for p in parcels:
        if SIZE_ORDER.get(p["size"], 0) > limit:
            return (f"max parcel size {vehicle.get('maxParcelSize')} "
                    f"< {p['size']} ({p['name']})")
    return None


def entity_in_use(kind: str, entity_id: str) -> bool:
    """Is a vehicle/driver referenced by any not-completed trip?"""
    field = {"vehicles": "vehicleId", "drivers": "driverId"}[kind]
    return any(t.get(field) == entity_id and t["status"] != "completed"
               for t in store.list_trips())


def set_parcel_status(parcel: dict, status: str, trip_id=None, ts=None) -> dict:
    parcel["status"] = status
    if trip_id is not None or status in ("pending", "awaiting_redelivery"):
        parcel["tripId"] = trip_id
    if status == "picked_up":
        parcel["pickedUpAt"] = ts or time.time()
    if status == "delivered":
        parcel["deliveredAt"] = ts or time.time()
        parcel["deadlineMissed"] = parcel["deliveredAt"] > parcel["deadline"]
    db.save_entity("parcels", parcel)
    store.broadcast_global("parcel", {"parcel": parcel, "deleted": False})
    return parcel


def flag_overdue(parcels: list[dict]) -> list[dict]:
    """Lazily mark undelivered, overdue parcels; persists + broadcasts changes."""
    now = time.time()
    for p in parcels:
        if p["status"] != "delivered" and not p["deadlineMissed"] and now > p["deadline"]:
            p["deadlineMissed"] = True
            db.save_entity("parcels", p)
            store.broadcast_global("parcel", {"parcel": p, "deleted": False})
    return parcels


def compute_planned_etas(route: dict, parcels_by_id: dict, departure_ts: float) -> list[dict]:
    """Per parcel-stop ETA (cumulative leg durations). Delivery stops are at
    risk when the ETA is past the deadline; pickup stops when it falls outside
    the parcel's collection window."""
    etas, elapsed = [], 0.0
    for i, stop in enumerate(route["orderedStops"]):
        if i > 0:
            elapsed += route["legs"][i - 1]["durationSeconds"]
        pid = stop.get("parcelId")
        if not pid or pid not in parcels_by_id:
            continue
        p = parcels_by_id[pid]
        eta = departure_ts + elapsed
        if stop_kind(stop) == "pickup":
            w = p.get("pickup") or {}
            earliest, latest = w.get("earliest"), w.get("latest")
            at_risk = (latest is not None and eta > latest) or \
                      (earliest is not None and eta < earliest)
            etas.append({"stopIndex": i, "parcelId": pid, "kind": "pickup",
                         "eta": eta, "earliest": earliest, "latest": latest,
                         "atRisk": at_risk})
        else:
            etas.append({"stopIndex": i, "parcelId": pid, "kind": "delivery",
                         "eta": eta, "deadline": p["deadline"],
                         "atRisk": eta > p["deadline"]})
    return etas


def build_run_stops(parcels: list[dict]) -> list[dict]:
    """Depot + a stop per parcel action. A parcel with a pickup contributes a
    collect stop *and* a delivery stop (both carrying its parcelId + a `kind`);
    everything else is one delivery stop loaded at the depot. A parcel in
    `awaiting_redelivery` was already collected and returned to the depot, so it
    gets no pickup stop. Shared by create_run and the dispatch evaluate_route
    tool."""
    depot = get_depot()
    stops = [{**depot, "depot": True, "kind": "depot"}]
    for p in parcels:
        pk = p.get("pickup")
        if pk and p["status"] != "awaiting_redelivery":
            stops.append({"lat": pk["lat"], "lng": pk["lng"], "label": pk["label"],
                          "parcelId": p["id"], "kind": "pickup",
                          "earliest": pk.get("earliest"), "latest": pk.get("latest")})
        stops.append({"lat": p["destination"]["lat"], "lng": p["destination"]["lng"],
                      "label": p["name"], "parcelId": p["id"], "kind": "delivery",
                      "deadline": p["deadline"]})
    return stops


def route_has_pickup(route: dict, parcel_id: str) -> bool:
    """Does this run's route collect the given parcel en route?"""
    return any(stop_kind(s) == "pickup" and s.get("parcelId") == parcel_id
              for s in route["orderedStops"])


class RunError(Exception):
    def __init__(self, message: str, code: int = 409):
        super().__init__(message)
        self.code = code


async def create_run(parcel_ids: list[str], driver_id: str,
                     avoid_tolls: bool = False) -> dict:
    """Create a delivery run: depot → optimized parcel drop-offs, planned by
    the existing ADK planner. Factored here so phase 2's dispatch agent can
    call it programmatically."""
    from .planner.service import plan_route  # late import: avoids ADK at module load

    driver = db.load_entity("drivers", driver_id)
    if not driver:
        raise RunError("Driver not found", 404)
    vehicle = db.load_entity("vehicles", driver.get("vehicleId") or "")
    if not vehicle:
        raise RunError("Driver has no vehicle assigned")

    parcels = []
    for pid in parcel_ids:
        p = db.load_entity("parcels", pid)
        if not p:
            raise RunError(f"Parcel {pid} not found", 404)
        if p["status"] not in PARCEL_PLANNABLE_STATUSES:
            raise RunError(f"Parcel '{p['name']}' is not available to plan")
        parcels.append(p)
    if not parcels:
        raise RunError("Select at least one parcel", 400)

    reason = check_eligibility(vehicle, parcels)
    if reason:
        raise RunError(f"{vehicle['name']} not eligible: {reason}")

    stops = build_run_stops(parcels)

    route = await plan_route(stops, avoid_tolls=avoid_tolls,
                             vehicle_type=vehicle["type"])
    trip = store.new_trip(stops, route, avoid_tolls=avoid_tolls,
                          vehicle_type=vehicle["type"])
    parcels_by_id = {p["id"]: p for p in parcels}
    trip.update({
        "vehicleId": vehicle["id"],
        "driverId": driver["id"],
        "parcelIds": [p["id"] for p in parcels],
        "plannedEtaPerStop": compute_planned_etas(route, parcels_by_id, time.time()),
    })
    store.save_trip(trip)
    for p in parcels:
        set_parcel_status(p, "assigned", trip_id=trip["id"])
    return trip


def get_depot() -> dict:
    depot = db.get_setting("depot")
    if not depot:
        depot = {
            "lat": float(os.environ.get("DEPOT_LAT", "6.9344")),
            "lng": float(os.environ.get("DEPOT_LNG", "79.8428")),
            "label": os.environ.get("DEPOT_LABEL", "Colombo Depot"),
        }
        db.set_setting("depot", depot)
    return depot
