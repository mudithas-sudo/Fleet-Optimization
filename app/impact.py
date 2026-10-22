"""What the optimisation is worth: savings against the way a run would have been
driven without it, plus fuel, CO2, planned-vs-actual and ETA accuracy.

No keys, no API calls — every figure comes from the runs, parcels and position
logs already stored, so it works on any dataset and can be reproduced.

How the savings are measured (kept simple on purpose, so it can be explained):
  * Baseline = the same parcels on the same vehicle, visited in the order they were
    entered (each pickup before its drop, hub drop-offs last) — how a run is built by
    hand, without sequencing.
  * Optimised = the order the planner actually produced.
  * Both are measured with one yardstick (straight-line distance x a road factor), so the
    saving is the difference in routing only, never an artefact of two different methods.
  * Fuel = km x litres/100 km for the vehicle type; CO2 = litres x kg per litre.
Planned-vs-actual and ETA accuracy use what really happened (position log, delivery
times), so they can be worse than the plan — they are reported as they are.
"""

import time

from . import db, delivery, reports, rides, routing, store

ROAD_FACTOR = 1.3          # straight line -> road distance
SPEED_KMH = 28.0           # urban average used to turn km into minutes
STOP_MIN = 6.0             # service time at each stop

DEFAULT_FUEL = {
    "pricePerL": 350.0,                                   # per litre, in the cost currency
    "co2PerL": 2.31,                                      # kg CO2 per litre of fuel
    "l100": {"bike": 2.5, "car": 8.0, "van": 12.0, "truck": 24.0},   # litres per 100 km
}


def get_fuel() -> dict:
    saved = db.get_setting("fuel") or {}
    return {**DEFAULT_FUEL, **{k: v for k, v in saved.items() if k != "l100"},
            "l100": {**DEFAULT_FUEL["l100"], **(saved.get("l100") or {})}}


def set_fuel(price_per_l: float, l100: dict | None = None, co2_per_l: float | None = None) -> dict:
    cur = get_fuel()
    fuel = {"pricePerL": float(price_per_l), "co2PerL": float(co2_per_l or cur["co2PerL"]),
            "l100": {**cur["l100"], **{k: float(v) for k, v in (l100 or {}).items() if k in cur["l100"]}}}
    db.set_setting("fuel", fuel)
    return fuel


def _label(trip: dict) -> str:
    label = rides._label(trip["route"]["orderedStops"])
    return label.replace("Current location", "Hub (re-planned)")


def _km(points: list[dict]) -> float:
    return sum(routing._haversine_m(a, b) for a, b in zip(points, points[1:])) * ROAD_FACTOR / 1000


def _minutes(km: float, stops: int) -> float:
    return km / SPEED_KMH * 60 + STOP_MIN * stops


def _entry_order(trip: dict, parcel_order: list[str]) -> list[dict]:
    """The run's stops in the order a person would have built it: parcels as entered,
    each pickup before its drop, hub drop-offs last."""
    stops = trip["route"]["orderedStops"]
    first, rest = stops[0], stops[1:]
    rank = {pid: i for i, pid in enumerate(parcel_order)}
    kind_rank = {"pickup": 0, "delivery": 1}
    body = [s for s in rest if not s.get("hubReturn")]
    tail = [s for s in rest if s.get("hubReturn")]
    body.sort(key=lambda s: (rank.get(s.get("parcelId"), 10 ** 6), kind_rank.get(delivery.stop_kind(s), 1)))
    return [first] + body + tail


def run_impact(trip: dict, fuel: dict, costs: dict, parcels_by_id: dict) -> dict | None:
    stops = trip["route"]["orderedStops"]
    if len(stops) < 2 or not trip.get("parcelIds"):
        return None
    vehicle_type = trip.get("vehicleType", "car")
    parcel_order = sorted(trip["parcelIds"], key=lambda pid: (parcels_by_id.get(pid) or {}).get("createdAt") or 0)
    opt_km = _km(stops)
    base_km = max(opt_km, _km(_entry_order(trip, parcel_order)))   # never claim a loss as a saving
    n = len(stops) - 1
    l100 = fuel["l100"].get(vehicle_type, fuel["l100"]["car"])

    def money(km, minutes):
        return km * costs["perKm"] + minutes / 60 * costs["perHour"]

    base_min, opt_min = _minutes(base_km, n), _minutes(opt_km, n)
    saved_km = base_km - opt_km
    saved_l = saved_km * l100 / 100
    return {
        "tripId": trip["id"], "label": trip.get("label"), "status": trip["status"], "vehicleType": vehicle_type,
        "stops": n, "baselineKm": round(base_km, 1), "optimisedKm": round(opt_km, 1),
        "savedKm": round(saved_km, 1), "savedPct": round(saved_km / base_km * 100) if base_km else 0,
        "savedMin": round(base_min - opt_min), "savedLitres": round(saved_l, 2),
        "savedFuelCost": round(saved_l * fuel["pricePerL"]),
        "savedCo2Kg": round(saved_l * fuel["co2PerL"], 2),
        "savedCost": round(money(base_km, base_min) - money(opt_km, opt_min)),
        "litres": round(opt_km * l100 / 100, 2), "co2Kg": round(opt_km * l100 / 100 * fuel["co2PerL"], 2),
        "fuelCost": round(opt_km * l100 / 100 * fuel["pricePerL"]),
    }


def _plan_vs_actual(trip: dict) -> dict | None:
    if trip["status"] != "completed" or not trip.get("startedAt") or not trip.get("endedAt"):
        return None
    planned_km = (trip["route"].get("totalDistanceMeters") or 0) / 1000
    actual_km = (trip.get("actualDistanceMeters") or 0) / 1000
    stops = max(0, len(trip["route"]["orderedStops"]) - 1)
    planned_min = (trip["route"].get("totalDurationSeconds") or 0) / 60 + stops * delivery.SERVICE_SECONDS / 60
    actual_min = (trip["endedAt"] - trip["startedAt"]) / 60
    if not planned_km or not actual_km or not planned_min:
        return None
    return {"tripId": trip["id"], "label": _label(trip), "plannedKm": round(planned_km, 1),
            "actualKm": round(actual_km, 1), "kmVarPct": round((actual_km / planned_km - 1) * 100),
            "plannedMin": round(planned_min), "actualMin": round(actual_min),
            "minVarPct": round((actual_min / planned_min - 1) * 100),
            "deviations": sum(1 for a in trip.get("alerts", []) if a["type"] == "deviation")}


def _eta_accuracy(trips: list[dict], parcels_by_id: dict) -> dict:
    errors = []
    for t in trips:
        if t["status"] != "completed":
            continue
        for e in t.get("plannedEtaPerStop") or []:
            p = parcels_by_id.get(e.get("parcelId"))
            if (e.get("kind") == "delivery" and p and p["status"] == "delivered"
                    and p.get("tripId") == t["id"] and p.get("deliveredAt")):
                errors.append(abs(p["deliveredAt"] - e["eta"]) / 60)
    return {"stops": len(errors),
            "within15Pct": round(sum(1 for x in errors if x <= 15) / len(errors) * 100) if errors else None,
            "meanErrorMin": round(sum(errors) / len(errors), 1) if errors else None}


def summary(days: int | None = 7) -> dict:
    since, until = reports.window(days)
    fuel, costs = get_fuel(), reports.get_costs()
    parcels_by_id = {p["id"]: p for p in db.all_entities("parcels")}
    vehicles = {v["id"]: v for v in db.all_entities("vehicles")}
    drivers = {d["id"]: d for d in db.all_entities("drivers")}
    trips = [t for t in store.list_trips() if reports._in(t.get("createdAt"), since, until)]

    rows = []
    for t in trips:
        r = run_impact(t, fuel, costs, parcels_by_id)
        if r:
            r["driver"] = (drivers.get(t.get("driverId")) or {}).get("name")
            r["vehicle"] = (vehicles.get(t.get("vehicleId")) or {}).get("name")
            r["label"] = _label(t)
            rows.append(r)
    rows.sort(key=lambda r: -r["savedKm"])

    tot = lambda k: sum(r[k] for r in rows)
    base_km = sum(r["baselineKm"] for r in rows)
    saved_km = tot("savedKm")
    pva = [x for x in (_plan_vs_actual(t) for t in trips) if x]
    loads = []
    for t in trips:
        v = vehicles.get(t.get("vehicleId"))
        ps = [parcels_by_id[i] for i in t.get("parcelIds") or [] if i in parcels_by_id]
        if v and ps:
            loads.append(min(delivery.load_fraction(v, ps), 1.0) * 100)
    base = reports.summary(days)
    return {
        "period": {"days": days, "since": since, "until": until},
        "currency": costs["currency"], "fuel": fuel, "costs": costs,
        "method": ("Savings compare each run with the same parcels visited in the order they were entered, "
                   "measured on one straight-line x %.1f road-factor yardstick. Fuel = km x litres/100 km "
                   "for the vehicle type." % ROAD_FACTOR),
        "savings": {
            "runs": len(rows), "baselineKm": round(base_km, 1), "optimisedKm": round(tot("optimisedKm"), 1),
            "savedKm": round(saved_km, 1), "savedPct": round(saved_km / base_km * 100) if base_km else 0,
            "savedHours": round(tot("savedMin") / 60, 1), "savedLitres": round(tot("savedLitres"), 1),
            "savedFuelCost": round(tot("savedFuelCost")), "savedCo2Kg": round(tot("savedCo2Kg"), 1),
            "savedCost": round(tot("savedCost")),
            "perYear": {  # what a repeat of this period's saving is worth over a year (simple scaling)
                "factor": _per_year_factor(days, since, until),
            },
        },
        "footprint": {"km": round(tot("optimisedKm"), 1), "litres": round(tot("litres"), 1),
                      "co2Kg": round(tot("co2Kg"), 1), "fuelCost": round(tot("fuelCost"))},
        "perRun": rows,
        "planVsActual": {"runs": pva, **_pva_totals(pva)},
        "eta": _eta_accuracy(trips, parcels_by_id),
        "utilisation": {"avgLoadPct": round(sum(loads) / len(loads)) if loads else None, "runs": len(loads),
                        "parcelsPerRun": round(sum(len(t.get("parcelIds") or []) for t in trips) / len(trips), 1) if trips else None},
        "service": {"onTimeRate": base["parcels"]["onTimeRate"], "firstAttemptRate": base["parcels"]["firstAttemptRate"],
                    "delivered": base["parcels"]["delivered"], "failedAttempts": base["parcels"]["failedAttempts"],
                    "costPerDelivery": base["cost"]["perDelivery"]},
    }


def _per_year_factor(days, since, until) -> float | None:
    span_days = (until - since) / 86400 if days is not None else None
    return round(365 / span_days, 1) if span_days and span_days > 0 else None


def _pva_totals(pva: list[dict]) -> dict:
    if not pva:
        return {"plannedKm": None, "actualKm": None, "kmVarPct": None, "plannedMin": None, "actualMin": None, "minVarPct": None}
    pk, ak = sum(x["plannedKm"] for x in pva), sum(x["actualKm"] for x in pva)
    pm, am = sum(x["plannedMin"] for x in pva), sum(x["actualMin"] for x in pva)
    return {"plannedKm": round(pk, 1), "actualKm": round(ak, 1), "kmVarPct": round((ak / pk - 1) * 100),
            "plannedMin": round(pm), "actualMin": round(am), "minVarPct": round((am / pm - 1) * 100)}
