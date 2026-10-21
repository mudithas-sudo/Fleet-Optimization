"""Deterministic checks for proof of delivery, failed deliveries and parcel
timelines — no Gemini, no Google (FAKE_ROUTES).
Run: `venv/bin/python tests/test_pod_failures.py` (or pytest)."""

import asyncio
import os
import sys
import tempfile
import time

os.environ.setdefault("FAKE_ROUTES", "1")
os.environ.setdefault("GOOGLE_API_KEY", "unused")
os.environ.setdefault("MAPS_API_KEY", "unused")
os.environ["DB_PATH"] = os.path.join(tempfile.mkdtemp(), "t.db")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi.testclient import TestClient     # noqa: E402

from app import db, delivery, routing, store  # noqa: E402
from app.main import app                      # noqa: E402

DEPOT = {"lat": 6.93, "lng": 79.84, "label": "Hub"}
delivery.get_depot = lambda: DEPOT
client = TestClient(app)

# 1x1 transparent PNG
PNG = ("data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR4nGNgYGD4DwABBAEAX+XDSwAAAABJRU5ErkJggg==")


def _parcel(name, i):
    return delivery.new_parcel({
        "name": name, "size": "small", "deadline": time.time() + 7200,
        "destination": {"lat": 6.90 + i * 0.01, "lng": 79.85, "label": f"{name} addr"}})


def _live_run(parcels):
    """A started run containing the parcels, built without the AI planner."""
    van = delivery.new_vehicle({"name": "Van", "type": "van"})
    drv = delivery.new_driver({"name": "Dee", "vehicleId": van["id"]})
    stops = delivery.build_run_stops(parcels)
    route = asyncio.run(routing.compute_route(stops, vehicle_type="van"))
    trip = store.new_trip(stops, route, vehicle_type="van")
    trip.update({"vehicleId": van["id"], "driverId": drv["id"], "status": "active",
                 "parcelIds": [p["id"] for p in parcels], "plannedEtaPerStop": []})
    store.save_trip(trip)
    for p in parcels:
        delivery.set_parcel_status(p, "assigned", trip_id=trip["id"])
        delivery.set_parcel_status(p, "in_transit", trip_id=trip["id"])
    return trip


def test_timeline_records_lifecycle():
    p = _parcel("Lifecycle", 0)
    assert [e["type"] for e in delivery.timeline(p)] == ["created"]
    _live_run([p])
    kinds = [e["type"] for e in client.get(f"/api/parcels/{p['id']}").json()["timeline"]]
    assert kinds == ["created", "assigned", "out_for_delivery"]


def test_old_parcels_get_a_synthesised_timeline():
    legacy = {"id": "p-old", "createdAt": 1000, "deliveredAt": 2000, "deadlineMissed": True}
    tl = delivery.timeline(legacy)
    assert [e["type"] for e in tl] == ["created", "delivered"] and "after the deadline" in tl[1]["message"]


def test_pod_marks_delivered_and_stores_images_separately():
    p = _parcel("PodParcel", 1)
    trip = _live_run([p])
    r = client.post(f"/api/parcels/{p['id']}/pod", json={
        "method": "signature", "signature": PNG, "receivedBy": "A. Perera", "lat": 6.9, "lng": 79.85})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "delivered" and body["pod"]["hasSignature"] is True
    assert "signature" not in body["pod"], "image data must not ride along with the parcel"
    assert client.get("/api/parcels").json()  # list works
    listed = next(x for x in client.get("/api/parcels").json() if x["id"] == p["id"])
    assert "data:image" not in str(listed)
    images = client.get(f"/api/parcels/{p['id']}/pod").json()
    assert images["signature"] == PNG
    assert store.get_trip(trip["id"])["deliveredCount"] == 1
    kinds = [e["type"] for e in client.get(f"/api/parcels/{p['id']}").json()["timeline"]]
    assert "pod" in kinds and kinds[-1] == "delivered"
    # a second POD (evidence added after the route ticked the parcel off) doesn't double count
    r2 = client.post(f"/api/parcels/{p['id']}/pod", json={"method": "photo", "photo": PNG})
    assert r2.status_code == 200
    assert store.get_trip(trip["id"])["deliveredCount"] == 1


def test_pod_validation():
    p = _parcel("Validate", 2)
    assert client.post(f"/api/parcels/{p['id']}/pod", json={"method": "handed_over", "receivedBy": "X"}).status_code == 409
    _live_run([p])
    pid = p["id"]
    assert client.post(f"/api/parcels/{pid}/pod", json={"method": "teleport"}).status_code == 400
    assert client.post(f"/api/parcels/{pid}/pod", json={"method": "handed_over"}).status_code == 400
    assert client.post(f"/api/parcels/{pid}/pod", json={"method": "signature"}).status_code == 400
    assert client.post(f"/api/parcels/{pid}/pod", json={"method": "photo", "photo": "javascript:alert(1)"}).status_code == 400
    big = "data:image/png;base64," + "A" * 800_000
    assert client.post(f"/api/parcels/{pid}/pod", json={"method": "photo", "photo": big}).status_code == 413
    assert client.get(f"/api/parcels/{pid}").json()["status"] == "in_transit"   # nothing stuck half-way


def test_failed_delivery_waits_for_review_and_survives_trip_end():
    a, b = _parcel("FailMe", 3), _parcel("DeliverMe", 4)
    trip = _live_run([a, b])
    assert client.post(f"/api/parcels/{a['id']}/fail", json={"reason": "nonsense"}).status_code == 400
    r = client.post(f"/api/parcels/{a['id']}/fail", json={"reason": "no_one_home", "note": "Gate locked"})
    assert r.status_code == 200
    got = r.json()
    assert got["status"] == "failed" and got["tripId"] is None and got["review"]["state"] == "pending"
    assert got["failedAttempts"] == 1 and got["lastFailure"]["label"] == "Nobody home"
    # run arrives: the failed parcel must NOT be marked delivered with the rest
    client.post(f"/api/trips/{trip['id']}/end", json={"arrived": True})
    assert db.load_entity("parcels", a["id"])["status"] == "failed"
    assert db.load_entity("parcels", b["id"])["status"] == "delivered"
    assert store.get_trip(trip["id"])["failedCount"] == 1
    tl = client.get(f"/api/parcels/{a['id']}").json()["timeline"]
    assert any(e["type"] == "failed" and "Gate locked" in e["message"] for e in tl)


def test_review_reschedules_or_returns_nothing_automatic():
    p = _parcel("Stubborn", 5)
    for attempt in range(1, delivery.ESCALATE_AFTER + 1):
        _live_run([db.load_entity("parcels", p["id"])])
        r = client.post(f"/api/parcels/{p['id']}/fail", json={"reason": "no_one_home"})
        assert r.status_code == 200 and r.json()["status"] == "failed", "never auto-returned or auto-requeued"
        # a failed shipment is not plannable until it has been reviewed
        assert not delivery.is_plannable(db.load_entity("parcels", p["id"]))
        assert client.post(f"/api/parcels/{p['id']}/reschedule", json={"deadline": time.time() + 99}).status_code == 409
        if attempt < delivery.ESCALATE_AFTER:
            when = time.time() + 30 * 3600           # the customer asks for tomorrow
            rv = client.post(f"/api/parcels/{p['id']}/review", json={
                "decision": "reschedule", "note": "Customer asked for tomorrow", "scheduledFor": when,
                "destination": {"lat": 6.9, "lng": 79.9, "label": "Corrected St"}})
            assert rv.status_code == 200, rv.text
            got = rv.json()
            assert got["status"] == "awaiting_redelivery" and got["destination"]["label"] == "Corrected St"
            assert got["review"]["state"] == "done" and got["holdUntil"], "held out of today's planning"
            assert not delivery.is_plannable(got)
            # reviewing twice is refused
            assert client.post(f"/api/parcels/{p['id']}/review", json={"decision": "return"}).status_code == 409
            db.save_entity("parcels", {**got, "holdUntil": None})   # tomorrow arrives
    got = client.get(f"/api/parcels/{p['id']}").json()
    assert got["status"] == "failed" and got["escalate"], "third failure is flagged, not returned"
    assert client.post(f"/api/parcels/{p['id']}/review", json={"decision": "reschedule"}).status_code == 400
    assert client.post(f"/api/parcels/{p['id']}/review", json={"decision": "return", "note": "Gave up"}).json()["status"] == "returned"

    q = _parcel("Refused", 6)
    _live_run([q])
    assert client.post(f"/api/parcels/{q['id']}/fail", json={"reason": "refused"}).json()["status"] == "failed"

    z = _parcel("Manual", 7)
    assert client.post(f"/api/parcels/{z['id']}/reschedule", json={"deadline": time.time() - 5}).status_code == 400
    assert client.post(f"/api/parcels/{z['id']}/return").json()["status"] == "returned"


def test_trip_parcels_for_the_driver_app():
    a, b = _parcel("Stop A", 8), _parcel("Stop B", 9)
    trip = _live_run([a, b])
    rows = client.get(f"/api/trips/{trip['id']}/parcels").json()
    assert [r["name"] for r in rows] == sorted((r["name"] for r in rows),
                                               key=lambda n: next(x["stopIndex"] for x in rows if x["name"] == n))
    assert all(r["stopIndex"] is not None for r in rows)


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
