"""Cash on delivery: what each rider is carrying, what reached the hub, what the merchant is owed.

Sri Lankan e-commerce is mostly cash on delivery, so a parcel with `cod` is not "delivered" until
the cash is in the rider's hands. The money then moves through three states:

    due  ->  collected (with the rider)  ->  deposited (counted at the hub)  ->  remitted (paid to the merchant)

The rider's cash on hand is tracked live against a limit (a rider carrying too much is a safety and
fraud risk), every deposit records any difference between what the rider should hold and what was
handed in, and merchants see how long their money has been waiting.
"""

import time
import uuid

from . import db

DEFAULTS = {"cashLimit": 100000.0, "depositHours": 24.0, "currency": "LKR"}


def get_settings() -> dict:
    return {**DEFAULTS, **(db.get_setting("cod") or {})}


def set_settings(cash_limit: float | None = None, deposit_hours: float | None = None) -> dict:
    cur = get_settings()
    if cash_limit is not None:
        cur["cashLimit"] = max(0.0, float(cash_limit))
    if deposit_hours is not None:
        cur["depositHours"] = max(1.0, float(deposit_hours))
    db.set_setting("cod", cur)
    return cur


def due_amount(parcel: dict) -> float:
    cod = parcel.get("cod")
    return float(cod["amount"]) if cod and cod.get("state") == "due" else 0.0


def new_cod(amount: float | None) -> dict | None:
    if not amount or float(amount) <= 0:
        return None
    return {"amount": round(float(amount), 2), "state": "due"}


def collect(parcel: dict, amount: float, driver_id: str | None, actor: str = "driver") -> dict:
    """The rider took the cash at the door. A short collection is allowed but recorded."""
    cod = parcel["cod"]
    cod.update(state="collected", collected=round(float(amount), 2), collectedAt=time.time(),
               collectedBy=actor, driverId=driver_id,
               short=round(max(0.0, cod["amount"] - float(amount)), 2))
    return cod


def _driver_name(driver_id: str | None) -> str:
    d = driver_id and db.load_entity("drivers", driver_id)
    return d["name"] if d else "Unknown driver"


def ledger(now: float | None = None) -> dict:
    now = now or time.time()
    cfg = get_settings()
    parcels = [p for p in db.all_entities("parcels") if p.get("cod")]
    riders: dict[str, dict] = {}
    merchants: dict[str, dict] = {}
    totals = {"onTheRoad": 0.0, "withRiders": 0.0, "atHub": 0.0, "paidOut": 0.0, "shortfall": 0.0}
    for p in parcels:
        cod = p["cod"]
        state = cod["state"]
        merchant = p.get("merchant") or "Unknown merchant"
        m = merchants.setdefault(merchant, {"merchant": merchant, "parcels": 0, "onTheRoad": 0.0, "withRiders": 0.0,
                                            "awaitingPayout": 0.0, "paidOut": 0.0, "oldestWaitingAt": None})
        m["parcels"] += 1
        amt = cod.get("collected", cod["amount"])
        if state == "due":
            if p["status"] in ("in_transit", "assigned"):
                totals["onTheRoad"] += cod["amount"]
                m["onTheRoad"] += cod["amount"]
        elif state == "collected":
            totals["withRiders"] += amt
            m["withRiders"] += amt
            r = riders.setdefault(cod.get("driverId") or "?", {"driverId": cod.get("driverId"), "driverName": _driver_name(cod.get("driverId")),
                                                              "cash": 0.0, "parcels": [], "oldestAt": None})
            r["cash"] += amt
            r["parcels"].append({"parcelId": p["id"], "trackingNumber": p["trackingNumber"], "name": p["name"],
                                 "amount": amt, "at": cod["collectedAt"], "merchant": merchant})
            r["oldestAt"] = min(r["oldestAt"] or cod["collectedAt"], cod["collectedAt"])
        elif state == "deposited":
            totals["atHub"] += amt
            m["awaitingPayout"] += amt
            m["oldestWaitingAt"] = min(m["oldestWaitingAt"] or cod["depositedAt"], cod["depositedAt"])
        elif state == "remitted":
            totals["paidOut"] += amt
            m["paidOut"] += amt
        totals["shortfall"] += cod.get("short", 0.0)
    limit, hours = cfg["cashLimit"], cfg["depositHours"]
    rider_rows = []
    for r in riders.values():
        r["overLimit"] = r["cash"] > limit
        r["overdue"] = bool(r["oldestAt"]) and (now - r["oldestAt"]) / 3600 > hours
        r["usedPct"] = round(100 * r["cash"] / limit) if limit else 0
        r["parcels"].sort(key=lambda x: x["at"])
        rider_rows.append(r)
    rider_rows.sort(key=lambda r: -r["cash"])
    merchant_rows = sorted(merchants.values(), key=lambda m: -(m["awaitingPayout"] + m["withRiders"]))
    for m in merchant_rows:
        m["waitingDays"] = round((now - m["oldestWaitingAt"]) / 86400, 1) if m["oldestWaitingAt"] else None
    deposits = sorted(db.all_entities("cod_deposits"), key=lambda d: -d["createdAt"])[:15]
    return {"settings": cfg, "totals": {k: round(v, 2) for k, v in totals.items()}, "riders": rider_rows,
            "merchants": merchant_rows, "deposits": deposits,
            "flags": {"overLimit": sum(r["overLimit"] for r in rider_rows), "overdue": sum(r["overdue"] for r in rider_rows)}}


def deposit(driver_id: str, counted: float, reference: str | None, actor: str, note: str | None = None,
            until: float | None = None) -> dict:
    """Cash handed in at the hub. Everything the rider was holding is settled; any difference between
    what they should have and what was counted is recorded on the deposit and kept for follow-up."""
    held = [p for p in db.all_entities("parcels")
            if p.get("cod") and p["cod"]["state"] == "collected" and p["cod"].get("driverId") == driver_id
            and (until is None or p["cod"]["collectedAt"] <= until)]       # (`until`: only cash taken by then, for back-dated sample data)
    expected = round(sum(p["cod"].get("collected", p["cod"]["amount"]) for p in held), 2)
    if not held:
        raise ValueError("This rider isn't holding any cash.")
    now = time.time()
    rec = {"id": f"dep-{uuid.uuid4().hex[:6]}", "createdAt": now, "driverId": driver_id,
           "driverName": _driver_name(driver_id), "expected": expected, "counted": round(float(counted), 2),
           "variance": round(float(counted) - expected, 2), "reference": (reference or "").strip()[:60] or None,
           "note": (note or "").strip()[:200] or None, "by": actor, "parcelIds": [p["id"] for p in held]}
    from . import delivery
    for p in held:
        p["cod"].update(state="deposited", depositedAt=now, depositId=rec["id"])
        delivery.add_event(p, "cod", f"Cash counted at the hub (deposit {rec['id']})", by=actor)
        db.save_entity("parcels", p)
    db.save_entity("cod_deposits", rec)
    return rec


def remit(merchant: str, reference: str | None, actor: str, until: float | None = None) -> dict:
    """The merchant has been paid everything the hub is holding for them."""
    from . import delivery
    held = [p for p in db.all_entities("parcels")
            if p.get("cod") and p["cod"]["state"] == "deposited" and (p.get("merchant") or "Unknown merchant") == merchant
            and (until is None or p["cod"]["depositedAt"] <= until)]
    if not held:
        raise ValueError("Nothing is waiting to be paid to this merchant.")
    now = time.time()
    total = 0.0
    for p in held:
        total += p["cod"].get("collected", p["cod"]["amount"])
        p["cod"].update(state="remitted", remittedAt=now, remittanceRef=(reference or "").strip()[:60] or None)
        delivery.add_event(p, "cod", f"Cash paid out to {merchant}", by=actor)
        db.save_entity("parcels", p)
    return {"merchant": merchant, "parcels": len(held), "amount": round(total, 2), "reference": reference}
