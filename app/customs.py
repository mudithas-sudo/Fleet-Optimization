"""International import shipments and Sri Lanka Customs.

FedEx's Sri Lankan operation is mostly inbound express: a parcel arrives by air, goes through Customs, and only then
can it go on a delivery run. Customs either clears it quickly or detains it, and a detained parcel needs the receiver
to attend an examination and pay any duty and tax at the Customs point (not to the rider). So the parcel carries a
customs state, and a parcel that is not cleared is not plannable:

    clearing -> cleared                 (the normal fast route)
    clearing -> held_exam -> cleared    (detained: the receiver must attend, then it is released)

Domestic parcels have `shipment = None` and are unaffected.
"""

import time

from . import db

STATES = {"clearing": "Awaiting customs clearance", "held_exam": "Held by Customs: receiver must attend an examination",
          "cleared": "Cleared by Customs"}


def new_shipment(kind: str | None, origin: str | None, state: str | None) -> dict | None:
    if (kind or "domestic") != "import":
        return None
    state = state if state in STATES else "clearing"
    return {"kind": "import", "origin": (origin or "").strip()[:60] or None, "customs": {"state": state, "since": time.time()}}


def blocking(parcel: dict) -> str | None:
    """The reason this parcel cannot be planned yet, if Customs still has it."""
    c = ((parcel.get("shipment") or {}).get("customs") or {}).get("state")
    if c in ("clearing", "held_exam"):
        return STATES[c]
    return None


def set_state(parcel: dict, state: str, note: str | None, actor: str) -> dict:
    from . import delivery, notify
    if not parcel.get("shipment"):
        raise ValueError("This is a domestic parcel: there is no customs step.")
    if state not in STATES:
        raise ValueError("state must be one of " + ", ".join(STATES))
    if parcel["status"] not in ("pending", "awaiting_redelivery", "assigned"):
        raise ValueError("The parcel is already on the road or closed.")
    cur = parcel["shipment"]["customs"]
    if cur["state"] == state:
        return parcel
    cur.update(state=state, since=time.time(), note=(note or "").strip()[:200] or None, by=actor)
    delivery.add_event(parcel, "customs", STATES[state] + (f": {cur['note']}" if cur["note"] else ""), by=actor)
    db.save_entity("parcels", parcel)
    try:
        notify.notify(parcel, "customs_hold" if state == "held_exam" else "customs_cleared" if state == "cleared" else "delayed",
                      reason="it is being cleared by Sri Lanka Customs")
    except Exception:
        notify.log.exception("notification failed")
    return parcel


def queue() -> dict:
    rows = []
    for p in db.all_entities("parcels"):
        c = ((p.get("shipment") or {}).get("customs") or {})
        if c.get("state") in ("clearing", "held_exam") and p["status"] in ("pending", "awaiting_redelivery", "assigned"):
            rows.append({"id": p["id"], "trackingNumber": p["trackingNumber"], "name": p["name"], "receiverName": p.get("receiverName"),
                         "phone": p.get("recipientPhone"), "origin": p["shipment"].get("origin"), "state": c["state"],
                         "since": c.get("since"), "priority": p.get("priority"), "deadline": p.get("deadline")})
    rows.sort(key=lambda r: (r["state"] != "held_exam", r["deadline"] or 0))
    return {"count": len(rows), "held": sum(r["state"] == "held_exam" for r in rows), "rows": rows}
