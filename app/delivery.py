"""Delivery-management domain: parcels, fleet registry, eligibility, depot.

Run creation lives here too (added with the runs endpoint) so phase 2's
dispatch agent can reuse it programmatically.
"""

import os
import secrets
import time
import uuid

from . import db, notify, routing, store

SIZES = ["small", "medium", "large"]
SIZE_ORDER = {s: i for i, s in enumerate(SIZES)}

# registry form defaults per vehicle type (editable per record)
VEHICLE_DEFAULTS = {
    "bike": {"maxParcelSize": "small", "capacity": 2},
    "car": {"maxParcelSize": "medium", "capacity": 4},
    "van": {"maxParcelSize": "large", "capacity": 8},
    "truck": {"maxParcelSize": "large", "capacity": 20},
}

# cargo-space defaults per vehicle type: interior L×W×H in cm + payload in kg
CARGO_DEFAULTS = {
    "bike": {"cargoCm": {"l": 40, "w": 30, "h": 30}, "maxPayloadKg": 10},
    "car": {"cargoCm": {"l": 100, "w": 50, "h": 45}, "maxPayloadKg": 200},
    "van": {"cargoCm": {"l": 300, "w": 170, "h": 130}, "maxPayloadKg": 1000},
    "truck": {"cargoCm": {"l": 500, "w": 200, "h": 200}, "maxPayloadKg": 5000},
}
# share of the cargo volume parcels can realistically fill (packing gaps)
PACKING_EFFICIENCY = 0.85

PRIORITIES = ("standard", "priority", "express")
PRIORITY_LABEL = {"standard": "Standard", "priority": "Priority", "express": "Express"}

# a parcel committed to a run (can't be edited/deleted or picked for a new run)
PARCEL_ACTIVE_STATUSES = ("assigned", "in_transit", "picked_up")
# a parcel that can be selected for a (new) run
PARCEL_PLANNABLE_STATUSES = ("pending", "awaiting_redelivery")

# why an attempt failed (code -> label shown to drivers and dispatchers)
FAIL_REASONS = {
    "no_one_home": "Nobody home",
    "wrong_address": "Wrong or incomplete address",
    "refused": "Receiver refused the parcel",
    "damaged": "Parcel damaged",
    "access_problem": "Couldn't get access (gate, road, security)",
    "not_ready": "Shipment not ready for pickup",
    "customer_unavailable": "Customer unavailable",
    "cancelled": "Customer cancelled the pickup",
    "other": "Other reason",
}
DELIVERY_FAIL_REASONS = ("no_one_home", "wrong_address", "refused", "damaged", "access_problem", "other")
PICKUP_FAIL_REASONS = ("not_ready", "customer_unavailable", "wrong_address", "access_problem", "cancelled", "other")
# a shipment that has failed this many times is flagged in review as a candidate for return
ESCALATE_AFTER = 3
# how proof of delivery was captured
POD_METHODS = {
    "handed_over": "Handed to the receiver",
    "signature": "Signed for",
    "photo": "Photo taken",
    "left_safe": "Left in a safe place",
}


def day_start(ts: float) -> float:
    t = time.localtime(ts)
    return time.mktime((t.tm_year, t.tm_mon, t.tm_mday, 0, 0, 0, 0, 0, -1))


def is_plannable(p: dict, now: float | None = None) -> bool:
    """Can this parcel go on a run right now? Pending or back at the hub, and not
    held for a later day (a reviewed failure rescheduled for tomorrow waits)."""
    now = now or time.time()
    return p["status"] in PARCEL_PLANNABLE_STATUSES and not (p.get("holdUntil") and p["holdUntil"] > now)


def _new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:6]}"


def stop_kind(stop: dict) -> str:
    """Back-compat: pre-pickup stops have no `kind`."""
    return stop.get("kind") or ("depot" if stop.get("depot") else "delivery")


def add_event(parcel: dict, kind: str, message: str, **extra) -> dict:
    """Append to the parcel's history (caller saves). `kind` is a short code
    (created, assigned, out_for_delivery, collected, delivered, failed,
    rescheduled, returned, pod, edited …)."""
    event = {"ts": time.time(), "type": kind, "message": message, **extra}
    parcel.setdefault("events", []).append(event)
    return event


def timeline(parcel: dict) -> list[dict]:
    """The parcel's history, oldest first. Parcels created before history was
    recorded get a best-effort timeline from their timestamps."""
    if parcel.get("events"):
        return sorted(parcel["events"], key=lambda e: e["ts"])
    out = [{"ts": parcel.get("createdAt") or 0, "type": "created", "message": "Added to the system"}]
    if parcel.get("pickedUpAt"):
        out.append({"ts": parcel["pickedUpAt"], "type": "collected", "message": "Collected"})
    if parcel.get("deliveredAt"):
        late = " (after the deadline)" if parcel.get("deadlineMissed") else ""
        out.append({"ts": parcel["deliveredAt"], "type": "delivered", "message": "Delivered" + late})
    return out


def new_parcel(data: dict) -> dict:
    parcel = {
        "id": _new_id("p"),
        "name": data["name"],
        "trackingNumber": ((data.get("trackingNumber") or "").strip()
                           or f"FO-{uuid.uuid4().hex[:8].upper()}"),
        "receiverName": (data.get("receiverName") or "").strip() or None,
        "type": data.get("type", "general"),
        "size": data["size"],
        "priority": data.get("priority") or "standard",
        "windowStart": data.get("windowStart"),   # earliest delivery time (epoch) or None
        "receiverEmail": (data.get("receiverEmail") or "").strip() or None,
        "dimensionsCm": data.get("dimensionsCm"),   # {l,w,h} cm or None
        "destination": data["destination"],
        "pickup": data.get("pickup"),      # {lat,lng,label,earliest?,latest?} or None
        "pickedUpAt": None,
        "deadline": data["deadline"],
        "weightKg": data.get("weightKg"),        # optional — kg, or None
        "fragile": bool(data.get("fragile")),
        "recipientPhone": (data.get("recipientPhone") or "").strip() or None,
        "notes": (data.get("notes") or "").strip() or None,
        "requestType": data.get("requestType") or "delivery",   # delivery | pickup (a customer's pickup request)
        "earlyOk": bool(data.get("earlyOk")),   # pickup: shipment is ready AND the customer confirmed it may be collected before their cut-off
        "status": "pending",
        "deadlineMissed": False,
        "tripId": None,
        "deliveredAt": None,
        "createdAt": time.time(),
        "failedAttempts": 0,
        "lastFailure": None,    # {reason, label, note, ts, tripId}
        "review": None,         # {state: pending|done, decision, note, by, ts} — the separate rescheduling process
        "holdUntil": None,      # epoch: not plannable before this (rescheduled for a later day)
        "pod": None,            # proof-of-delivery meta; images live in the pods table
        "events": [],
    }
    add_event(parcel, "created", f"Added to the system ({parcel['trackingNumber']})")
    db.save_entity("parcels", parcel)
    return parcel


def new_vehicle(data: dict) -> dict:
    defaults = VEHICLE_DEFAULTS.get(data.get("type", "car"), VEHICLE_DEFAULTS["car"])
    cargo = CARGO_DEFAULTS.get(data.get("type", "car"), CARGO_DEFAULTS["car"])
    vehicle = {
        "id": _new_id("v"),
        "name": data["name"],
        "plate": data.get("plate", ""),
        "type": data.get("type", "car"),
        "capacity": int(data.get("capacity") or defaults["capacity"]),
        "maxParcelSize": data.get("maxParcelSize") or defaults["maxParcelSize"],
        "cargoCm": data.get("cargoCm") or dict(cargo["cargoCm"]),
        "maxPayloadKg": data.get("maxPayloadKg") or cargo["maxPayloadKg"],
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
        "shiftStart": data.get("shiftStart") or None,    # "HH:MM", blank = always on
        "shiftEnd": data.get("shiftEnd") or None,
        "onLeave": bool(data.get("onLeave")),
        "token": secrets.token_urlsafe(12),             # secret key in the driver's link
        "maxStops": data.get("maxStops") or None,
        "maxHours": data.get("maxHours") or None,
        "breakMinutes": data.get("breakMinutes") if data.get("breakMinutes") is not None else 30,
        "createdAt": time.time(),
    }
    db.save_entity("drivers", driver)
    return driver


def _vehicle_cargo(vehicle: dict) -> tuple[dict, float]:
    """Cargo box + payload, falling back to the type default for vehicles
    registered before dimensions existed."""
    d = CARGO_DEFAULTS.get(vehicle.get("type"), CARGO_DEFAULTS["car"])
    return (vehicle.get("cargoCm") or d["cargoCm"],
            vehicle.get("maxPayloadKg") or d["maxPayloadKg"])


def _volume_cm3(dims: dict) -> float:
    return dims["l"] * dims["w"] * dims["h"]


def fits_in_cargo(dims: dict, cargo: dict) -> bool:
    """Can a box fit the cargo space in some orientation? Sorting both sides'
    edges and comparing pairwise is exact for axis-aligned boxes."""
    return all(p <= c for p, c in zip(sorted((dims["l"], dims["w"], dims["h"])),
                                      sorted((cargo["l"], cargo["w"], cargo["h"]))))


def parcel_unfit_reason(vehicle: dict, parcel: dict) -> str | None:
    """Why this single parcel can never go in this vehicle (size class,
    dimensions or weight), or None."""
    limit = SIZE_ORDER.get(vehicle.get("maxParcelSize", "large"), 2)
    if SIZE_ORDER.get(parcel["size"], 0) > limit:
        return (f"max parcel size {vehicle.get('maxParcelSize')} "
                f"< {parcel['size']} ({parcel['name']})")
    cargo, payload = _vehicle_cargo(vehicle)
    dims = parcel.get("dimensionsCm")
    if dims and not fits_in_cargo(dims, cargo):
        return (f"{parcel['name']} ({dims['l']}×{dims['w']}×{dims['h']} cm) doesn't fit "
                f"the {cargo['l']}×{cargo['w']}×{cargo['h']} cm cargo space")
    if parcel.get("weightKg") and parcel["weightKg"] > payload:
        return f"{parcel['name']} ({parcel['weightKg']:g} kg) exceeds the {payload:g} kg payload"
    return None


def _hhmm(text: str | None) -> int | None:
    try:
        h, m = (text or "").split(":")
        return int(h) * 60 + int(m)
    except ValueError:
        return None


def driver_unavailable_reason(driver: dict, at: float | None = None) -> str | None:
    """Why this driver can't start work now (on leave / outside their shift)."""
    if driver.get("onLeave"):
        return "on leave"
    start, end = _hhmm(driver.get("shiftStart")), _hhmm(driver.get("shiftEnd"))
    if start is None or end is None:
        return None
    t = time.localtime(at or time.time())
    minute = t.tm_hour * 60 + t.tm_min
    if not (start <= minute < end):
        return f"off shift (works {driver['shiftStart']}–{driver['shiftEnd']})"
    return None


def estimate_run_minutes(parcels: list[dict]) -> float:
    """Rough working time for a run before it is routed: nearest-neighbour
    straight-line legs from the hub × 1.35 road factor at 28 km/h, plus 6 min per
    stop (8 for a collect-and-deliver parcel)."""
    from . import routing
    cur, metres = get_depot(), 0.0
    rest = [p["pickup"] if p.get("requestType") == "pickup" and p.get("pickup") else p["destination"] for p in parcels]
    while rest:
        nxt = min(rest, key=lambda d: routing._haversine_m(cur, d))
        metres += routing._haversine_m(cur, nxt)
        rest.remove(nxt)
        cur = nxt
    stops = sum(2 if p.get("pickup") and p["status"] != "awaiting_redelivery" else 1 for p in parcels)
    return metres * 1.35 / 1000 / 28 * 60 + 6 * stops + 2 * sum(1 for p in parcels if p.get("pickup"))


def check_driver_limits(driver: dict, parcels: list[dict], minutes: float | None = None) -> str | None:
    """Driver-level limits (max stops, max hours, shift end). None when fine."""
    if driver.get("maxStops") and len(parcels) > driver["maxStops"]:
        return f"{driver['name']} takes at most {driver['maxStops']} stops"
    minutes = estimate_run_minutes(parcels) if minutes is None else minutes
    brk = driver.get("breakMinutes") or 0
    total = minutes + (brk if minutes > 240 else 0)
    if driver.get("maxHours") and total > driver["maxHours"] * 60:
        return (f"about {total / 60:.1f} h of work is over {driver['name']}'s "
                f"{driver['maxHours']:g} h limit")
    end = _hhmm(driver.get("shiftEnd"))
    if end is not None:
        t = time.localtime()
        if t.tm_hour * 60 + t.tm_min + total > end:
            return f"would run past {driver['name']}'s shift end ({driver['shiftEnd']})"
    return None


def check_eligibility(vehicle: dict, parcels: list[dict], driver: dict | None = None) -> str | None:
    """None if the vehicle (and driver, when given) can take the parcel set,
    else a human reason. Checks count, size class, per-parcel dimensions, total
    weight and total volume (parcels without dimensions are skipped for
    volume/fit), then the driver's availability and limits."""
    if driver:
        away = driver_unavailable_reason(driver)
        if away:
            return f"{driver['name']} is {away}"
    if len(parcels) > vehicle["capacity"]:
        return f"capacity {vehicle['capacity']} < {len(parcels)} parcels"
    for p in parcels:
        reason = parcel_unfit_reason(vehicle, p)
        if reason:
            return reason
    cargo, payload = _vehicle_cargo(vehicle)
    weight = sum(p.get("weightKg") or 0 for p in parcels)
    if weight > payload:
        return f"total weight {weight:g} kg exceeds the {payload:g} kg payload"
    volume = sum(_volume_cm3(p["dimensionsCm"]) for p in parcels if p.get("dimensionsCm"))
    usable = _volume_cm3(cargo) * PACKING_EFFICIENCY
    if volume > usable:
        return (f"parcels need {volume / 1e6:.2f} m³ but only "
                f"{usable / 1e6:.2f} m³ is usable in the cargo space")
    if driver:
        return check_driver_limits(driver, parcels)
    return None


def load_fraction(vehicle: dict, parcels: list[dict]) -> float:
    """Fullest of the count / weight / volume limits, 0–1+ (for summaries)."""
    cargo, payload = _vehicle_cargo(vehicle)
    vol = sum(_volume_cm3(p["dimensionsCm"]) for p in parcels if p.get("dimensionsCm"))
    return max(len(parcels) / vehicle["capacity"],
               sum(p.get("weightKg") or 0 for p in parcels) / payload,
               vol / (_volume_cm3(cargo) * PACKING_EFFICIENCY))


def entity_in_use(kind: str, entity_id: str) -> bool:
    """Is a vehicle/driver referenced by any not-completed trip?"""
    field = {"vehicles": "vehicleId", "drivers": "driverId"}[kind]
    return any(t.get(field) == entity_id and t["status"] != "completed"
               for t in store.list_trips())


_STATUS_EVENT = {
    "pending": ("unassigned", "Back on the pending list"),
    "assigned": ("assigned", "Assigned to a run"),
    "in_transit": ("out_for_delivery", "Out for delivery"),
    "picked_up": ("collected", "Collected from the pickup point"),
    "delivered": ("delivered", "Delivered"),
    "awaiting_redelivery": ("returned_to_hub", "Back at the hub, waiting to be redelivered"),
    "returned": ("returned", "Returned to sender"),
}


def set_parcel_status(parcel: dict, status: str, trip_id=None, ts=None, note=None) -> dict:
    previous = parcel["status"]
    parcel["status"] = status
    if trip_id is not None or status in ("pending", "awaiting_redelivery", "returned", "failed"):
        parcel["tripId"] = trip_id
    request = parcel.get("requestType") == "pickup"
    if status == "picked_up":
        parcel["pickedUpAt"] = ts or time.time()
        if request:
            parcel["deadlineMissed"] = parcel["pickedUpAt"] > parcel["deadline"]
    if status == "delivered":
        parcel["deliveredAt"] = ts or time.time()
        if not request:
            parcel["deadlineMissed"] = parcel["deliveredAt"] > parcel["deadline"]
    if status != previous and status in _STATUS_EVENT:
        kind, message = _STATUS_EVENT[status]
        if note:
            message = note
        elif status == "assigned" and trip_id:
            trip = store.get_trip(trip_id)
            driver = trip and trip.get("driverId") and db.load_entity("drivers", trip["driverId"])
            message = f"Assigned to run {trip_id}" + (f" ({driver['name']})" if driver else "")
        elif status == "in_transit" and trip_id:
            message = (f"Driver on the way to collect on run {trip_id}" if request
                       else f"Out for delivery on run {trip_id}")
        elif status == "delivered" and request:
            message = "Received at the hub"
        elif status == "delivered" and parcel["deadlineMissed"]:
            message = "Delivered after the deadline"
        add_event(parcel, kind, message, tripId=trip_id or parcel.get("tripId"))
    db.save_entity("parcels", parcel)
    store.broadcast_global("parcel", {"parcel": parcel, "deleted": False})
    if status != previous:
        _notify_for_status(parcel, status)
    return parcel


def _notify_for_status(parcel: dict, status: str) -> None:
    try:
        if status == "in_transit":
            trip = parcel.get("tripId") and store.get_trip(parcel["tripId"])
            eta = next((e["eta"] for e in (trip or {}).get("plannedEtaPerStop") or []
                        if e.get("parcelId") == parcel["id"] and e.get("kind") == "delivery"), None)
            notify.notify(parcel, "out_for_delivery", eta=eta)
        elif status == "delivered":
            notify.notify(parcel, "delivered", receivedBy=(parcel.get("pod") or {}).get("receivedBy"))
        elif status == "returned":
            notify.notify(parcel, "returned")
    except Exception:                      # customer messaging must never block a delivery
        notify.log.exception("notification failed")


def record_failed_attempt(parcel: dict, reason: str, note: str | None,
                          trip_id: str | None, retry_at: float | None = None,
                          actor: str = "driver") -> dict:
    """A delivery (or pickup) attempt failed. The shipment stops in `failed` and
    waits for a dispatcher to review the reason; nothing reschedules or returns it
    automatically. `retry_at` is accepted for compatibility and ignored — a date is
    only set in the review."""
    label = FAIL_REASONS[reason]
    parcel["failedAttempts"] = parcel.get("failedAttempts", 0) + 1
    parcel["lastFailure"] = {"reason": reason, "label": label, "note": note or None,
                             "ts": time.time(), "tripId": trip_id, "by": actor}
    parcel["review"] = {"state": "pending", "since": time.time()}
    attempt = parcel["failedAttempts"]
    what = "Pickup" if parcel.get("requestType") == "pickup" else "Delivery"
    add_event(parcel, "failed", f"{what} attempt {attempt} failed: {label}"
              + (f" — {note}" if note else ""), tripId=trip_id, reason=reason, by=actor)
    set_parcel_status(parcel, "failed", trip_id=None)
    try:
        notify.notify(parcel, "failed", reason=label)
    except Exception:
        notify.log.exception("notification failed")
    return parcel


def collect_pickup(parcel: dict, trip_id: str | None, actor: str = "driver",
                   note: str | None = None) -> dict:
    """The driver has the shipment from the customer. Lateness is judged now."""
    if parcel["status"] not in ("in_transit", "assigned"):
        raise ValueError("This pickup isn't out on a run.")
    set_parcel_status(parcel, "picked_up", note="Collected from the customer"
                      + (f" — {note}" if note else ""))
    return parcel


def confirm_early(parcel: dict, actor: str = "dispatcher", note: str | None = None) -> dict:
    """Allow collecting before the customer's cut-off: only when the shipment is
    ready AND the customer has confirmed it (the dispatcher records that)."""
    if parcel.get("requestType") != "pickup":
        raise ValueError("Only a pickup request has a customer cut-off.")
    if parcel["status"] in ("picked_up", "delivered", "returned"):
        raise ValueError("This pickup has already been collected or closed.")
    parcel["earlyOk"] = True
    add_event(parcel, "edited", "Early collection confirmed with the customer" + (f": {note}" if note else ""), by=actor)
    db.save_entity("parcels", parcel)
    store.broadcast_global("parcel", {"parcel": parcel, "deleted": False})
    return parcel


def apply_schedule(parcel: dict, when: float) -> None:
    """Give a parcel a new delivery (or collection cut-off) time. A time on a later
    day also holds the parcel out of today's planning."""
    parcel["deadline"] = when
    parcel["deadlineMissed"] = False
    if parcel.get("requestType") == "pickup" and parcel.get("pickup"):
        parcel["pickup"]["latest"] = when
    parcel["holdUntil"] = day_start(when) if day_start(when) > time.time() else None


def review_failed(parcel: dict, decision: str, note: str | None, scheduled_for: float | None = None,
                  destination: dict | None = None, actor: str = "dispatcher") -> dict:
    """The review of a failed shipment: reschedule it (the date comes from the
    customer, e.g. the next day) or return it. Only a parcel in `failed` can be reviewed."""
    if parcel["status"] != "failed":
        raise ValueError("Only a failed shipment can be reviewed.")
    note = (note or "").strip() or None
    parcel["review"] = {"state": "done", "decision": decision, "note": note, "by": actor, "ts": time.time()}
    add_event(parcel, "review", "Reviewed" + (f": {note}" if note else ""), by=actor)
    if decision == "return":
        what = "Pickup cancelled" if parcel.get("requestType") == "pickup" else "Returned to sender"
        set_parcel_status(parcel, "returned", trip_id=None, note=what + " after review")
        return parcel
    if not scheduled_for or scheduled_for < time.time():
        raise ValueError("Choose a new time in the future.")
    if destination and parcel.get("requestType") != "pickup":
        parcel["destination"] = destination
        add_event(parcel, "edited", f"Delivery address changed to {destination['label']}")
    apply_schedule(parcel, scheduled_for)
    add_event(parcel, "rescheduled", "Rescheduled for " + time.strftime("%d %b %H:%M", time.localtime(scheduled_for)),
              by=actor)
    # a failed pickup is attempted again from scratch; a failed delivery waits at the hub
    set_parcel_status(parcel, "pending" if parcel.get("requestType") == "pickup" else "awaiting_redelivery", trip_id=None)
    try:
        notify.notify(parcel, "rescheduled", deadline=scheduled_for)
    except Exception:
        notify.log.exception("notification failed")
    return parcel


def save_pod(parcel: dict, method: str, received_by: str | None, note: str | None,
             signature: str | None, photo: str | None, trip_id: str | None,
             lat: float | None, lng: float | None, actor: str = "driver",
             reference: str | None = None) -> dict:
    """Attach proof of delivery. Images go to the pods table so parcel lists
    and live updates stay small."""
    parcel["pod"] = {
        "method": method, "label": POD_METHODS[method],
        "receivedBy": received_by or None, "note": note or None,
        "reference": reference or None,        # id of the signature/receipt in the existing e-signature process
        "hasSignature": bool(signature), "hasPhoto": bool(photo),
        "ts": time.time(), "tripId": trip_id, "by": actor,
        "position": {"lat": lat, "lng": lng} if lat is not None and lng is not None else None,
    }
    db.save_entity("pods", {"id": parcel["id"], "createdAt": time.time(),
                            "signature": signature, "photo": photo})
    detail = (POD_METHODS[method] + (f" — {received_by}" if received_by else "")
              + (f" (ref {reference})" if reference else ""))
    add_event(parcel, "pod", "Proof of delivery: " + detail, tripId=trip_id, by=actor)
    return parcel


def flag_overdue(parcels: list[dict]) -> list[dict]:
    """Lazily mark undelivered, overdue parcels; persists + broadcasts changes."""
    now = time.time()
    for p in parcels:
        collected = p.get("requestType") == "pickup" and p["status"] in ("picked_up", "delivered")
        if p["status"] != "delivered" and not collected and not p["deadlineMissed"] and now > p["deadline"]:
            p["deadlineMissed"] = True
            db.save_entity("parcels", p)
            store.broadcast_global("parcel", {"parcel": p, "deleted": False})
    return parcels


# time the driver spends at each stop (parking, hand-over, signature), added to every promised ETA
SERVICE_SECONDS = float(os.environ.get("STOP_SERVICE_MIN", "4")) * 60


def compute_planned_etas(route: dict, parcels_by_id: dict, departure_ts: float) -> list[dict]:
    """Per parcel-stop ETA (cumulative leg durations). Delivery stops are at
    risk when the ETA is past the deadline; pickup stops when it falls outside
    the parcel's collection window."""
    etas, elapsed = [], 0.0
    for i, stop in enumerate(route["orderedStops"]):
        if i > 0:
            elapsed += route["legs"][i - 1]["durationSeconds"] + (SERVICE_SECONDS if i > 1 else 0)
        pid = stop.get("parcelId")
        if not pid or pid not in parcels_by_id:
            continue
        p = parcels_by_id[pid]
        eta = departure_ts + elapsed
        if stop_kind(stop) == "pickup":
            w = p.get("pickup") or {}
            earliest, latest = w.get("earliest"), w.get("latest")
            at_risk = (latest is not None and eta > latest) or \
                      (earliest is not None and eta < earliest and not p.get("earlyOk"))
            etas.append({"stopIndex": i, "parcelId": pid, "kind": "pickup",
                         "eta": eta, "earliest": earliest, "latest": latest,
                         "atRisk": at_risk})
        else:
            ws = p.get("windowStart")
            etas.append({"stopIndex": i, "parcelId": pid, "kind": "delivery",
                         "eta": eta, "deadline": p["deadline"], "windowStart": ws,
                         "early": bool(ws and eta < ws),
                         # a pickup request's hub drop has no customer deadline
                         "atRisk": eta > p["deadline"] and p.get("requestType") != "pickup"})
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
        request = p.get("requestType") == "pickup"
        if pk and p["status"] not in ("awaiting_redelivery", "picked_up"):
            stops.append({"lat": pk["lat"], "lng": pk["lng"], "label": pk["label"],
                          "parcelId": p["id"], "parcelName": p["name"], "kind": "pickup",
                          "place": pk["label"], "priority": p.get("priority", "standard"),
                          # a pickup request is collected from the customer's cut-off; earlier only when confirmed
                          "earliest": None if p.get("earlyOk") else pk.get("earliest"),
                          "latest": pk.get("latest"),
                          **({"requestType": "pickup", "earlyOk": bool(p.get("earlyOk"))} if request else {})})
        if request:
            stops.append({"lat": depot["lat"], "lng": depot["lng"], "label": depot["label"],
                          "parcelId": p["id"], "parcelName": p["name"], "place": depot["label"],
                          "kind": "delivery", "hubReturn": True,
                          "priority": p.get("priority", "standard"), "deadline": p["deadline"]})
            continue
        dest_label = (p["destination"].get("label") or "").strip() or p["name"]
        stops.append({"lat": p["destination"]["lat"], "lng": p["destination"]["lng"],
                      # the stop label is the drop-off place, not the parcel name —
                      # markers show a number, the manifest looks the parcel up by id
                      "label": dest_label, "parcelId": p["id"], "parcelName": p["name"],
                      "place": dest_label, "kind": "delivery",
                      "priority": p.get("priority", "standard"),
                      "windowStart": p.get("windowStart"),
                      "deadline": p["deadline"]})
    return stops


def route_has_pickup(route: dict, parcel_id: str) -> bool:
    """Does this run's route collect the given parcel en route?"""
    return any(stop_kind(s) == "pickup" and s.get("parcelId") == parcel_id
              for s in route["orderedStops"])


async def plan_with_priorities(stops: list[dict], avoid_tolls: bool, vehicle_type: str) -> dict:
    """Route a run that has express/priority parcels or delivery windows in the
    order the dispatcher's priorities demand, without involving the AI planner."""
    ordered = routing.sequence_stops(stops)
    route = await routing.compute_route(ordered, avoid_tolls=avoid_tolls, optimize=False,
                                        vehicle_type=vehicle_type, presequenced=True)
    first = [s for s in ordered[1:] if (s.get("priority") or "standard") != "standard"
             and s.get("kind") == "delivery"]
    route["briefing"] = (
        f"{len(ordered) - 1} stops from the hub. "
        + (f"Express and priority parcels come first ({', '.join(s['parcelName'] for s in first[:3])}"
           f"{'…' if len(first) > 3 else ''}). " if first else "")
        + "Stops with a delivery window are timed so you arrive once the window opens.")
    return route


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
        if not is_plannable(p):
            raise RunError(f"Parcel '{p['name']}' is not available to plan")
        parcels.append(p)
    if not parcels:
        raise RunError("Select at least one parcel", 400)

    reason = check_eligibility(vehicle, parcels, driver)
    if reason:
        raise RunError(f"{vehicle['name']} not eligible: {reason}")

    stops = build_run_stops(parcels)

    if routing.has_special_handling(stops):
        route = await plan_with_priorities(stops, avoid_tolls, vehicle["type"])
    else:
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
    if depot and depot.get("label") == "Colombo Depot":      # renamed: every run starts at the local hub
        depot["label"] = "Colombo Hub"
        db.set_setting("depot", depot)
    if not depot:
        depot = {
            "lat": float(os.environ.get("DEPOT_LAT", "6.9344")),
            "lng": float(os.environ.get("DEPOT_LNG", "79.8428")),
            "label": os.environ.get("DEPOT_LABEL", "Colombo Hub"),
        }
        db.set_setting("depot", depot)
    return depot
