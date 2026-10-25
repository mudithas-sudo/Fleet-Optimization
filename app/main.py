"""Fleet Transportation Planning demo — FastAPI backend."""

import asyncio
import re
import logging
import os
import time
import uuid

from dotenv import load_dotenv

load_dotenv()  # must run before app modules read env vars

from fastapi import FastAPI, HTTPException, Request  # noqa: E402
from fastapi.responses import RedirectResponse, Response  # noqa: E402
from fastapi.staticfiles import StaticFiles  # noqa: E402
import httpx  # noqa: E402
from pydantic import BaseModel  # noqa: E402
from sse_starlette.sse import EventSourceResponse  # noqa: E402

from . import auth, cod, db, delivery, deviation, disruptions, impact, notify, replan, reports, risk, rides, scorecard, route_templates, routing, store, warehouse  # noqa: E402
from .planner.service import plan_route  # noqa: E402

app = FastAPI(title="Fleet Transportation Planning")
app.middleware("http")(auth.middleware)
app.include_router(auth.router)
auth.bootstrap_admin()

log = logging.getLogger("fleetops.risk")
if not log.handlers:
    _h = logging.StreamHandler()
    _h.setFormatter(logging.Formatter("%(asctime)s  RISK  %(message)s", "%H:%M:%S"))
    log.addHandler(_h)
    log.setLevel(logging.INFO)
    log.propagate = False

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
    stopped: bool = False   # driver deliberately halted (sim Stop button) — not traffic


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


class Dims(BaseModel):
    l: float     # cm
    w: float
    h: float


class ParcelIn(BaseModel):
    name: str
    requestType: str = "delivery"       # delivery | pickup (customer asks us to collect; destination is the hub)
    earlyOk: bool = False               # pickup: ready and the customer confirmed early collection
    priority: str = "standard"          # standard | priority | express
    windowStart: float | None = None    # earliest delivery time (epoch secs)
    receiverEmail: str | None = None    # optional — used for tracking notifications
    trackingNumber: str | None = None   # blank → generated (FO-XXXXXXXX)
    receiverName: str | None = None
    dimensionsCm: Dims | None = None    # optional — package L×W×H in cm
    type: str = "general"
    size: str
    destination: Stop | None = None     # not needed for a pickup request: it comes back to the hub
    pickup: Pickup | None = None        # pickup request: earliest = customer cut-off, latest = collect by
    deadline: float | None = None       # not needed for a pickup request: the collect-by time
    weightKg: float | None = None       # optional — parcel weight in kilograms
    fragile: bool = False               # optional — flag for careful handling
    recipientPhone: str | None = None   # optional — contact at the destination
    notes: str | None = None            # optional — delivery instructions
    codAmount: float | None = None      # optional — cash the rider must collect at the door
    merchant: str | None = None         # optional — the seller the cash is owed to
    landmark: str | None = None         # optional — directions or a landmark near the door


class PodIn(BaseModel):
    method: str                          # handed_over | signature | photo | left_safe
    receivedBy: str | None = None
    note: str | None = None
    reference: str | None = None         # receipt / e-signature id from the existing signature process
    signature: str | None = None         # optional data URL (png); the driver app doesn't capture it any more
    photo: str | None = None             # data URL (jpeg/webp), downscaled client-side
    codCollected: float | None = None    # cash taken at the door (required when the parcel is cash on delivery)
    lat: float | None = None
    lng: float | None = None
    by: str = "driver"


class FailIn(BaseModel):
    reason: str
    note: str | None = None
    retryAt: float | None = None         # new deadline for the next attempt (epoch secs)
    by: str = "driver"


class ReviewIn(BaseModel):
    decision: str                        # reschedule | return
    note: str | None = None              # what the review found / what the customer asked for
    scheduledFor: float | None = None    # new delivery (or pickup cut-off) time — required to reschedule
    destination: Stop | None = None      # corrected address


class RescheduleIn(BaseModel):
    deadline: float
    destination: Stop | None = None      # corrected address, if the first one was wrong


class BulkParcels(BaseModel):
    parcels: list[ParcelIn]


class VehicleIn(BaseModel):
    name: str
    plate: str = ""
    type: str = "car"
    capacity: int | None = None
    maxParcelSize: str | None = None
    cargoCm: Dims | None = None         # cargo space L×W×H in cm (type default if blank)
    maxPayloadKg: float | None = None   # payload limit (type default if blank)


class DriverIn(BaseModel):
    name: str
    phone: str = ""
    vehicleId: str | None = None
    shiftStart: str | None = None       # "HH:MM"; blank = always on duty
    shiftEnd: str | None = None
    onLeave: bool = False
    maxStops: int | None = None
    maxHours: float | None = None
    breakMinutes: int | None = None


def _entity_or_404(table: str, entity_id: str) -> dict:
    obj = db.load_entity(table, entity_id)
    if not obj:
        raise HTTPException(404, f"{table[:-1]} not found")
    return obj


def _parcel_data(p: ParcelIn, exclude_id: str | None = None) -> dict:
    """Validate a parcel and return the data to store. A pickup request is
    collected at the customer and brought to the hub, so its destination is the
    hub and its deadline is the collect-by time."""
    _validate_parcel(p, exclude_id)
    data = p.model_dump()
    if p.requestType == "pickup":
        hub = delivery.get_depot()
        data["destination"] = {"lat": hub["lat"], "lng": hub["lng"], "label": hub["label"]}
        pk = data["pickup"]
        cutoff = pk.get("earliest")
        if pk.get("latest") is None:
            pk["latest"] = (cutoff or time.time()) + 2 * 3600
        data["deadline"] = pk["latest"]
    return data


def _validate_parcel(p: ParcelIn, exclude_id: str | None = None):
    if p.requestType not in ("delivery", "pickup"):
        raise HTTPException(400, "requestType must be delivery or pickup")
    if p.requestType == "pickup":
        if not p.pickup:
            raise HTTPException(400, "A pickup request needs the customer's address.")
        if p.pickup.earliest and p.pickup.latest and p.pickup.earliest > p.pickup.latest:
            raise HTTPException(400, "The collect-by time is before the customer's cut-off.")
    elif not p.destination or p.deadline is None:
        raise HTTPException(400, "A delivery needs a destination and a deadline.")
    if p.size not in delivery.SIZE_ORDER:
        raise HTTPException(400, f"size must be one of {delivery.SIZES}")
    if p.priority not in delivery.PRIORITIES:
        raise HTTPException(400, f"priority must be one of {list(delivery.PRIORITIES)}")
    if p.windowStart and p.deadline is not None and p.windowStart > p.deadline:
        raise HTTPException(400, "The delivery window opens after the deadline.")
    if p.weightKg is not None and (p.weightKg <= 0 or p.weightKg > 2000):
        raise HTTPException(400, "weightKg must be between 0 and 2000")
    if p.dimensionsCm and not all(0 < v <= 1000 for v in
                                  (p.dimensionsCm.l, p.dimensionsCm.w, p.dimensionsCm.h)):
        raise HTTPException(400, "Package dimensions must be between 0 and 1000 cm")
    tn = (p.trackingNumber or "").strip()
    if tn and any(x["trackingNumber"].lower() == tn.lower() and x["id"] != exclude_id
                  for x in db.all_entities("parcels") if x.get("trackingNumber")):
        raise HTTPException(409, f"Tracking number {tn} already exists")


def _validate_driver(d: DriverIn):
    for label, value in (("shiftStart", d.shiftStart), ("shiftEnd", d.shiftEnd)):
        if value and delivery._hhmm(value) is None:
            raise HTTPException(400, f"{label} must look like 08:00")
    if bool(d.shiftStart) != bool(d.shiftEnd):
        raise HTTPException(400, "Give both a shift start and a shift end, or neither.")
    if d.shiftStart and delivery._hhmm(d.shiftStart) >= delivery._hhmm(d.shiftEnd):
        raise HTTPException(400, "The shift must end after it starts.")
    if d.maxStops is not None and d.maxStops < 1:
        raise HTTPException(400, "maxStops must be at least 1")
    if d.maxHours is not None and not 0 < d.maxHours <= 24:
        raise HTTPException(400, "maxHours must be between 0 and 24")


def _validate_vehicle(v: VehicleIn):
    if v.cargoCm and not all(0 < x <= 2000 for x in (v.cargoCm.l, v.cargoCm.w, v.cargoCm.h)):
        raise HTTPException(400, "Cargo dimensions must be between 0 and 2000 cm")
    if v.maxPayloadKg is not None and v.maxPayloadKg <= 0:
        raise HTTPException(400, "maxPayloadKg must be positive")


@app.get("/api/parcels")
async def list_parcels():
    return delivery.flag_overdue(db.all_entities("parcels"))


@app.post("/api/parcels")
async def add_parcel(p: ParcelIn):
    data = _parcel_data(p)
    if p.requestType == "pickup" and data["deadline"] < time.time():
        raise HTTPException(400, "The collect-by time is in the past.")
    parcel = delivery.new_parcel(data)
    store.broadcast_global("parcel", {"parcel": parcel, "deleted": False})
    return parcel


@app.post("/api/parcels/bulk")
async def bulk_parcels(req: BulkParcels):
    created, errors = [], []
    for i, p in enumerate(req.parcels):
        try:
            parcel = delivery.new_parcel(_parcel_data(p))   # also catches duplicates of earlier rows, which are saved first
            store.broadcast_global("parcel", {"parcel": parcel, "deleted": False})
            created.append(parcel)
        except HTTPException as e:
            errors.append({"index": i, "message": str(e.detail)})
    return {"created": created, "errors": errors}


_DATA_URL = re.compile(r"^data:image/(png|jpeg|webp);base64,[A-Za-z0-9+/=]+$")
_MAX_IMAGE_CHARS = 700_000      # ~500 KB of image


def _check_image(value: str | None, what: str):
    if value is None:
        return
    if len(value) > _MAX_IMAGE_CHARS:
        raise HTTPException(413, f"The {what} is too large — use a smaller image.")
    if not _DATA_URL.match(value):
        raise HTTPException(400, f"The {what} must be a PNG, JPEG or WebP image.")


def _trip_for(parcel: dict) -> dict | None:
    return store.get_trip(parcel["tripId"]) if parcel.get("tripId") else None


@app.get("/api/parcels/{parcel_id}")
async def parcel_detail(parcel_id: str):
    parcel = _entity_or_404("parcels", parcel_id)
    trip = _trip_for(parcel) or (store.get_trip((parcel.get("lastFailure") or {}).get("tripId") or "")
                                 if parcel.get("lastFailure") else None)
    driver = trip and trip.get("driverId") and db.load_entity("drivers", trip["driverId"])
    return {**parcel, "timeline": delivery.timeline(parcel),
            "driverName": driver["name"] if driver else None,
            "attempts": parcel.get("failedAttempts", 0),
            "escalate": parcel.get("failedAttempts", 0) >= delivery.ESCALATE_AFTER}


@app.get("/api/parcels/{parcel_id}/pod")
async def parcel_pod_images(parcel_id: str):
    parcel = _entity_or_404("parcels", parcel_id)
    files = db.load_entity("pods", parcel_id) or {}
    return {"pod": parcel.get("pod"), "signature": files.get("signature"),
            "photo": files.get("photo")}


@app.post("/api/parcels/{parcel_id}/pod")
async def parcel_pod(parcel_id: str, body: PodIn):
    """Proof of delivery. On a parcel that is still on the road this also marks
    it delivered (the driver confirmed the drop); on one the route already
    ticked off it just attaches the evidence."""
    parcel = _entity_or_404("parcels", parcel_id)
    if body.method not in delivery.POD_METHODS:
        raise HTTPException(400, f"method must be one of {list(delivery.POD_METHODS)}")
    if parcel["status"] not in ("in_transit", "picked_up", "delivered"):
        raise HTTPException(409, "This parcel isn't out for delivery.")
    _check_image(body.signature, "signature")
    _check_image(body.photo, "photo")
    received_by = (body.receivedBy or "").strip()[:80] or None
    note = (body.note or "").strip()[:300] or None
    reference = (body.reference or "").strip()[:60] or None
    needs = {"signature": body.signature, "photo": body.photo,
             "handed_over": received_by or reference, "left_safe": note or body.photo}
    if not needs[body.method]:
        raise HTTPException(400, {"signature": "Draw a signature.", "photo": "Add a photo.",
                                  "handed_over": "Enter who received the parcel, or the receipt reference.",
                                  "left_safe": "Say where it was left, or add a photo."}[body.method])
    trip = _trip_for(parcel)
    was_delivered = parcel["status"] == "delivered"
    if parcel.get("cod") and parcel["cod"]["state"] == "due" and not was_delivered:
        if body.codCollected is None or body.codCollected < 0:
            raise HTTPException(400, f"Collect the cash first: {parcel['cod']['amount']:,.0f} is due on delivery.")
        cod.collect(parcel, body.codCollected, (trip or {}).get("driverId"), actor=body.by)
        short = parcel["cod"]["short"]
        delivery.add_event(parcel, "cod", f"Cash collected: {body.codCollected:,.0f}" + (f" ({short:,.0f} short)" if short else ""),
                           tripId=parcel.get("tripId"), by=body.by)
    delivery.save_pod(parcel, body.method, received_by, note, body.signature, body.photo,
                      parcel.get("tripId"), body.lat, body.lng, actor=body.by, reference=reference)
    if not was_delivered:
        delivery.set_parcel_status(parcel, "delivered")
        if trip:
            trip["deliveredCount"] = trip.get("deliveredCount", 0) + 1
            late = " (past deadline)" if parcel["deadlineMissed"] else ""
            _emit(trip, "delivered", f"Delivered: {parcel['name']}{late}")
            store.save_trip(trip)
    else:
        db.save_entity("parcels", parcel)
        store.broadcast_global("parcel", {"parcel": parcel, "deleted": False})
    return parcel


@app.post("/api/parcels/{parcel_id}/fail")
async def parcel_fail(parcel_id: str, body: FailIn):
    parcel = _entity_or_404("parcels", parcel_id)
    if body.reason not in delivery.FAIL_REASONS:
        raise HTTPException(400, f"reason must be one of {list(delivery.FAIL_REASONS)}")
    if parcel["status"] not in ("in_transit", "picked_up"):
        raise HTTPException(409, "Only a parcel that is out for delivery can fail.")
    trip = _trip_for(parcel)
    note = (body.note or "").strip()[:300] or None
    delivery.record_failed_attempt(parcel, body.reason, note, parcel.get("tripId") or
                                   (trip and trip["id"]), body.retryAt, actor=body.by)
    if trip:
        trip["failedCount"] = trip.get("failedCount", 0) + 1
        _emit(trip, "failed", f"Couldn't deliver {parcel['name']}: "
                              f"{delivery.FAIL_REASONS[body.reason]}")
        store.save_trip(trip)
    return parcel


class CollectIn(BaseModel):
    note: str | None = None
    by: str = "driver"


@app.post("/api/parcels/{parcel_id}/collect")
async def parcel_collect(parcel_id: str, body: CollectIn | None = None):
    """The driver (or dispatch, on their behalf) has collected a pickup request."""
    parcel = _entity_or_404("parcels", parcel_id)
    if parcel.get("requestType") != "pickup":
        raise HTTPException(409, "Only a pickup request is collected this way.")
    trip = _trip_for(parcel)
    try:
        delivery.collect_pickup(parcel, trip and trip["id"], actor=(body.by if body else "driver"),
                                note=((body.note or "").strip()[:300] or None) if body else None)
    except ValueError as e:
        raise HTTPException(409, str(e))
    if trip:
        trip["collectedCount"] = trip.get("collectedCount", 0) + 1
        _emit(trip, "collected", f"Collected: {parcel['name']}")
        store.save_trip(trip)
    return parcel


@app.get("/api/parcels/{parcel_id}/fit")
async def parcel_fit(parcel_id: str):
    """Which run could take this parcel (or pickup request), and what it would cost."""
    from . import fit
    parcel = _entity_or_404("parcels", parcel_id)
    if not delivery.is_plannable(parcel):
        raise HTTPException(409, "This parcel isn't waiting to be planned.")
    return {"parcelId": parcel_id, "runs": fit.suggest(parcel)}


@app.post("/api/parcels/{parcel_id}/early")
async def parcel_early(parcel_id: str):
    """Record that the shipment is ready and the customer confirmed it can be collected
    before their cut-off."""
    parcel = _entity_or_404("parcels", parcel_id)
    try:
        return delivery.confirm_early(parcel)
    except ValueError as e:
        raise HTTPException(409, str(e))


@app.post("/api/parcels/{parcel_id}/review")
async def parcel_review(parcel_id: str, body: ReviewIn, request: Request):
    """The separate rescheduling process: a dispatcher reviews why a delivery or pickup
    failed, then reschedules it (typically for the next day, if the customer asks) or
    returns it."""
    parcel = _entity_or_404("parcels", parcel_id)
    if body.decision not in ("reschedule", "return"):
        raise HTTPException(400, "decision must be reschedule or return")
    if parcel["status"] != "failed":
        raise HTTPException(409, "Only a failed shipment waits for review.")
    user = auth.current_user(request) if auth.enabled() else None
    try:
        delivery.review_failed(parcel, body.decision, body.note, body.scheduledFor,
                               body.destination.model_dump() if body.destination else None,
                               actor=(user or {}).get("username", "dispatcher"))
    except ValueError as e:
        raise HTTPException(400, str(e))
    return parcel


@app.post("/api/parcels/{parcel_id}/reschedule")
async def parcel_reschedule(parcel_id: str, body: RescheduleIn):
    """Set a new delivery time (and optionally a corrected address) for a
    parcel that is back at the hub or still pending."""
    parcel = _entity_or_404("parcels", parcel_id)
    if parcel["status"] not in delivery.PARCEL_PLANNABLE_STATUSES:
        raise HTTPException(409, "Only a pending parcel or one back at the hub can be rescheduled.")
    if body.deadline < time.time():
        raise HTTPException(400, "The new delivery time is in the past.")
    delivery.apply_schedule(parcel, body.deadline)
    when = time.strftime("%d %b %H:%M", time.localtime(body.deadline))
    if body.destination:
        parcel["destination"] = body.destination.model_dump()
        delivery.add_event(parcel, "edited", f"Delivery address changed to {body.destination.label}")
    delivery.add_event(parcel, "rescheduled", f"Rescheduled for delivery by {when}")
    db.save_entity("parcels", parcel)
    store.broadcast_global("parcel", {"parcel": parcel, "deleted": False})
    notify.notify(parcel, "rescheduled", deadline=body.deadline)
    return parcel


@app.post("/api/parcels/{parcel_id}/return")
async def parcel_return(parcel_id: str):
    """Give up on delivery: return the parcel to the sender."""
    parcel = _entity_or_404("parcels", parcel_id)
    if parcel["status"] == "failed":
        delivery.review_failed(parcel, "return", "Returned without further attempts")
        return parcel
    if parcel["status"] not in delivery.PARCEL_PLANNABLE_STATUSES:
        raise HTTPException(409, "This parcel is on a run — it can't be returned yet.")
    delivery.set_parcel_status(parcel, "returned", trip_id=None,
                               note="Returned to sender by dispatch")
    return parcel


# ---- customer tracking (public: no admin data, only what the customer should see)

_TRACK_STEPS = ["Order received", "Scheduled", "Out for delivery", "Delivered"]
_TRACK_STEP = {"pending": 0, "awaiting_redelivery": 0, "failed": 2, "assigned": 1, "in_transit": 2,
               "picked_up": 2, "delivered": 3, "returned": 3}
_TRACK_LABEL = {
    "pending": "We have your parcel", "assigned": "Scheduled for delivery",
    "in_transit": "Out for delivery", "picked_up": "Out for delivery",
    "delivered": "Delivered", "awaiting_redelivery": "We'll try delivering again",
    "failed": "We couldn't deliver — we'll contact you",
    "returned": "Returned to sender",
}
_PICKUP_STEPS = ["Pickup requested", "Scheduled", "Driver on the way", "Collected"]
_PICKUP_STEP = {"pending": 0, "failed": 2, "assigned": 1, "in_transit": 2, "picked_up": 3,
                "delivered": 3, "returned": 3}
_PICKUP_LABEL = {
    "pending": "Pickup requested", "assigned": "Pickup scheduled", "in_transit": "Driver on the way to collect",
    "picked_up": "Collected — on its way to our hub", "delivered": "Collected and at our hub",
    "failed": "We couldn't collect — we'll contact you", "returned": "Pickup cancelled",
}
_TRACK_EVENT = {
    "created": "Order received", "assigned": "Scheduled for delivery",
    "out_for_delivery": "Out for delivery", "collected": "Collected from the sender",
    "delivered": "Delivered", "returned_to_hub": "Back at our hub",
    "rescheduled": "Delivery rescheduled", "returned": "Returned to the sender",
    "unassigned": "Waiting to be scheduled",
}
_track_hits: dict[str, list[float]] = {}
TRACK_RATE_PER_MIN = int(os.environ.get("TRACK_RATE_PER_MIN", "30"))


def _track_rate_limit(ip: str):
    now = time.time()
    hits = [t for t in _track_hits.get(ip, []) if now - t < 60]
    if len(hits) >= TRACK_RATE_PER_MIN:
        raise HTTPException(429, "Too many lookups — wait a minute and try again.")
    hits.append(now)
    _track_hits[ip] = hits


@app.get("/api/track/{tracking_number}")
async def track(tracking_number: str, request: Request):
    _track_rate_limit(request.client.host if request.client else "?")
    want = tracking_number.strip().lower()
    parcel = next((p for p in db.all_entities("parcels")
                   if (p.get("trackingNumber") or "").lower() == want), None)
    if not parcel:
        raise HTTPException(404, "We couldn't find that tracking number. Check it and try again.")
    trip = _trip_for(parcel)
    eta, stops_away, driver = None, None, None
    if trip and parcel["status"] in ("in_transit", "picked_up"):
        driver = trip.get("driverId") and db.load_entity("drivers", trip["driverId"])
        order = [s.get("parcelId") for s in trip["route"]["orderedStops"][1:]
                 if delivery.stop_kind(s) == "delivery"]
        pending = []
        for pid in order:
            q = db.load_entity("parcels", pid)
            if q and q["status"] in ("in_transit", "picked_up"):
                pending.append(pid)
        if parcel["id"] in pending:
            stops_away = pending.index(parcel["id"])
        eta = next((e["eta"] for e in trip.get("plannedEtaPerStop") or []
                    if e.get("parcelId") == parcel["id"] and e.get("kind") == "delivery"), None)
    request = parcel.get("requestType") == "pickup"
    label = ((parcel.get("pickup") or {}).get("label") if request else parcel["destination"].get("label")) or ""
    area = label.split(",", 1)[1].strip() if "," in label else label
    pod = parcel.get("pod") or {}
    failure = parcel.get("lastFailure")
    history = [{"ts": e["ts"], "message": _TRACK_EVENT[e["type"]]}
               for e in delivery.timeline(parcel) if e["type"] in _TRACK_EVENT]
    for e in delivery.timeline(parcel):
        if e["type"] == "failed":
            history.append({"ts": e["ts"], "message": "Delivery attempt unsuccessful"})
    history.sort(key=lambda h: h["ts"])
    return {
        "trackingNumber": parcel["trackingNumber"],
        "status": parcel["status"],
        "headline": (_PICKUP_LABEL if request else _TRACK_LABEL).get(parcel["status"], "In progress"),
        "step": (_PICKUP_STEP if request else _TRACK_STEP).get(parcel["status"], 0),
        "steps": _PICKUP_STEPS if request else _TRACK_STEPS,
        "kind": "pickup" if request else "delivery",
        "deliverTo": area,
        "expectedBy": parcel["deadline"] if parcel["status"] != "delivered" else None,
        "eta": eta,
        "stopsAway": stops_away,
        "driverFirstName": driver["name"].split()[0] if driver else None,
        "deliveredAt": parcel.get("deliveredAt"),
        "receivedBy": pod.get("receivedBy") if parcel["status"] == "delivered" else None,
        "lastProblem": failure["label"] if failure and parcel["status"] in ("failed", "awaiting_redelivery", "returned") else None,
        "history": history,
        # what the receiver may do themselves: confirm they will be there, add directions, pick another day, cancel
        "canRespond": (not request) and parcel["status"] in ("pending", "assigned", "awaiting_redelivery"),
        "confirmation": (parcel.get("confirmation") or {}).get("state"),
        "cashDue": parcel["cod"]["amount"] if parcel.get("cod") and parcel["cod"]["state"] == "due" else None,
        "landmark": parcel.get("landmark"),
    }


@app.get("/api/parcels/{parcel_id}/notifications")
async def parcel_notifications(parcel_id: str):
    parcel = _entity_or_404("parcels", parcel_id)
    return {"trackingUrl": notify.tracking_url(parcel), "messages": notify.for_parcel(parcel_id)}


# ---- warehouse: loading bay and receiving desk

def _who(request: Request) -> str:
    user = auth.current_user(request) if auth.enabled() else None
    return (user or {}).get("username") or "warehouse"


class LoadedIn(BaseModel):
    loaded: bool = True


class ReceivedIn(BaseModel):
    note: str | None = None


@app.get("/api/warehouse/loading")
async def warehouse_loading():
    """Per run that has not left: the parcels to hand to that driver, in loading order."""
    return warehouse.loading_board()


@app.get("/api/warehouse/receiving")
async def warehouse_receiving():
    """What is coming back to the hub and needs checking in."""
    return warehouse.receiving_board()


@app.post("/api/parcels/{parcel_id}/loaded")
async def parcel_loaded(parcel_id: str, body: LoadedIn | None = None, request: Request = None):
    parcel = _entity_or_404("parcels", parcel_id)
    try:
        return warehouse.set_loaded(parcel, body.loaded if body else True, _who(request))
    except ValueError as e:
        raise HTTPException(409, str(e))


@app.post("/api/runs/{trip_id}/loaded")
async def run_loaded(trip_id: str, body: LoadedIn | None = None, request: Request = None):
    """Tick (or untick) every parcel of a run in one go."""
    trip = _trip_or_404(trip_id)
    done = 0
    for pid in trip.get("parcelIds") or []:
        p = db.load_entity("parcels", pid)
        if not p or p["status"] not in warehouse.LOAD_STATUSES + ("in_transit",) or not warehouse._loadable(trip, p):
            continue
        warehouse.set_loaded(p, body.loaded if body else True, _who(request))
        done += 1
    return {"ok": True, "parcels": done}


@app.post("/api/parcels/{parcel_id}/received")
async def parcel_received(parcel_id: str, body: ReceivedIn | None = None, request: Request = None):
    parcel = _entity_or_404("parcels", parcel_id)
    try:
        return warehouse.check_in(parcel, _who(request), (body.note or "").strip()[:200] or None if body else None)
    except ValueError as e:
        raise HTTPException(409, str(e))


# ---- predefined routes (route templates)

class ZoneIn(BaseModel):
    lat: float
    lng: float
    radiusKm: float
    label: str | None = None


class TemplatePickupIn(BaseModel):
    id: str | None = None
    label: str
    lat: float
    lng: float
    name: str | None = None            # who is collected, e.g. the customer
    readyBy: str | None = None         # "HH:MM" customer cut-off
    collectBy: str | None = None       # "HH:MM"
    size: str = "small"
    note: str | None = None


class TemplateIn(BaseModel):
    code: str
    name: str
    zone: ZoneIn
    driverId: str | None = None
    days: list[int] | None = None      # Monday = 0
    startTime: str | None = None
    pickups: list[TemplatePickupIn] = []
    notes: str | None = None


class TemplateRunIn(BaseModel):
    driverId: str | None = None


def _validate_template(t: TemplateIn, exclude_id: str | None = None):
    if not t.code.strip() or not t.name.strip():
        raise HTTPException(400, "Give the route a code and a name.")
    if any(x["code"].lower() == t.code.strip().lower() and x["id"] != exclude_id for x in route_templates.list_templates()):
        raise HTTPException(409, f"Route code {t.code.strip()} already exists.")
    if not 0.5 <= t.zone.radiusKm <= route_templates.MAX_RADIUS_KM:
        raise HTTPException(400, f"The zone radius must be between 0.5 and {route_templates.MAX_RADIUS_KM:g} km.")
    if any(d not in range(7) for d in t.days or []):
        raise HTTPException(400, "Service days are 0 (Monday) to 6 (Sunday).")
    for label, value in (("startTime", t.startTime), *[(f"pickup {p.label} readyBy", p.readyBy) for p in t.pickups],
                         *[(f"pickup {p.label} collectBy", p.collectBy) for p in t.pickups]):
        if value and delivery._hhmm(value) is None:
            raise HTTPException(400, f"{label} must look like 08:00")
    if t.driverId and not db.load_entity("drivers", t.driverId):
        raise HTTPException(404, "Driver not found")
    if any(p.size not in delivery.SIZE_ORDER for p in t.pickups):
        raise HTTPException(400, f"size must be one of {delivery.SIZES}")


@app.get("/api/route-templates")
async def list_route_templates():
    out = []
    for t in route_templates.list_templates():
        out.append({**t, "zoneParcelCount": len(route_templates.zone_parcels(t)), "runsToday": route_templates.runs_today(t)})
    return out


@app.post("/api/route-templates")
async def add_route_template(t: TemplateIn):
    _validate_template(t)
    return route_templates.save(t.model_dump())


@app.put("/api/route-templates/{template_id}")
async def edit_route_template(template_id: str, t: TemplateIn):
    if not route_templates.get(template_id):
        raise HTTPException(404, "Route not found")
    _validate_template(t, exclude_id=template_id)
    return route_templates.save(t.model_dump(), template_id)


@app.delete("/api/route-templates/{template_id}")
async def delete_route_template(template_id: str):
    if not route_templates.get(template_id):
        raise HTTPException(404, "Route not found")
    route_templates.delete(template_id)
    return {"ok": True}


@app.get("/api/route-templates/{template_id}/preview")
async def preview_route_template(template_id: str):
    t = route_templates.get(template_id)
    if not t:
        raise HTTPException(404, "Route not found")
    return route_templates.preview(t)


@app.post("/api/route-templates/{template_id}/run")
async def run_route_template(template_id: str, req: TemplateRunIn | None = None):
    """Create today's run from a predefined route."""
    t = route_templates.get(template_id)
    if not t:
        raise HTTPException(404, "Route not found")
    try:
        trip = await route_templates.run_today(t, req.driverId if req else None)
    except delivery.RunError as e:
        raise HTTPException(e.code, str(e))
    store.broadcast(trip["id"], "trip", _summary(trip))
    return trip


# ---- reports

class CostsIn(BaseModel):
    perKm: float
    perHour: float
    currency: str = "LKR"


def _period(days: str | None) -> int | None:
    """?days=today|7|30|all  ->  0 | n | None"""
    if days in (None, "", "7"):
        return 7
    if days == "all":
        return None
    if days == "today":
        return 0
    if days.isdigit() and 0 < int(days) <= 3650:
        return int(days)
    raise HTTPException(400, "days must be today, a number of days, or all")


class FuelIn(BaseModel):
    pricePerL: float
    co2PerL: float | None = None
    l100: dict[str, float] | None = None


@app.get("/api/impact")
async def impact_summary(days: str | None = None):
    """What the optimisation is worth: savings vs the as-entered order, fuel, CO2, plan vs actual."""
    return impact.summary(_period(days))


@app.get("/api/scorecard")
async def scorecard_summary(days: str | None = None):
    """The research's KPI scorecard (first-attempt rate, RTO, COD remittance and variance, productivity) with its targets."""
    return scorecard.summary(_period(days))


@app.put("/api/settings/fuel")
async def set_fuel(f: FuelIn):
    if f.pricePerL < 0 or any(v < 0 for v in (f.l100 or {}).values()):
        raise HTTPException(400, "Fuel price and consumption can't be negative.")
    return impact.set_fuel(f.pricePerL, f.l100, f.co2PerL)


@app.get("/api/reports/summary")
async def report_summary(days: str | None = None):
    return reports.summary(_period(days))


@app.get("/api/reports/export")
async def report_export(kind: str = "parcels", days: str | None = None):
    try:
        body = reports.export_csv(kind, _period(days))
    except ValueError as e:
        raise HTTPException(400, str(e))
    name = f"fleetops-{kind}-{time.strftime('%Y%m%d')}.csv"
    return Response("﻿" + body, media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="{name}"'})


@app.get("/api/settings/costs")
async def get_costs():
    return reports.get_costs()


@app.put("/api/settings/costs")
async def put_costs(c: CostsIn):
    if c.perKm < 0 or c.perHour < 0 or c.perKm > 1e6 or c.perHour > 1e6:
        raise HTTPException(400, "Rates must be between 0 and 1,000,000.")
    return reports.set_costs(c.perKm, c.perHour, c.currency)


# ---- rides at a point in time (admin "live board" and time travel)

@app.get("/api/rides")
async def rides_at(at: str | None = None):
    """Every run and what it was doing at `at` (epoch seconds; omit or 'now' for live)."""
    if at in (None, "", "now"):
        return rides.rides_at(None)
    try:
        ts = float(at)
    except ValueError:
        raise HTTPException(400, "at must be 'now' or epoch seconds")
    if not 0 < ts < time.time() + 366 * 86400:
        raise HTTPException(400, "at is out of range")
    return rides.rides_at(min(ts, time.time()))


@app.get("/api/trips/{trip_id}/trail")
async def trip_trail(trip_id: str, until: float | None = None):
    _trip_or_404(trip_id)
    return {"points": rides.trail(trip_id, until)}


@app.get("/api/notifications")
async def recent_notifications(limit: int = 50):
    return {"providerConfigured": bool(os.environ.get("NOTIFY_WEBHOOK_URL")),
            "messages": notify.recent(min(max(limit, 1), 200))}


@app.get("/api/trips/{trip_id}/parcels")
async def trip_parcels(trip_id: str):
    """The parcels of a run, in stop order, for the driver's stop list."""
    trip = _trip_or_404(trip_id)
    order, pickup_at = {}, {}
    for i, stop in enumerate(trip["route"]["orderedStops"]):
        if stop.get("parcelId") and delivery.stop_kind(stop) == "delivery":
            order.setdefault(stop["parcelId"], i)
        elif stop.get("parcelId") and delivery.stop_kind(stop) == "pickup" and stop.get("requestType"):
            pickup_at[stop["parcelId"]] = i
    out = []
    for pid in trip.get("parcelIds") or []:
        p = db.load_entity("parcels", pid)
        if p:
            at = pickup_at.get(pid) if p["status"] in ("assigned", "in_transit", "failed") else None
            out.append({**p, "stopIndex": at if at is not None else order.get(pid), "events": None,
                        "collectFirst": delivery.route_has_pickup(trip["route"], pid)
                        and p["status"] != "picked_up" and p["status"] != "delivered"})
    return sorted(out, key=lambda p: (p["stopIndex"] is None, p["stopIndex"] or 0))


@app.put("/api/parcels/{parcel_id}")
async def edit_parcel(parcel_id: str, p: ParcelIn):
    parcel = _entity_or_404("parcels", parcel_id)
    if parcel["status"] not in (*delivery.PARCEL_PLANNABLE_STATUSES, "failed"):
        raise HTTPException(409, "This parcel is on a run — it can't be edited.")
    data = _parcel_data(p, exclude_id=parcel_id)
    if not (data.get("trackingNumber") or "").strip():
        data["trackingNumber"] = parcel.get("trackingNumber")   # blank keeps the existing one
    amount = data.pop("codAmount", None)
    if not parcel.get("cod") or parcel["cod"]["state"] == "due":
        parcel["cod"] = cod.new_cod(amount)
    parcel.update(data)
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
    _validate_vehicle(v)
    return delivery.new_vehicle(v.model_dump())


@app.put("/api/vehicles/{vehicle_id}")
async def edit_vehicle(vehicle_id: str, v: VehicleIn):
    vehicle = _entity_or_404("vehicles", vehicle_id)
    if delivery.entity_in_use("vehicles", vehicle_id):
        raise HTTPException(409, "Vehicle is on a delivery run — end it first.")
    _validate_vehicle(v)
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
async def list_drivers(request: Request):
    drivers = db.all_entities("drivers")
    user = auth.current_user(request) if auth.enabled() else None
    can_see_keys = not auth.enabled() or (user and (auth.has_perm(user["role"], "runs.plan")
                                                    or auth.has_perm(user["role"], "registry.write")))
    for d in drivers:
        if can_see_keys:
            auth.driver_key(d)          # older drivers get a key the first time
        else:
            d.pop("token", None)
    return drivers


@app.post("/api/drivers")
async def add_driver(d: DriverIn):
    _validate_driver(d)
    if d.vehicleId:
        _entity_or_404("vehicles", d.vehicleId)
    return delivery.new_driver(d.model_dump())


@app.put("/api/drivers/{driver_id}")
async def edit_driver(driver_id: str, d: DriverIn):
    driver = _entity_or_404("drivers", driver_id)
    if delivery.entity_in_use("drivers", driver_id):
        raise HTTPException(409, "Driver is on a delivery run — end it first.")
    _validate_driver(d)
    if d.vehicleId:
        _entity_or_404("vehicles", d.vehicleId)
    data = d.model_dump()
    if data["breakMinutes"] is None:
        data["breakMinutes"] = driver.get("breakMinutes", 30)
    driver.update(data)
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


class AddToRun(BaseModel):
    parcelIds: list[str]


class ReassignRequest(BaseModel):
    driverId: str


class MoveRequest(BaseModel):
    toTripId: str


def _replan_error(e: Exception):
    code = getattr(e, "code", 409)
    raise HTTPException(code, str(e))


@app.post("/api/runs/{trip_id}/parcels")
async def run_add_parcels(trip_id: str, req: AddToRun):
    """Add parcels to a planned run — or one already under way."""
    try:
        trip = await replan.replan(trip_id, add_ids=req.parcelIds)
    except delivery.RunError as e:
        _replan_error(e)
    store.broadcast(trip_id, "trip", _summary(trip))
    return _summary(trip)


class BreakdownIn(BaseModel):
    apply: bool = False


@app.post("/api/runs/{trip_id}/breakdown")
async def run_breakdown(trip_id: str, req: BreakdownIn | None = None):
    """A vehicle broke down: preview (default) or apply handing its remaining parcels to other runs."""
    try:
        return await replan.breakdown(trip_id, apply=bool(req and req.apply))
    except delivery.RunError as e:
        _replan_error(e)


@app.delete("/api/runs/{trip_id}/parcels/{parcel_id}")
async def run_remove_parcel(trip_id: str, parcel_id: str):
    try:
        trip = await replan.replan(trip_id, remove_ids=[parcel_id])
    except delivery.RunError as e:
        _replan_error(e)
    store.broadcast(trip_id, "trip", _summary(trip))
    return _summary(trip)


@app.post("/api/runs/{trip_id}/reassign")
async def run_reassign(trip_id: str, req: ReassignRequest):
    """Give a planned run to another driver (and their vehicle)."""
    try:
        trip = await replan.replan(trip_id, driver_id=req.driverId)
    except delivery.RunError as e:
        _replan_error(e)
    store.broadcast(trip_id, "trip", _summary(trip))
    return _summary(trip)


@app.post("/api/parcels/{parcel_id}/move")
async def parcel_move(parcel_id: str, req: MoveRequest):
    """Move a parcel from its run (or the pending list) onto another run."""
    try:
        target, source = await replan.move_parcel(parcel_id, req.toTripId)
    except delivery.RunError as e:
        _replan_error(e)
    store.broadcast(target["id"], "trip", _summary(target))
    if source:
        store.broadcast(source["id"], "trip", _summary(source))
    return {"to": _summary(target), "from": _summary(source) if source else None}


@app.get("/api/runs/{trip_id}/options")
async def run_options(trip_id: str):
    """What the dispatcher can do with this run: drivers who could take it over
    (planned runs only) and the reason each of the others can't."""
    trip = _trip_or_404(trip_id)
    parcels = [p for p in (db.load_entity("parcels", pid) for pid in trip.get("parcelIds") or [])
               if p and p["status"] in ("assigned", "in_transit", "picked_up")]
    drivers = []
    for d in db.all_entities("drivers"):
        if d["id"] == trip.get("driverId"):
            continue
        v = db.load_entity("vehicles", d.get("vehicleId") or "")
        reason = ("no vehicle assigned" if not v else
                  "has an unfinished run" if delivery.entity_in_use("drivers", d["id"]) else
                  delivery.check_eligibility(v, parcels, d))
        drivers.append({"id": d["id"], "name": d["name"],
                        "vehicle": v["name"] if v else None, "reason": reason})
    return {"canReassign": trip["status"] == "planned", "drivers": drivers}


class RunRequest(BaseModel):
    parcelIds: list[str]
    driverId: str
    avoidTolls: bool = False


class ProposeRequest(BaseModel):
    parcelIds: list[str] | None = None
    engine: str = "rules"      # "rules" (deterministic, fewest vehicles) | "ai" (Gemini agent)


class EligibilityRequest(BaseModel):
    parcelIds: list[str]


class ApproveBatch(BaseModel):
    driverId: str
    parcelIds: list[str]


class ApproveRequest(BaseModel):
    batches: list[ApproveBatch]
    avoidTolls: bool = False


@app.post("/api/dispatch/propose")
async def dispatch_propose(req: ProposeRequest):
    try:
        if req.engine == "ai":
            from .dispatcher.service import propose_dispatch
            plan = await propose_dispatch(req.parcelIds or None)
            plan["engine"] = "ai"
            return plan
        from .dispatcher.rules import propose_by_rules
        return propose_by_rules(req.parcelIds or None)
    except RuntimeError as e:
        raise HTTPException(502 if req.engine == "ai" else 409, str(e))


@app.post("/api/runs/eligibility")
async def run_eligibility(req: EligibilityRequest):
    """Per driver: can their vehicle take this parcel set? (reason if not)"""
    parcels = [_entity_or_404("parcels", pid) for pid in req.parcelIds]
    out = {}
    for d in db.all_entities("drivers"):
        v = db.load_entity("vehicles", d.get("vehicleId") or "")
        out[d["id"]] = ("no vehicle assigned" if not v
                        else delivery.check_eligibility(v, parcels, d))
    return out


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
        except Exception as e:            # never a bare HTTP 500: say what failed
            logging.getLogger(__name__).exception("approve failed for batch %s", i)
            errors.append({"index": i, "message": f"Couldn't plan this run: {type(e).__name__}: {e}"})
    return {"created": created, "errors": errors}


@app.post("/api/runs")
async def create_run(req: RunRequest):
    try:
        trip = await delivery.create_run(req.parcelIds, req.driverId, req.avoidTolls)
    except delivery.RunError as e:
        raise HTTPException(e.code, str(e))
    except RuntimeError as e:
        raise HTTPException(502, str(e))
    except Exception as e:
        logging.getLogger(__name__).exception("create_run failed")
        raise HTTPException(502, f"Couldn't plan this run: {type(e).__name__}: {e}")
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


def _run_label(stops: list[dict]) -> str:
    """'<origin> → <final drop-off>'. Origin is 'Hub' for a delivery run;
    the end is the last stop's place (a drop-off address), never a parcel name."""
    first = stops[0]
    origin = "Hub" if (first.get("depot") or first.get("kind") == "depot") \
        else first.get("label", "Start")
    end = stops[-1].get("place") or stops[-1].get("label", "destination")
    return f"{origin} → {end}"


def _summary(t: dict) -> dict:
    stops = t["route"]["orderedStops"]
    return {
        "id": t["id"],
        "status": t["status"],
        "label": _run_label(stops),
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
        "templateCode": t.get("templateCode"), "templateName": t.get("templateName"),
        "parcelCount": len(t.get("parcelIds") or []),
        "deliveredCount": t.get("deliveredCount", 0),
        "collectedCount": t.get("collectedCount", 0),
        # delivery stops whose planned ETA is past the parcel deadline
        "atRiskParcelIds": [e["parcelId"] for e in t.get("plannedEtaPerStop") or []
                            if e.get("kind") == "delivery" and e.get("atRisk")],
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
    # promise times from the real departure, not from when the run was planned
    members = {p["id"]: p for p in (db.load_entity("parcels", i) for i in trip.get("parcelIds") or []) if p}
    trip["plannedEtaPerStop"] = delivery.compute_planned_etas(trip["route"], members, trip["startedAt"])
    store.runtime(trip_id).update(
        consecutive=0, alerting=False, along=0.0,
        pickup_risk_fired=set(), stall_along=0.0,
        stall_since=time.time(), stall_fired=False)
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
        if p.get("tripId") != trip_id:        # failed / returned during this run
            continue
        if p.get("requestType") == "pickup":
            if p["status"] == "picked_up":      # collected: it is at the hub now
                delivery.set_parcel_status(p, "delivered")
                trip["deliveredCount"] = trip.get("deliveredCount", 0) + 1
            else:                               # never collected: back on the pickup queue
                delivery.set_parcel_status(p, "pending", trip_id=None)
        elif arrived:
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
    runtime["driver_halted"] = pos.stopped
    alert, along = deviation.check_position(trip, runtime, pos.lat, pos.lng)
    _check_stop_progress(trip, runtime)
    _check_nearby(trip, runtime)
    _check_pickup_risk(trip, runtime)
    _check_stall(trip, runtime)
    if alert:
        trip["alerts"].append(alert)
        trip["status"] = "deviating" if alert["type"] == "deviation" else "active"
        store.broadcast(trip_id, "alert", alert)
        store.broadcast(trip_id, "status", {"status": trip["status"]})
        if alert["type"] == "deviation":
            _maybe_reroute(trip, runtime, pos.lat, pos.lng, along)
    store.save_trip(trip)
    return {
        "ok": True, "alert": alert, "routeVersion": trip["routeVersion"],
        # so the driver page can confirm a stop reached dispatch, without
        # depending on the admin's SSE
        "stall": _stall_status(trip, runtime),
    }


_BLOCK_LABEL = {"deviating": "you're off the route",
                "at-a-stop": "parked at a stop"}


def _stall_status(trip: dict, runtime: dict) -> dict | None:
    if trip["status"] not in ("active", "deviating") or not runtime.get("stall_since"):
        return None
    idle = time.time() - runtime["stall_since"]
    if idle < 3:                       # moving normally
        return None
    blk = runtime.get("stall_blocked")
    return {"idleSeconds": round(idle), "alerted": bool(runtime.get("stall_fired")),
            "threshold": STALL_SECONDS,
            "blocked": _BLOCK_LABEL.get(blk) if not runtime.get("stall_fired") and idle >= STALL_SECONDS else None}


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
        if kind == "pickup" and p.get("requestType") == "pickup":
            continue            # a pickup request is collected by the driver (or fails), never by driving past
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
            if p.get("cod") and p["cod"]["state"] == "due":
                continue        # cash on delivery: only the rider taking the cash completes it
            delivery.set_parcel_status(p, "delivered")
            trip["deliveredCount"] = trip.get("deliveredCount", 0) + 1
            late = " (past deadline)" if p["deadlineMissed"] else ""
            _emit(trip, "delivered", (f"Received at the hub: {p['name']}" if p.get("requestType") == "pickup"
                                      else f"Delivered: {p['name']}{late}"))


NEARBY_METERS = float(os.environ.get("NEARBY_METERS", "1500"))
NEARBY_SPEED_MS = 25 / 3.6          # assumed urban speed for the "minutes away" estimate


def _check_nearby(trip: dict, runtime: dict):
    """Tell the customer when the vehicle is within NEARBY_METERS (along the
    route) of their stop. notify() makes this once per parcel."""
    if not trip.get("parcelIds") or trip["status"] not in ("active", "deviating"):
        return
    bounds, cum = [0.0], 0.0
    for leg in trip["route"]["legs"]:
        cum += leg["distanceMeters"]
        bounds.append(cum)
    for i, stop in enumerate(trip["route"]["orderedStops"]):
        pid = stop.get("parcelId")
        if i == 0 or not pid or delivery.stop_kind(stop) != "delivery":
            continue
        remaining = bounds[i] - runtime["along"]
        if not 0 < remaining <= NEARBY_METERS:
            continue
        p = db.load_entity("parcels", pid)
        if p and p["status"] in ("in_transit", "picked_up"):
            notify.notify(p, "nearby", minutes=max(1, round(remaining / NEARBY_SPEED_MS / 60)))


def _emit(trip: dict, kind: str, message: str):
    alert = _lifecycle_alert(trip["id"], kind, message)
    trip["alerts"].append(alert)
    store.broadcast(trip["id"], "alert", alert)


# ---- live risk detection (dispatcher-facing alerts) ----

# how late (vs the pickup window) the projection must run before we warn
PICKUP_RISK_SLACK_S = float(os.environ.get("PICKUP_RISK_SLACK_S", "60"))
# no on-route progress for this long, while not at a stop and not in a jam,
# reads as the vehicle being stopped for a non-traffic reason. Bump it up for
# a less twitchy production feel (e.g. STALL_SECONDS=90).
STALL_SECONDS = float(os.environ.get("STALL_SECONDS", "30"))
STALL_ADVANCE_M = 12.0          # progress below this over the window = "not moving"
STALL_STOP_RADIUS_M = 80.0      # legitimately paused this close (along the route) to a stop


def _leg_bounds(trip: dict) -> list[float]:
    bounds, cum = [0.0], 0.0
    for leg in trip["route"]["legs"]:
        cum += leg["distanceMeters"]
        bounds.append(cum)
    return bounds


def _eta_seconds_to_stop(trip: dict, along: float, stop_index: int) -> float:
    """Seconds from the current on-route progress to `stop_index`, scaling each
    remaining leg's planned (traffic-aware) duration by the fraction of it still
    to drive."""
    legs, bounds = trip["route"]["legs"], _leg_bounds(trip)
    if stop_index >= len(bounds) or along >= bounds[stop_index]:
        return 0.0
    total = 0.0
    for k in range(stop_index):            # legs 0..stop_index-1 lead to it
        lo, hi = bounds[k], bounds[k + 1]
        if hi <= along or hi == lo:
            continue
        frac = (hi - max(along, lo)) / (hi - lo)
        total += legs[k]["durationSeconds"] * max(0.0, min(1.0, frac))
    return total


def _check_pickup_risk(trip: dict, runtime: dict):
    """Warn the dispatcher when the vehicle is projected to reach a not-yet-
    collected parcel's pickup after its collection window closes."""
    if trip["status"] not in ("active", "deviating") or not trip.get("parcelIds"):
        return
    now = time.time()
    along = runtime.get("along", 0.0)
    fired = runtime.setdefault("pickup_risk_fired", set())
    for i, stop in enumerate(trip["route"]["orderedStops"]):
        if i == 0 or delivery.stop_kind(stop) != "pickup":
            continue
        latest = stop.get("latest")
        pid = stop.get("parcelId")
        if not latest or not pid or pid in fired:
            continue
        p = db.load_entity("parcels", pid)
        if not p or p["status"] in ("picked_up", "delivered"):
            continue
        eta = now + _eta_seconds_to_stop(trip, along, i)
        if eta > latest + PICKUP_RISK_SLACK_S:
            fired.add(pid)
            mins = max(1, round((eta - latest) / 60))
            place = stop.get("place") or stop.get("label", "the pickup")
            _emit(trip, "pickup_risk",
                  f"At risk of missing the pickup window for “{p['name']}” at "
                  f"{place} — projected ~{mins} min late "
                  f"(collect by {time.strftime('%I:%M %p', time.localtime(latest)).lstrip('0')})")


def _in_traffic(trip: dict, along: float) -> bool:
    """Does the current on-route position sit inside a full TRAFFIC_JAM stretch?
    'SLOW' doesn't count — a merely slow road still means the vehicle is moving,
    so a *stationary* one there is still worth flagging."""
    jams = [t for t in (trip["route"].get("traffic") or []) if t[2] == "TRAFFIC_JAM"]
    path = trip["route"].get("path") or []
    if not jams or len(path) < 2:
        return False
    cum, idx = 0.0, len(path) - 1
    for i in range(1, len(path)):
        seg = routing._haversine_m({"lat": path[i - 1][0], "lng": path[i - 1][1]},
                                   {"lat": path[i][0], "lng": path[i][1]})
        if cum + seg >= along:
            idx = i - 1
            break
        cum += seg
    return any(lo - 2 <= idx <= hi + 2 for lo, hi, _speed in jams)


def _at_a_stop(trip: dict, along: float, pos: dict | None = None) -> bool:
    """Plausibly paused at a delivery / pickup stop to load or unload?

    Progress must be within STALL_STOP_RADIUS_M *along the route* of a stop
    boundary — the depot origin (bounds[0]) excluded, since you don't "pause
    at the depot" once you've driven off — and, when a position is given, the
    vehicle must be physically near that stop too."""
    bounds = _leg_bounds(trip)
    stops = trip["route"]["orderedStops"]
    for i in range(1, len(bounds)):
        if abs(along - bounds[i]) > STALL_STOP_RADIUS_M:
            continue
        if pos is None or i >= len(stops):
            return True
        if routing._haversine_m(pos, stops[i]) <= STALL_STOP_RADIUS_M * 1.5:
            return True
    return False


def _check_stall(trip: dict, runtime: dict):
    """Warn the dispatcher when a moving vehicle stops making progress and it
    isn't parked at one of its stops. A genuinely stationary vehicle is the
    signal — heavy traffic is noted in the message, not used to suppress it
    (plan-time traffic data is stale within minutes anyway)."""
    if trip["status"] != "active":            # deviations own the "deviating" state
        return
    now = time.time()
    along = runtime.get("along", 0.0)
    if runtime.get("stall_since", 0.0) == 0.0:
        runtime["stall_since"] = now
        runtime["stall_along"] = along
        return

    if along - runtime.get("stall_along", 0.0) >= STALL_ADVANCE_M:
        runtime["stall_along"] = along
        runtime["stall_since"] = now
        if runtime.get("stall_fired"):
            runtime["stall_fired"] = False
            _emit(trip, "moving_again", "Vehicle is moving again")
            log.info("trip %s moving again (along=%.0f m)", trip["id"], along)
        return

    idle = now - runtime["stall_since"]
    blocked = ("deviating" if runtime.get("alerting")
               else "at-a-stop" if _at_a_stop(trip, along, trip.get("lastPosition"))
               else None)
    runtime["stall_blocked"] = blocked

    # once the vehicle has sat for a few seconds, log the state every ~5 s so
    # "why no alert?" is answerable straight from the uvicorn console
    if idle >= 3 and now - runtime.get("stall_logged", 0.0) >= 5:
        runtime["stall_logged"] = now
        log.info("trip %s idle %.0fs / %.0fs  along=%.0f m  fired=%s  blocked=%s",
                 trip["id"], idle, STALL_SECONDS, along,
                 runtime.get("stall_fired"), blocked or "-")

    if runtime.get("stall_fired") or idle < STALL_SECONDS or blocked:
        return

    runtime["stall_fired"] = True
    span = f"~{round(idle / 60)} min" if idle >= 90 else f"~{round(idle)}s"
    if runtime.get("driver_halted"):
        msg = (f"The driver has stopped the vehicle for {span} — not traffic. "
               f"Check in with them.")
    elif _in_traffic(trip, along):
        msg = (f"Vehicle has not moved for {span}. This stretch had heavy "
               f"traffic when the route was planned — confirm it's just traffic.")
    else:
        msg = (f"Vehicle has not moved for {span} and it isn't traffic — "
               f"the driver may be stopped. Check in with them.")
    log.info("trip %s STALLED — idle %.0fs, along=%.0f m, driver_halted=%s",
             trip["id"], idle, along, runtime.get("driver_halted"))
    _emit(trip, "stalled", msg)


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
    # if a pickup got dropped from `remaining` but its delivery is still there
    # (progress estimate straddling coincident stops), repair keeps the reroute
    # order valid — you can't deliver what you haven't collected
    remaining = routing.repair_precedence(remaining)
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
    runtime.update(consecutive=0, alerting=False, along=0.0,
                   stall_along=0.0, stall_since=time.time(), stall_fired=False)
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


class RevalidatingStatic(StaticFiles):
    """Static files that browsers must revalidate on every load (cheap 304s when unchanged), so an
    updated page never runs against a stale cached script or stylesheet."""

    async def get_response(self, path, scope):
        response = await super().get_response(path, scope)
        response.headers["Cache-Control"] = "no-cache"
        return response


app.mount("/static", RevalidatingStatic(directory="static"), name="static")


# ---- real-world operations: cash on delivery, confirmation and risk, disruptions, capacity

class CodDepositIn(BaseModel):
    driverId: str
    counted: float
    reference: str | None = None
    note: str | None = None


class CodRemitIn(BaseModel):
    merchant: str
    reference: str | None = None


class CodSettingsIn(BaseModel):
    cashLimit: float | None = None
    depositHours: float | None = None


@app.get("/api/cod/ledger")
async def cod_ledger():
    return cod.ledger()


@app.put("/api/cod/settings")
async def cod_settings(body: CodSettingsIn):
    return cod.set_settings(body.cashLimit, body.depositHours)


@app.post("/api/cod/deposit")
async def cod_deposit(body: CodDepositIn, request: Request):
    if body.counted < 0:
        raise HTTPException(400, "The counted amount can't be negative.")
    try:
        return cod.deposit(body.driverId, body.counted, body.reference, _who(request), body.note)
    except ValueError as e:
        raise HTTPException(409, str(e))


@app.post("/api/cod/remit")
async def cod_remit(body: CodRemitIn, request: Request):
    try:
        return cod.remit(body.merchant, body.reference, _who(request))
    except ValueError as e:
        raise HTTPException(409, str(e))


class ConfirmIn(BaseModel):
    action: str                  # request | confirm | reschedule | cancel
    window: str | None = None    # morning | afternoon | evening | any
    day: float | None = None     # reschedule: the day (epoch seconds)
    landmark: str | None = None
    note: str | None = None


def _apply_confirmation(parcel: dict, body: ConfirmIn, actor: str) -> dict:
    try:
        if body.action == "request":
            return risk.request_confirmation(parcel, actor)
        return risk.respond(parcel, body.action, window=body.window, day=body.day, landmark=body.landmark,
                            actor=actor, note=body.note)
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.get("/api/risk/queue")
async def risk_queue():
    return risk.queue()


@app.get("/api/risk/kpis")
async def risk_kpis(days: int = 30):
    return risk.first_attempt(days or None)


@app.get("/api/parcels/{parcel_id}/risk")
async def parcel_risk(parcel_id: str):
    return risk.assess(_entity_or_404("parcels", parcel_id))


@app.post("/api/parcels/{parcel_id}/confirmation")
async def parcel_confirmation(parcel_id: str, body: ConfirmIn, request: Request):
    parcel = _entity_or_404("parcels", parcel_id)
    _apply_confirmation(parcel, body, _who(request))
    if body.action in ("reschedule", "cancel") and parcel["status"] == "assigned" and parcel.get("tripId"):
        try:
            await replan.replan(parcel["tripId"], remove_ids=[parcel["id"]])
        except delivery.RunError:
            pass
        parcel = db.load_entity("parcels", parcel_id)
    if body.action == "cancel" and parcel["status"] in delivery.PARCEL_PLANNABLE_STATUSES:
        delivery.set_parcel_status(parcel, "returned", trip_id=None, note="Cancelled by the receiver before dispatch")
    store.broadcast_global("parcel", {"parcel": parcel, "deleted": False})
    return parcel


@app.post("/api/parcels/{parcel_id}/use-known-pin")
async def parcel_use_known_pin(parcel_id: str):
    parcel = _entity_or_404("parcels", parcel_id)
    if parcel["status"] not in delivery.PARCEL_PLANNABLE_STATUSES:
        raise HTTPException(409, "This parcel is on a run: change its pin before it is planned.")
    try:
        return risk.use_known_pin(parcel)
    except ValueError as e:
        raise HTTPException(409, str(e))


@app.post("/api/track/{tracking_number}/respond")
async def track_respond(tracking_number: str, body: ConfirmIn, request: Request):
    """The customer's own answer from the tracking page. Public, rate-limited, and limited to what they may do."""
    _track_rate_limit(request.client.host if request.client else "?")
    if body.action not in ("confirm", "reschedule", "cancel"):
        raise HTTPException(400, "Choose confirm, reschedule or cancel.")
    want = tracking_number.strip().lower()
    parcel = next((p for p in db.all_entities("parcels") if (p.get("trackingNumber") or "").lower() == want), None)
    if not parcel:
        raise HTTPException(404, "We couldn't find that tracking number.")
    if parcel.get("requestType") == "pickup":
        raise HTTPException(409, "Please contact us to change a pickup.")
    _apply_confirmation(parcel, body, "customer")
    if body.action in ("reschedule", "cancel") and parcel["status"] == "assigned" and parcel.get("tripId"):
        try:
            await replan.replan(parcel["tripId"], remove_ids=[parcel["id"]])
        except delivery.RunError:
            pass
        parcel = db.load_entity("parcels", parcel["id"])
    if body.action == "cancel" and parcel["status"] in delivery.PARCEL_PLANNABLE_STATUSES:
        delivery.set_parcel_status(parcel, "returned", trip_id=None, note="Cancelled by the receiver before dispatch")
    store.broadcast_global("parcel", {"parcel": parcel, "deleted": False})
    return {"ok": True, "state": (parcel.get("confirmation") or {}).get("state")}


class DisruptionIn(BaseModel):
    kind: str = "flood"
    label: str | None = None
    lat: float
    lng: float
    radiusKm: float = 3.0
    until: float | None = None


class AbsentIn(BaseModel):
    reason: str | None = None


class FuelQuotaIn(BaseModel):
    weeklyLitres: float


@app.get("/api/disruptions")
async def disruptions_list():
    return disruptions.list_all()


@app.post("/api/disruptions")
async def disruptions_create(body: DisruptionIn, request: Request):
    try:
        return await disruptions.create(body.kind, body.label or "", body.lat, body.lng, body.radiusKm, body.until, _who(request))
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.post("/api/disruptions/{disruption_id}/clear")
async def disruptions_clear(disruption_id: str, request: Request):
    try:
        return disruptions.clear(disruption_id, _who(request))
    except KeyError:
        raise HTTPException(404, "disruption not found")


@app.post("/api/drivers/{driver_id}/absent")
async def driver_absent(driver_id: str, body: AbsentIn, request: Request):
    try:
        return await disruptions.mark_absent(driver_id, body.reason or "", _who(request))
    except KeyError:
        raise HTTPException(404, "driver not found")


@app.get("/api/capacity")
async def capacity(days: int = 5):
    return {**disruptions.forecast(max(1, min(days, 14))), "fuel": disruptions.fuel_quota()}


@app.put("/api/fuel-quota")
async def fuel_quota_set(body: FuelQuotaIn):
    return disruptions.set_fuel_quota(body.weeklyLitres)
