"""Deterministic dispatch: assign parcels to the fewest vehicles.

No LLM, no keys — the same answer every time. It reuses delivery.check_eligibility
so count, size class, dimensions, weight and volume limits all apply, and it
returns the same shape as dispatcher.service.propose_dispatch so the admin UI
and /api/dispatch/approve treat both engines alike.

Algorithm (sweep + largest-vehicle-first):
  1. A parcel that no vehicle in the fleet could ever carry is flagged
     unassigned with the reason (too big, too heavy …).
  2. Free drivers (no unfinished run) are ordered by cargo volume, largest first,
     so the biggest vans fill before smaller ones are used at all.
  3. Remaining parcels are sorted by bearing around the hub, starting from the
     most urgent parcel's bearing, so each van gets a compact wedge of the city.
  4. Each van takes parcels in that order while the load still fits; parcels that
     don't fit are skipped and left for the next van.
"""

import math
import time

from .. import db, delivery, store


def _bearing(depot: dict, p: dict) -> float:
    dest = p["destination"]
    return math.atan2(dest["lng"] - depot["lng"], dest["lat"] - depot["lat"])


def _free_drivers() -> list[tuple[dict, dict]]:
    busy = {t.get("driverId") for t in store.list_trips() if t["status"] != "completed"}
    out = []
    for d in db.all_entities("drivers"):
        v = db.load_entity("vehicles", d.get("vehicleId") or "")
        if v and d["id"] not in busy:
            out.append((d, v))

    def volume(pair):
        cargo, _ = delivery._vehicle_cargo(pair[1])
        return delivery._volume_cm3(cargo)

    out.sort(key=volume, reverse=True)
    return out


def propose_by_rules(parcel_ids: list[str] | None = None) -> dict:
    now = time.time()
    scope = set(parcel_ids) if parcel_ids else None
    parcels = [p for p in delivery.flag_overdue(db.all_entities("parcels"))
               if p["status"] in delivery.PARCEL_PLANNABLE_STATUSES
               and (scope is None or p["id"] in scope)]
    if not parcels:
        raise RuntimeError("No pending parcels to assign.")

    fleet = [(d, db.load_entity("vehicles", d.get("vehicleId") or ""))
             for d in db.all_entities("drivers")]
    fleet = [(d, v) for d, v in fleet if v]
    if not fleet:
        raise RuntimeError("Add vehicles and drivers in the Registry tab first.")

    unassigned, workable = [], []
    for p in parcels:
        reasons = [delivery.parcel_unfit_reason(v, p) for _, v in fleet]
        if all(reasons):
            unassigned.append({"parcelId": p["id"], "name": p["name"],
                               "reason": f"No vehicle in the fleet can carry it — {reasons[0]}"})
        else:
            workable.append(p)

    depot = delivery.get_depot()
    workable.sort(key=lambda p: _bearing(depot, p))
    if workable:
        urgent = min(range(len(workable)), key=lambda i: workable[i]["deadline"])
        workable = workable[urgent:] + workable[:urgent]

    batches, remaining = [], workable
    for driver, vehicle in _free_drivers():
        if not remaining:
            break
        load, left = [], []
        for p in remaining:
            if delivery.check_eligibility(vehicle, load + [p]) is None:
                load.append(p)
            else:
                left.append(p)
        if not load:
            continue
        remaining = left
        pct = round(delivery.load_fraction(vehicle, load) * 100)
        late = [p for p in load if p["deadline"] < now]
        batches.append({
            "driverId": driver["id"],
            "driverName": driver["name"],
            "vehicleType": vehicle["type"],
            "vehicleName": vehicle["name"],
            "vehiclePlate": vehicle.get("plate", ""),
            "parcelIds": [p["id"] for p in load],
            "parcelNames": [p["name"] for p in load],
            "rationale": (f"{len(load)} parcel{'s' if len(load) != 1 else ''} in the same "
                          f"part of the city; {vehicle['name']} is {pct}% full."),
            "riskNotes": (f"{len(late)} parcel{'s are' if len(late) != 1 else ' is'} "
                          "already past the deadline." if late else ""),
        })

    for p in remaining:
        unassigned.append({"parcelId": p["id"], "name": p["name"],
                           "reason": "No free vehicle with enough room left — "
                                     "finish a run or add a vehicle."})

    used = len(batches)
    return {
        "engine": "rules",
        "batches": batches,
        "unassigned": unassigned,
        "summary": (f"{len(parcels) - len(unassigned)} of {len(parcels)} parcels fit on "
                    f"{used} vehicle{'s' if used != 1 else ''}"
                    + (f"; {len(unassigned)} could not be assigned." if unassigned else ".")),
        "warnings": [],
    }
