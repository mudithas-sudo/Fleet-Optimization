"""Failed-delivery prevention: confirmation, address confidence and a risk score per parcel.

The costliest thing in Sri Lankan last-mile is the failed first attempt (no one home, wrong or vague
address, COD refused). Most of it can be seen coming, so every parcel gets a transparent score built
from a few visible reasons, and the back office works a short list of parcels to confirm before the
rider leaves. Nothing here is a black box: each point on the score has a stated reason.
"""

import math
import time

from . import db

CONFIRM_STATES = ("requested", "confirmed", "reschedule", "cancelled")
WINDOWS = {"morning": "Morning (9 to 12)", "afternoon": "Afternoon (12 to 4)", "evening": "Evening (4 to 8)", "any": "Any time"}
OPEN = ("pending", "assigned", "in_transit", "awaiting_redelivery")


def _digits(phone: str | None) -> str:
    d = "".join(c for c in (phone or "") if c.isdigit())
    return d[-9:] if len(d) >= 9 else d


def _metres(a: dict, b: dict) -> float:
    dlat = (a["lat"] - b["lat"]) * 111_000
    dlng = (a["lng"] - b["lng"]) * 111_000 * math.cos(math.radians(a["lat"]))
    return math.hypot(dlat, dlng)


def customer_index(parcels: list[dict] | None = None) -> dict:
    """What we already know about each phone number: past outcomes and where drop-offs really worked."""
    idx: dict[str, dict] = {}
    for p in parcels if parcels is not None else db.all_entities("parcels"):
        key = _digits(p.get("recipientPhone"))
        if not key:
            continue
        c = idx.setdefault(key, {"delivered": 0, "failed": 0, "refused": 0, "noOne": 0, "codRefused": 0, "pin": None})
        if p["status"] == "delivered":
            c["delivered"] += 1
            pos = (p.get("pod") or {}).get("position")
            if pos and pos.get("lat") is not None and p.get("requestType") != "pickup":
                if not c["pin"] or (p.get("deliveredAt") or 0) > c["pin"]["ts"]:
                    c["pin"] = {"lat": pos["lat"], "lng": pos["lng"], "ts": p.get("deliveredAt") or 0}
        lf = p.get("lastFailure")
        if p.get("failedAttempts"):
            c["failed"] += p["failedAttempts"]
            if lf and lf.get("reason") == "refused":
                c["refused"] += 1
                if p.get("cod"):
                    c["codRefused"] += 1
            if lf and lf.get("reason") == "no_one_home":
                c["noOne"] += 1
    return idx


def address_check(parcel: dict, history: dict | None) -> dict:
    """How sure are we that the rider can find the door? (0 to 100, with reasons)"""
    dest = parcel["destination"]
    score, why = 55, []
    if parcel.get("landmark"):
        score += 20
        why.append("Landmark given")
    else:
        why.append("No landmark or directions")
    if len((dest.get("label") or "").strip()) < 8:
        score -= 10
        why.append("Very short address")
    pin = history and history.get("pin")
    suggested = None
    if pin:
        off = _metres(dest, pin)
        if off <= 120:
            score += 20
            why.append("A past delivery was made at this spot")
        else:
            score -= 10
            suggested = {"lat": pin["lat"], "lng": pin["lng"], "offsetM": round(off)}
            why.append(f"Past deliveries to this number were {round(off)} m away: use that pin")
    if parcel.get("lastFailure", {}) and (parcel.get("lastFailure") or {}).get("reason") == "wrong_address":
        score -= 30
        why.append("Last attempt: address problem")
    return {"score": max(0, min(100, score)), "reasons": why, "suggestedPin": suggested}


def assess(parcel: dict, idx: dict | None = None, now: float | None = None) -> dict:
    """Score a parcel that has not been delivered. 0 to 100; each reason adds a stated number of points."""
    now = now or time.time()
    history = (idx if idx is not None else customer_index()).get(_digits(parcel.get("recipientPhone")))
    pts: list[tuple[int, str]] = []
    if not parcel.get("recipientPhone"):
        pts.append((25, "No phone number to reach the receiver"))
    conf = (parcel.get("confirmation") or {}).get("state")
    if conf == "confirmed":
        pts.append((-25, "Receiver confirmed they will be there"))
    elif conf == "requested":
        pts.append((10, "Confirmation asked for, no reply yet"))
    elif conf == "cancelled":
        pts.append((60, "Receiver cancelled"))
    elif parcel.get("recipientPhone"):
        pts.append((12, "Not confirmed with the receiver"))
    cod = parcel.get("cod")
    if cod and cod.get("state") == "due":
        pts.append((15, f"Cash on delivery: {cod['amount']:,.0f}"))
        if cod["amount"] >= 25000:
            pts.append((8, "High-value cash on delivery"))
    if parcel.get("failedAttempts"):
        pts.append((20 * min(2, parcel["failedAttempts"]), f"{parcel['failedAttempts']} failed attempt(s) already"))
    if history:
        if history["codRefused"] or history["refused"]:
            pts.append((20, "This number has refused a parcel before"))
        elif history["noOne"]:
            pts.append((10, "No one was home on a past delivery to this number"))
        if history["delivered"] and not history["failed"]:
            pts.append((-10, f"{history['delivered']} earlier successful deliver{'y' if history['delivered'] == 1 else 'ies'}"))
    addr = address_check(parcel, history)
    if addr["score"] < 50:
        pts.append((15, "Address may be hard to find"))
    elif addr["score"] >= 80:
        pts.append((-5, "Address is well described"))
    score = max(0, min(100, 20 + sum(p for p, _ in pts)))
    level = "high" if score >= 55 else "medium" if score >= 35 else "low"
    return {"score": score, "level": level, "reasons": [{"points": p, "text": t} for p, t in pts if p],
            "address": addr, "confirmation": parcel.get("confirmation"), "history": history and
            {k: v for k, v in history.items() if k != "pin"}}


def needs_confirming(parcel: dict, now: float | None = None) -> bool:
    now = now or time.time()
    if parcel.get("requestType") == "pickup" or parcel["status"] not in ("pending", "assigned", "awaiting_redelivery"):
        return False
    state = (parcel.get("confirmation") or {}).get("state")
    return state not in ("confirmed", "cancelled", "reschedule")


def queue(limit: int = 40) -> dict:
    """The confirmation call list: open parcels not yet confirmed, riskiest first."""
    parcels = db.all_entities("parcels")
    idx = customer_index(parcels)
    now = time.time()
    rows = []
    for p in parcels:
        if not needs_confirming(p, now):
            continue
        a = assess(p, idx, now)
        rows.append({"id": p["id"], "trackingNumber": p["trackingNumber"], "name": p["name"], "receiverName": p.get("receiverName"),
                     "phone": p.get("recipientPhone"), "label": p["destination"].get("label"), "status": p["status"],
                     "deadline": p.get("deadline"), "cod": (p.get("cod") or {}).get("amount"),
                     "confirmation": (p.get("confirmation") or {}).get("state"), "score": a["score"], "level": a["level"],
                     "top": [r["text"] for r in sorted(a["reasons"], key=lambda r: -r["points"])[:2] if r["points"] > 0]})
    rows.sort(key=lambda r: -r["score"])
    return {"count": len(rows), "high": sum(r["level"] == "high" for r in rows), "rows": rows[:limit]}


# ---------------------------------------------------------------- confirmation

def respond(parcel: dict, action: str, *, window: str | None = None, day: float | None = None,
            landmark: str | None = None, actor: str = "customer", note: str | None = None) -> dict:
    """The receiver (or customer service on their behalf) answers: confirm / reschedule / cancel."""
    from . import delivery
    if action not in ("confirm", "reschedule", "cancel"):
        raise ValueError("action must be confirm, reschedule or cancel")
    if parcel["status"] in ("delivered", "returned", "picked_up"):
        raise ValueError("This parcel is already closed.")
    if parcel["status"] == "in_transit" and action != "confirm":
        raise ValueError("The rider is already on the way: call the rider or the hub to change this.")
    window = window if window in WINDOWS else "any"
    now = time.time()
    conf = {"state": {"confirm": "confirmed", "reschedule": "reschedule", "cancel": "cancelled"}[action],
            "respondedAt": now, "by": actor, "window": window, "requestedAt": (parcel.get("confirmation") or {}).get("requestedAt")}
    if note:
        conf["note"] = note[:200]
    parcel["confirmation"] = conf
    if landmark and landmark.strip():
        parcel["landmark"] = landmark.strip()[:200]
        delivery.add_event(parcel, "edited", f"Receiver added directions: {parcel['landmark']}", by=actor)
    if action == "confirm":
        delivery.add_event(parcel, "confirmed", f"Receiver confirmed ({WINDOWS[window]})", by=actor)
        db.save_entity("parcels", parcel)
    elif action == "reschedule":
        if not day or day < now:
            raise ValueError("Choose a day in the future.")
        hold = delivery.day_start(day) + {"morning": 9, "afternoon": 12, "evening": 16, "any": 8}[window] * 3600 - 3600
        parcel["holdUntil"] = hold
        delivery.add_event(parcel, "rescheduled", f"Receiver asked for {time.strftime('%a %d %b', time.localtime(day))} ({WINDOWS[window]})", by=actor)
        db.save_entity("parcels", parcel)
    else:
        delivery.add_event(parcel, "cancelled", "Receiver cancelled before dispatch" + (f": {note}" if note else ""), by=actor)
        db.save_entity("parcels", parcel)
    return parcel


def request_confirmation(parcel: dict, actor: str = "dispatcher") -> dict:
    from . import delivery, notify
    parcel["confirmation"] = {"state": "requested", "requestedAt": time.time(), "by": actor}
    delivery.add_event(parcel, "confirm_request", "Asked the receiver to confirm the delivery", by=actor)
    db.save_entity("parcels", parcel)
    try:
        notify.notify(parcel, "confirm_request")
    except Exception:
        notify.log.exception("notification failed")
    return parcel


def use_known_pin(parcel: dict) -> dict:
    from . import delivery
    a = assess(parcel)["address"]["suggestedPin"]
    if not a:
        raise ValueError("There is no better pin on record for this receiver.")
    parcel["destination"] = {**parcel["destination"], "lat": a["lat"], "lng": a["lng"]}
    delivery.add_event(parcel, "edited", f"Delivery pin moved {a['offsetM']} m to where past deliveries succeeded", by="dispatcher")
    db.save_entity("parcels", parcel)
    return parcel


# ---------------------------------------------------------------- first attempt KPIs

def first_attempt(days: int | None = 30, now: float | None = None) -> dict:
    """First-attempt delivery rate and what stopped parcels failing: the figures the research says to watch."""
    now = now or time.time()
    since = now - days * 86400 if days else 0
    done = [p for p in db.all_entities("parcels") if p.get("requestType") != "pickup" and since <= (p.get("createdAt") or 0) <= now
            and p["status"] in ("delivered", "failed", "returned", "awaiting_redelivery")]
    first_ok = sum(1 for p in done if p["status"] == "delivered" and not p.get("failedAttempts"))
    delivered = sum(1 for p in done if p["status"] == "delivered")
    rto = sum(1 for p in done if p["status"] == "returned")
    reasons: dict[str, int] = {}
    for p in done:
        lf = p.get("lastFailure")
        if p.get("failedAttempts") and lf:
            reasons[lf["label"]] = reasons.get(lf["label"], 0) + 1
    conf = [p for p in done if (p.get("confirmation") or {}).get("state") == "confirmed"]
    unconf = [p for p in done if (p.get("confirmation") or {}).get("state") != "confirmed"]
    rate = lambda ps: round(100 * sum(1 for p in ps if p["status"] == "delivered" and not p.get("failedAttempts")) / len(ps), 1) if ps else None
    cod = [p for p in done if p.get("cod")]
    return {"parcels": len(done), "firstAttemptPct": rate(done), "deliveredPct": round(100 * delivered / len(done), 1) if done else None,
            "returnedPct": round(100 * rto / len(done), 1) if done else None, "firstOk": first_ok,
            "confirmed": {"parcels": len(conf), "firstAttemptPct": rate(conf)},
            "unconfirmed": {"parcels": len(unconf), "firstAttemptPct": rate(unconf)},
            "cod": {"parcels": len(cod), "firstAttemptPct": rate(cod)},
            "failureReasons": sorted(({"reason": k, "parcels": v} for k, v in reasons.items()), key=lambda r: -r["parcels"])}
