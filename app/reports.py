"""Operations reporting: on-time rate, per-driver productivity, failures,
distance and cost per delivery — plus CSV exports.

"Period" means activity that happened in the window: parcels delivered in it,
failed attempts logged in it, runs created in it. Costs use two editable rates
(per km driven, per hour worked); distance and hours are what was actually
driven when the run finished, else the planned figures.
"""

import csv
import io
import time

from . import db, delivery, store

DEFAULT_COSTS = {"perKm": 60.0, "perHour": 600.0, "currency": "LKR"}


def get_costs() -> dict:
    return {**DEFAULT_COSTS, **(db.get_setting("costs") or {})}


def set_costs(perKm: float, perHour: float, currency: str) -> dict:
    costs = {"perKm": float(perKm), "perHour": float(perHour),
             "currency": (currency or "").strip()[:6] or DEFAULT_COSTS["currency"]}
    db.set_setting("costs", costs)
    return costs


def window(days: int | None, now: float | None = None) -> tuple[float, float]:
    """(since, until) epoch seconds. days=0 means since local midnight; None = all time."""
    now = now or time.time()
    if days is None:
        return 0.0, now + 1
    if days == 0:
        t = time.localtime(now)
        midnight = time.mktime((t.tm_year, t.tm_mon, t.tm_mday, 0, 0, 0, 0, 0, -1))
        return midnight, now + 1
    return now - days * 86400, now + 1


def _in(ts: float | None, since: float, until: float) -> bool:
    return ts is not None and since <= ts < until


def _trip_metrics(trip: dict) -> dict:
    planned_km = (trip["route"].get("totalDistanceMeters") or 0) / 1000
    driven = trip.get("actualDistanceMeters")
    km = driven / 1000 if driven else planned_km
    if trip.get("startedAt") and trip.get("endedAt"):
        hours = (trip["endedAt"] - trip["startedAt"]) / 3600
    else:
        hours = (trip["route"].get("totalDurationSeconds") or 0) / 3600
    return {"plannedKm": planned_km, "km": km, "hours": hours,
            "deviations": sum(1 for a in trip.get("alerts", []) if a["type"] == "deviation")}


def summary(days: int | None = 7) -> dict:
    since, until = window(days)
    costs = get_costs()
    parcels = db.all_entities("parcels")
    trips = [t for t in store.list_trips() if _in(t.get("createdAt"), since, until)]
    drivers = {d["id"]: d for d in db.all_entities("drivers")}

    delivered = [p for p in parcels if p["status"] == "delivered" and _in(p.get("deliveredAt"), since, until)]
    on_time = [p for p in delivered if not p.get("deadlineMissed")]
    failures = [(p, e) for p in parcels for e in p.get("events", [])
                if e["type"] == "failed" and _in(e["ts"], since, until)]
    by_reason: dict[str, int] = {}
    for _p, e in failures:
        label = delivery.FAIL_REASONS.get(e.get("reason"), "Other reason")
        by_reason[label] = by_reason.get(label, 0) + 1
    returned = [p for p in parcels if p["status"] == "returned"
                and any(e["type"] == "returned" and _in(e["ts"], since, until) for e in p.get("events", []))]

    per_driver: dict[str, dict] = {}
    totals = {"km": 0.0, "plannedKm": 0.0, "hours": 0.0, "deviations": 0}
    for t in trips:
        m = _trip_metrics(t)
        for k in totals:
            totals[k] += m[k]
        d = drivers.get(t.get("driverId"), {"name": "Unassigned"})
        row = per_driver.setdefault(t.get("driverId") or "-", {
            "driverId": t.get("driverId"), "driver": d["name"], "runs": 0, "deliveries": 0,
            "onTime": 0, "failedAttempts": 0, "km": 0.0, "hours": 0.0})
        row["runs"] += 1
        row["km"] += m["km"]
        row["hours"] += m["hours"]
        for pid in t.get("parcelIds") or []:
            p = next((x for x in parcels if x["id"] == pid), None)
            if p and p["status"] == "delivered" and p.get("tripId") == t["id"]:
                row["deliveries"] += 1
                row["onTime"] += 0 if p.get("deadlineMissed") else 1
        row["failedAttempts"] += t.get("failedCount", 0)
    for row in per_driver.values():
        row["km"] = round(row["km"], 1)
        row["hours"] = round(row["hours"], 2)
        row["onTimeRate"] = round(row["onTime"] / row["deliveries"] * 100) if row["deliveries"] else None
        row["stopsPerHour"] = round(row["deliveries"] / row["hours"], 1) if row["hours"] else None
        row["cost"] = round(row["km"] * costs["perKm"] + row["hours"] * costs["perHour"])
        row["costPerDelivery"] = round(row["cost"] / row["deliveries"]) if row["deliveries"] else None

    total_cost = round(totals["km"] * costs["perKm"] + totals["hours"] * costs["perHour"])
    run_deliveries = sum(r["deliveries"] for r in per_driver.values())

    # per-day series (local dates) for the chart
    daily: dict[str, dict] = {}
    def bucket(ts):
        return daily.setdefault(time.strftime("%Y-%m-%d", time.localtime(ts)), {"delivered": 0, "failed": 0})
    for p in delivered:
        bucket(p["deliveredAt"])["delivered"] += 1
    for _p, e in failures:
        bucket(e["ts"])["failed"] += 1

    return {
        "period": {"days": days, "since": since, "until": until},
        "costs": costs,
        "parcels": {
            "delivered": len(delivered), "onTime": len(on_time), "late": len(delivered) - len(on_time),
            "onTimeRate": round(len(on_time) / len(delivered) * 100) if delivered else None,
            "failedAttempts": len(failures), "returned": len(returned),
            "created": sum(1 for p in parcels if _in(p.get("createdAt"), since, until)),
            "pendingNow": sum(1 for p in parcels if delivery.is_plannable(p)),
            "firstAttemptRate": (round(sum(1 for p in delivered if p.get("failedAttempts", 0) == 0)
                                       / len(delivered) * 100) if delivered else None),
        },
        "runs": {
            "count": len(trips), "completed": sum(1 for t in trips if t["status"] == "completed"),
            "km": round(totals["km"], 1), "plannedKm": round(totals["plannedKm"], 1),
            "hours": round(totals["hours"], 1), "deviations": totals["deviations"],
        },
        "cost": {"total": total_cost, "currency": costs["currency"],
                 "perDelivery": round(total_cost / run_deliveries) if run_deliveries else None,
                 "perKm": round(total_cost / totals["km"]) if totals["km"] else None},
        "perDriver": sorted(per_driver.values(), key=lambda r: -r["deliveries"]),
        "failuresByReason": sorted(({"reason": k, "count": v} for k, v in by_reason.items()),
                                   key=lambda r: -r["count"]),
        "daily": [{"date": k, **v} for k, v in sorted(daily.items())],
    }


# ------------------------------------------------------------------ CSV

def _safe(value):
    """Stop spreadsheet formula injection: cells that start with = + - @ are text."""
    if isinstance(value, str) and value[:1] in ("=", "+", "-", "@", "\t", "\r"):
        return "'" + value
    return "" if value is None else value


def _iso(ts):
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(ts)) if ts else ""


def _csv(header: list[str], rows: list[list]) -> str:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(header)
    for r in rows:
        w.writerow([_safe(c) for c in r])
    return buf.getvalue()


def export_csv(kind: str, days: int | None) -> str:
    since, until = window(days)
    parcels = db.all_entities("parcels")
    trips = {t["id"]: t for t in store.list_trips()}
    drivers = {d["id"]: d for d in db.all_entities("drivers")}
    if kind == "parcels":
        rows = []
        for p in parcels:
            touched = [p.get("createdAt"), p.get("deliveredAt")] + [e["ts"] for e in p.get("events", [])]
            if not any(_in(t, since, until) for t in touched):
                continue
            trip = trips.get(p.get("tripId") or (p.get("lastFailure") or {}).get("tripId") or "")
            driver = drivers.get((trip or {}).get("driverId"), {})
            d = p.get("dimensionsCm")
            rows.append([
                p.get("trackingNumber"), p["name"], p.get("receiverName"), p.get("recipientPhone"),
                p["destination"].get("label"), p["size"], p.get("weightKg"),
                f"{d['l']}x{d['w']}x{d['h']}" if d else "", p.get("priority", "standard"), p["status"],
                _iso(p.get("createdAt")), _iso(p["deadline"]), _iso(p.get("deliveredAt")),
                "" if p["status"] != "delivered" else ("no" if p.get("deadlineMissed") else "yes"),
                p.get("failedAttempts", 0), (p.get("lastFailure") or {}).get("label"),
                driver.get("name"), (trip or {}).get("id"),
                (p.get("pod") or {}).get("label"), (p.get("pod") or {}).get("receivedBy")])
        return _csv(["tracking_number", "parcel", "receiver", "phone", "address", "size", "weight_kg",
                     "dimensions_cm", "priority", "status", "created", "deadline", "delivered",
                     "on_time", "failed_attempts", "last_failure", "driver", "run", "proof", "received_by"], rows)
    if kind == "runs":
        rows = []
        for t in sorted(trips.values(), key=lambda t: t["createdAt"]):
            if not _in(t.get("createdAt"), since, until):
                continue
            m = _trip_metrics(t)
            stops = t["route"]["orderedStops"]
            rows.append([t["id"], (drivers.get(t.get("driverId")) or {}).get("name"), t.get("vehicleType"),
                         t["status"], _iso(t.get("createdAt")), _iso(t.get("startedAt")), _iso(t.get("endedAt")),
                         len(t.get("parcelIds") or []), t.get("deliveredCount", 0), t.get("failedCount", 0),
                         round(m["plannedKm"], 1), round(m["km"], 1), round(m["hours"], 2), m["deviations"],
                         f"{stops[0].get('label', '')} -> {stops[-1].get('place') or stops[-1].get('label', '')}"])
        return _csv(["run", "driver", "vehicle", "status", "created", "started", "ended", "parcels",
                     "delivered", "failed", "planned_km", "km", "hours", "deviations", "route"], rows)
    if kind == "drivers":
        s = summary(days)
        return _csv(["driver", "runs", "deliveries", "on_time", "on_time_pct", "failed_attempts", "km",
                     "hours", "stops_per_hour", f"cost_{s['costs']['currency']}", "cost_per_delivery"],
                    [[r["driver"], r["runs"], r["deliveries"], r["onTime"], r["onTimeRate"], r["failedAttempts"],
                      r["km"], r["hours"], r["stopsPerHour"], r["cost"], r["costPerDelivery"]] for r in s["perDriver"]])
    raise ValueError("kind must be parcels, runs or drivers")


COST_PER_FAILED_ATTEMPT = 180.0      # LKR: the research's modelled cost of a failed attempt (re-attempt, handling, customer service). An assumption.


def _area(parcel: dict) -> str:
    label = ((parcel.get("destination") or {}).get("label") or "").strip()
    return (label.split(",")[-1].strip() if "," in label else label) or "Unknown"


def insights(days: int | None = 7) -> dict:
    """What a courier operator acts on: what failures cost, which areas fail most, service level by
    priority, and when deliveries happen."""
    since, until = window(days)
    parcels = [p for p in db.all_entities("parcels") if p.get("requestType") != "pickup" and _in(p.get("createdAt"), since, until)]
    closed = [p for p in parcels if p["status"] in ("delivered", "failed", "returned", "awaiting_redelivery")]
    first_ok = lambda p: p["status"] == "delivered" and not p.get("failedAttempts")
    pct = lambda a, b: round(100 * a / b, 1) if b else None

    failed_attempts = sum(p.get("failedAttempts", 0) for p in closed)
    preventable = {"no_one_home", "wrong_address", "refused", "customer_unreachable"}
    prevent = sum(1 for p in closed if p.get("failedAttempts") and (p.get("lastFailure") or {}).get("reason") in preventable)

    areas: dict[str, dict] = {}
    for p in closed:
        a = areas.setdefault(_area(p), {"area": _area(p), "closed": 0, "firstOk": 0, "failed": 0})
        a["closed"] += 1
        a["firstOk"] += first_ok(p)
        a["failed"] += p.get("failedAttempts", 0)
    area_rows = sorted(({"area": a["area"], "parcels": a["closed"], "firstAttemptPct": pct(a["firstOk"], a["closed"]), "failedAttempts": a["failed"]}
                        for a in areas.values() if a["closed"] >= 3),
                       key=lambda r: (r["firstAttemptPct"] if r["firstAttemptPct"] is not None else 101, -r["parcels"]))[:8]

    service = []
    for prio in ("express", "priority", "standard"):
        mine = [p for p in parcels if (p.get("priority") or "standard") == prio and p["status"] == "delivered"]
        if mine:
            service.append({"priority": prio, "delivered": len(mine), "onTimePct": pct(sum(1 for p in mine if not p.get("deadlineMissed")), len(mine))})

    by_hour = [0] * 24
    for p in parcels:
        if p["status"] == "delivered" and p.get("deliveredAt"):
            by_hour[time.localtime(p["deliveredAt"]).tm_hour] += 1

    return {"failedCost": {"attempts": failed_attempts, "perAttempt": COST_PER_FAILED_ATTEMPT, "total": round(failed_attempts * COST_PER_FAILED_ATTEMPT),
                           "currency": "LKR", "preventableParcels": prevent,
                           "note": "Cost per failed attempt is the research's modelled figure (an assumption); replace it with your own."},
            "areas": area_rows, "service": service, "byHour": by_hour}
