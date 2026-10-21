"""Deterministic checks for customer notifications and the public tracking API —
no Gemini, no Google (FAKE_ROUTES), no real SMS/email.
Run: `venv/bin/python tests/test_tracking_notify.py` (or pytest)."""

import asyncio
import json
import os
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

os.environ.setdefault("FAKE_ROUTES", "1")
os.environ.setdefault("GOOGLE_API_KEY", "unused")
os.environ.setdefault("MAPS_API_KEY", "unused")
os.environ.pop("NOTIFY_WEBHOOK_URL", None)
os.environ["DB_PATH"] = os.path.join(tempfile.mkdtemp(), "t.db")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi.testclient import TestClient     # noqa: E402

from app import db, delivery, main, notify, routing, store  # noqa: E402

DEPOT = {"lat": 6.93, "lng": 79.84, "label": "Hub"}
delivery.get_depot = lambda: DEPOT
client = TestClient(main.app)
_n = [0]


def _parcel(name, **kw):
    _n[0] += 1
    return delivery.new_parcel({
        "name": name, "size": "small", "deadline": time.time() + 7200,
        "destination": {"lat": 6.90 + _n[0] * 0.01, "lng": 79.85, "label": f"{_n[0]} High St, Fort Area"}, **kw})


def _run(parcels):
    van = delivery.new_vehicle({"name": "Van", "type": "van"})
    drv = delivery.new_driver({"name": "Dilani Fernando", "vehicleId": van["id"]})
    stops = delivery.build_run_stops(parcels)
    route = asyncio.run(routing.compute_route(stops, vehicle_type="van"))
    trip = store.new_trip(stops, route, vehicle_type="van")
    trip.update({"driverId": drv["id"], "vehicleId": van["id"], "status": "active",
                 "parcelIds": [p["id"] for p in parcels],
                 "plannedEtaPerStop": delivery.compute_planned_etas(route, {p["id"]: p for p in parcels}, time.time())})
    store.save_trip(trip)
    for p in parcels:
        delivery.set_parcel_status(p, "assigned", trip_id=trip["id"])
        delivery.set_parcel_status(p, "in_transit", trip_id=trip["id"])
    return trip


def test_messages_recorded_once_per_event_and_channel():
    p = _parcel("Router", receiverName="Anil Perera", recipientPhone="+94 77 000 1111",
                receiverEmail="anil@example.com", notes="Gate code 4411 — do not share")
    _run([p])
    msgs = notify.for_parcel(p["id"])
    assert {(m["channel"], m["event"]) for m in msgs} == {("sms", "out_for_delivery"), ("email", "out_for_delivery")}
    sms = next(m for m in msgs if m["channel"] == "sms")
    assert p["trackingNumber"] in sms["message"] and "/static/track.html?no=" in sms["message"]
    assert sms["message"].startswith("Hi Anil") and sms["status"] == "not_sent"
    assert "4411" not in sms["message"], "internal notes never go to the customer"
    # same event again does nothing
    assert notify.notify(db.load_entity("parcels", p["id"]), "out_for_delivery") == []
    assert len(notify.for_parcel(p["id"])) == 2


def test_no_contact_details_no_messages():
    p = _parcel("Silent")
    _run([p])
    assert notify.for_parcel(p["id"]) == []


def test_delivery_failure_and_reschedule_messages():
    p = _parcel("Fails", recipientPhone="+94 71 222 3333")
    _run([p])
    client.post(f"/api/parcels/{p['id']}/fail", json={"reason": "no_one_home", "note": "neighbour said away"})
    failed = [m for m in notify.for_parcel(p["id"]) if m["event"] == "failed"]
    assert len(failed) == 1 and "nobody home" in failed[0]["message"].lower()
    assert "neighbour" not in failed[0]["message"]
    assert "contact you" in failed[0]["message"]
    r = client.post(f"/api/parcels/{p['id']}/review", json={"decision": "reschedule", "scheduledFor": time.time() + 86400})
    assert r.status_code == 200, r.text
    assert [m["event"] for m in notify.for_parcel(p["id"])][-1] == "rescheduled"


def test_delivered_message_mentions_receiver():
    p = _parcel("Done", recipientPhone="+94 71 999 0000")
    _run([p])
    r = client.post(f"/api/parcels/{p['id']}/pod", json={"method": "handed_over", "receivedBy": "Mrs Silva"})
    assert r.status_code == 200
    done = [m for m in notify.for_parcel(p["id"]) if m["event"] == "delivered"]
    assert len(done) == 1 and "Mrs Silva" in done[0]["message"]


def test_nearby_fires_once_when_the_vehicle_is_close():
    a, b = _parcel("Near A", recipientPhone="+94 71 111 0001"), _parcel("Near B", recipientPhone="+94 71 111 0002")
    trip = _run([a, b])
    bounds, cum = [0.0], 0.0
    for leg in trip["route"]["legs"]:
        cum += leg["distanceMeters"]
        bounds.append(cum)
    runtime = {"along": bounds[1] - 3000}
    main._check_nearby(trip, runtime)
    assert not [m for p in (a, b) for m in notify.for_parcel(p["id"]) if m["event"] == "nearby"]
    runtime["along"] = bounds[1] - 600
    main._check_nearby(trip, runtime)
    main._check_nearby(trip, runtime)
    first_parcel = trip["route"]["orderedStops"][1]["parcelId"]
    sent = [m for m in notify.for_parcel(first_parcel) if m["event"] == "nearby"]
    assert len(sent) == 1 and "min away" in sent[0]["message"]


class _Capture(BaseHTTPRequestHandler):
    seen: list = []

    def do_POST(self):
        body = self.rfile.read(int(self.headers["Content-Length"]))
        _Capture.seen.append(json.loads(body))
        self.send_response(200)
        self.end_headers()

    def log_message(self, *a):
        pass


def test_webhook_delivery_and_failure_never_break_the_flow():
    srv = HTTPServer(("127.0.0.1", 0), _Capture)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    os.environ["NOTIFY_WEBHOOK_URL"] = f"http://127.0.0.1:{srv.server_port}/hook"
    try:
        p = _parcel("Hooked", recipientPhone="+94 71 555 0000")
        _run([p])
        for _ in range(40):
            if all(m["status"] == "sent" for m in notify.for_parcel(p["id"])) and notify.for_parcel(p["id"]):
                break
            time.sleep(0.1)
        rec = notify.for_parcel(p["id"])[0]
        assert rec["status"] == "sent"
        payload = _Capture.seen[-1]
        assert payload["channel"] == "sms" and payload["to"] == "+94 71 555 0000"
        assert payload["trackingNumber"] == p["trackingNumber"] and payload["event"] == "out_for_delivery"
        # provider down: the delivery itself must still succeed
        srv.shutdown()
        srv.server_close()
        q = _parcel("Unreachable", recipientPhone="+94 71 555 0001")
        _run([q])
        for _ in range(60):
            recs = notify.for_parcel(q["id"])
            if recs and recs[0]["status"] != "sending":
                break
            time.sleep(0.1)
        assert notify.for_parcel(q["id"])[0]["status"] == "failed"
        assert db.load_entity("parcels", q["id"])["status"] == "in_transit"
    finally:
        os.environ.pop("NOTIFY_WEBHOOK_URL", None)


def test_public_tracking_api_is_safe_and_helpful():
    a = _parcel("Track A", receiverName="Anil", recipientPhone="+94 77 000 9999", notes="secret gate code")
    b = _parcel("Track B")
    trip = _run([a, b])
    r = client.get(f"/api/track/{a['trackingNumber'].lower()}")
    assert r.status_code == 200
    d = r.json()
    text = json.dumps(d)
    assert "+94 77 000 9999" not in text and "secret gate code" not in text and "Fernando" not in text
    assert d["status"] == "in_transit" and d["step"] == 2 and d["driverFirstName"] == "Dilani"
    first = trip["route"]["orderedStops"][1]["parcelId"]
    assert client.get(f"/api/track/{first and db.load_entity('parcels', first)['trackingNumber']}").json()["stopsAway"] == 0
    assert d["deliverTo"] == "Fort Area" and d["eta"]
    assert [h["message"] for h in d["history"]][0] == "Order received"
    assert client.get("/api/track/FO-NOPE0000").status_code == 404
    client.post(f"/api/parcels/{a['id']}/pod", json={"method": "handed_over", "receivedBy": "Anil"})
    done = client.get(f"/api/track/{a['trackingNumber']}").json()
    assert done["headline"] == "Delivered" and done["receivedBy"] == "Anil" and done["step"] == 3 and done["eta"] is None


def test_tracking_lookups_are_rate_limited():
    main._track_hits.clear()
    old = main.TRACK_RATE_PER_MIN
    main.TRACK_RATE_PER_MIN = 3
    try:
        codes = [client.get("/api/track/FO-NOPE0000").status_code for _ in range(5)]
        assert codes[:3] == [404, 404, 404] and codes[3:] == [429, 429]
    finally:
        main.TRACK_RATE_PER_MIN = old
        main._track_hits.clear()


def test_admin_notification_endpoints():
    p = _parcel("Listed", recipientPhone="+94 71 000 0000")
    _run([p])
    got = client.get(f"/api/parcels/{p['id']}/notifications").json()
    assert got["trackingUrl"].endswith(p["trackingNumber"]) and got["messages"]
    allm = client.get("/api/notifications").json()
    assert allm["providerConfigured"] is False and allm["messages"]


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
