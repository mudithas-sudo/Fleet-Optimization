"""Customer notifications for parcel events (out for delivery, nearby, delivered,
failed, rescheduled, returned).

Messages are always recorded (so the back office can see what the customer was
told). They are actually delivered only when a provider is configured: set
NOTIFY_WEBHOOK_URL and every message is POSTed there as JSON
({channel, to, subject, message, trackingNumber, trackingUrl, event}), which can
front Twilio, an SMTP relay, Zapier, etc. Without it messages are logged only,
marked "not sent".
"""

import json
import logging
import os
import threading
import time
import urllib.request
import uuid

from . import db

log = logging.getLogger("fleetops.notify")

EVENTS = ("out_for_delivery", "nearby", "delivered", "failed", "rescheduled", "returned")


def public_base_url() -> str:
    return os.environ.get("PUBLIC_BASE_URL", "http://localhost:8000").rstrip("/")


def tracking_url(parcel: dict) -> str:
    return f"{public_base_url()}/static/track.html?no={parcel['trackingNumber']}"


def _clock(ts: float | None) -> str:
    return time.strftime("%I:%M %p", time.localtime(ts)).lstrip("0") if ts else ""


def _compose(parcel: dict, event: str, **info) -> tuple[str, str]:
    """(subject, message) for the customer."""
    tn, url = parcel["trackingNumber"], tracking_url(parcel)
    first = (parcel.get("receiverName") or "").split()[:1]
    # "N. Silva" -> no first name to use
    who = f"Hi {first[0]}, " if first and not first[0].endswith(".") and len(first[0]) > 1 else "Hello, "
    if event == "out_for_delivery":
        eta = f" It should arrive around {_clock(info['eta'])}." if info.get("eta") else ""
        return (f"Your parcel {tn} is out for delivery",
                f"{who}your parcel {tn} is out for delivery.{eta} Track it live: {url}")
    if event == "nearby":
        mins = info.get("minutes")
        where = f"about {mins} min away" if mins else "nearby"
        return (f"Your driver is {where}", f"{who}your driver is {where} with parcel {tn}. {url}")
    if event == "delivered":
        by = f" Received by {info['receivedBy']}." if info.get("receivedBy") else ""
        return (f"Your parcel {tn} was delivered", f"{who}your parcel {tn} was delivered.{by} {url}")
    if event == "failed":
        again = (f" We'll try again by {time.strftime('%d %b', time.localtime(info['retryAt']))}."
                 if info.get("retryAt") else " We'll be in touch to arrange another attempt.")
        return (f"We couldn't deliver parcel {tn}",
                f"{who}we couldn't deliver parcel {tn} ({info.get('reason', 'delivery problem').lower()}).{again} {url}")
    if event == "rescheduled":
        when = time.strftime("%d %b %H:%M", time.localtime(info["deadline"])) if info.get("deadline") else "soon"
        return (f"Parcel {tn} rescheduled", f"{who}delivery of parcel {tn} is rescheduled for {when}. {url}")
    return (f"Parcel {tn} is being returned", f"{who}parcel {tn} could not be delivered and is being returned to the sender. {url}")


def notify(parcel: dict, event: str, **info) -> list[dict]:
    """Record (and, if a provider is configured, send) the customer messages for
    one event. Idempotent per parcel + event + attempt. Returns the new records."""
    if event not in EVENTS:
        return []
    channels = []
    if parcel.get("recipientPhone"):
        channels.append(("sms", parcel["recipientPhone"]))
    if parcel.get("receiverEmail"):
        channels.append(("email", parcel["receiverEmail"]))
    if not channels:
        return []
    key = f"{parcel['id']}:{event}:{parcel.get('failedAttempts', 0)}"
    if any(n.get("key") == key for n in for_parcel(parcel["id"])):
        return []
    subject, message = _compose(parcel, event, **info)
    out = []
    for channel, to in channels:
        rec = {"id": f"n-{uuid.uuid4().hex[:8]}", "createdAt": time.time(), "key": key,
               "parcelId": parcel["id"], "trackingNumber": parcel["trackingNumber"],
               "event": event, "channel": channel, "to": to, "subject": subject,
               "message": message, "status": "not_sent"}
        if os.environ.get("NOTIFY_WEBHOOK_URL"):
            rec["status"] = "sending"
        db.save_entity("notifications", rec)
        out.append(rec)
        log.info("notification [%s -> %s] %s", channel, to, message)
        if rec["status"] == "sending":
            threading.Thread(target=_send, args=(rec, parcel), daemon=True).start()
    return out


def _send(rec: dict, parcel: dict) -> None:
    body = json.dumps({"channel": rec["channel"], "to": rec["to"], "subject": rec["subject"],
                       "message": rec["message"], "event": rec["event"],
                       "trackingNumber": rec["trackingNumber"],
                       "trackingUrl": tracking_url(parcel)}).encode()
    req = urllib.request.Request(os.environ["NOTIFY_WEBHOOK_URL"], data=body,
                                 headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            rec["status"] = "sent" if 200 <= resp.status < 300 else "failed"
    except Exception as exc:                      # a provider outage must never break deliveries
        rec["status"] = "failed"
        rec["error"] = str(exc)[:200]
        log.warning("notification webhook failed: %s", exc)
    db.save_entity("notifications", rec)


def for_parcel(parcel_id: str) -> list[dict]:
    return [n for n in db.all_entities("notifications") if n["parcelId"] == parcel_id]


def recent(limit: int = 50) -> list[dict]:
    return list(reversed(db.all_entities("notifications")))[:limit]
