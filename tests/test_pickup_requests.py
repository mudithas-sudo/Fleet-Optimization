"""Customer pickup requests that arrive during the day, run on the same vehicle
as deliveries, are collected by the driver, return to the hub, and go through
review when they fail — no Gemini, no Google (FAKE_ROUTES).
Run: `venv/bin/python tests/test_pickup_requests.py` (or pytest)."""

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

HUB = {"lat": 6.93, "lng": 79.84, "label": "Colombo Hub"}
delivery.get_depot = lambda: HUB
client = TestClient(app)


def _delivery(name, i):
    return delivery.new_parcel({
        "name": name, "size": "small", "deadline": time.time() + 7200,
        "destination": {"lat": 6.90 + i * 0.01, "lng": 79.85, "label": f"{name} addr"}})


def _request(name="Pickup", i=0, **over):
    body = {"name": name, "requestType": "pickup", "size": "small",
            "pickup": {"lat": 6.95 + i * 0.01, "lng": 79.88, "label": f"{name} street",
                       "earliest": time.time() + 600, "latest": time.time() + 3 * 3600}}
    body.update(over)
    r = client.post("/api/parcels", json=body)
    assert r.status_code == 200, r.text
    return r.json()


def _driver():
    van = delivery.new_vehicle({"name": "Van", "type": "van"})
    return delivery.new_driver({"name": "Dee", "vehicleId": van["id"]})


def _live_run(parcels):
    drv = _driver()
    stops = delivery.build_run_stops(parcels)
    stops = routing.sequence_stops(stops)
    route = asyncio.run(routing.compute_route(stops, vehicle_type="van", presequenced=True))
    trip = store.new_trip(stops, route, vehicle_type="van")
    trip.update({"vehicleId": drv["vehicleId"], "driverId": drv["id"], "status": "active",
                 "parcelIds": [p["id"] for p in parcels],
                 "plannedEtaPerStop": delivery.compute_planned_etas(route, {p["id"]: p for p in parcels}, time.time())})
    store.save_trip(trip)
    for p in parcels:
        delivery.set_parcel_status(p, "assigned", trip_id=trip["id"])
        delivery.set_parcel_status(p, "in_transit", trip_id=trip["id"])
    return trip


def test_request_needs_only_the_customer_address_and_returns_to_the_hub():
    p = _request("Docs")
    assert p["requestType"] == "pickup" and p["status"] == "pending"
    assert p["destination"]["label"] == "Colombo Hub", "a pickup is brought back to the hub"
    assert p["deadline"] == p["pickup"]["latest"]
    assert client.post("/api/parcels", json={"name": "x", "size": "small", "requestType": "pickup"}).status_code == 400
    assert client.post("/api/parcels", json={"name": "x", "size": "small"}).status_code == 400, "a delivery still needs a destination"
    assert client.post("/api/parcels", json={"name": "x", "size": "small", "requestType": "bogus"}).status_code == 400
    r = client.post("/api/parcels", json={"name": "old", "requestType": "pickup", "size": "small",
                                           "pickup": {"lat": 6.9, "lng": 79.9, "label": "Old", "latest": time.time() - 60}})
    assert r.status_code == 400


def test_pickups_and_deliveries_share_one_run_and_the_hub_comes_last():
    d1, d2 = _delivery("D1", 1), _delivery("D2", 5)
    p = _request("Mixed")
    stops = delivery.build_run_stops([d1, p, d2])
    ordered = routing.sequence_stops(stops)
    kinds = [(s.get("kind"), s.get("parcelName")) for s in ordered]
    assert kinds[0][0] == "depot"
    assert kinds[-1] == ("delivery", "Mixed") and ordered[-1]["hubReturn"], "the hub drop closes the run"
    assert kinds.index(("pickup", "Mixed")) < len(kinds) - 1
    route = asyncio.run(routing.compute_route(stops, vehicle_type="van"))
    assert route["orderedStops"][-1].get("hubReturn")
    assert {s["parcelName"] for s in route["orderedStops"][1:]} == {"D1", "D2", "Mixed"}


def test_driver_collects_and_the_hub_drop_completes_it():
    d1, p = _delivery("D3", 2), _request("Collect me", 1)
    trip = _live_run([d1, p])
    rows = client.get(f"/api/trips/{trip['id']}/parcels").json()
    row = next(r for r in rows if r["id"] == p["id"])
    assert row["collectFirst"] and row["stopIndex"] is not None
    # driving past does not collect it
    runtime = store.runtime(trip["id"])
    runtime["along"] = 10 ** 9
    trip["lastPosition"] = {"lat": HUB["lat"], "lng": HUB["lng"], "ts": time.time()}
    from app.main import _check_stop_progress
    _check_stop_progress(trip, runtime)
    assert db.load_entity("parcels", p["id"])["status"] == "in_transit", "pickups are never auto-collected"
    assert db.load_entity("parcels", d1["id"])["status"] == "delivered"
    r = client.post(f"/api/parcels/{p['id']}/collect", json={"note": "Sealed box"})
    assert r.status_code == 200 and r.json()["status"] == "picked_up"
    assert client.post(f"/api/parcels/{p['id']}/collect").status_code == 409
    client.post(f"/api/trips/{trip['id']}/end", json={"arrived": True})
    got = db.load_entity("parcels", p["id"])
    assert got["status"] == "delivered" and not got["deadlineMissed"]
    assert any(e["message"] == "Received at the hub" for e in delivery.timeline(got))
    assert client.post(f"/api/parcels/{d1['id']}/collect").status_code == 409, "only pickup requests are collected"


def test_uncollected_pickup_goes_back_to_the_queue_when_the_run_ends():
    p = _request("Missed", 2)
    trip = _live_run([p])
    client.post(f"/api/trips/{trip['id']}/end", json={"arrived": True})
    got = db.load_entity("parcels", p["id"])
    assert got["status"] == "pending" and got["tripId"] is None


def test_failed_pickup_waits_for_review_then_reschedules_for_tomorrow():
    p = _request("Not ready", 3)
    _live_run([p])
    got = client.post(f"/api/parcels/{p['id']}/fail", json={"reason": "not_ready", "note": "Packing"}).json()
    assert got["status"] == "failed" and got["lastFailure"]["label"] == "Shipment not ready for pickup"
    tomorrow = time.time() + 30 * 3600
    r = client.post(f"/api/parcels/{p['id']}/review", json={"decision": "reschedule", "scheduledFor": tomorrow, "note": "Customer: tomorrow"})
    assert r.status_code == 200
    out = r.json()
    assert out["status"] == "pending" and out["holdUntil"] and out["pickup"]["latest"] == tomorrow
    assert not delivery.is_plannable(out), "held for tomorrow, so it isn't offered to today's runs"
    assert out["lastFailure"]["reason"] == "not_ready"
    # cancelling a failed pickup
    q = _request("Cancel me", 4)
    _live_run([q])
    client.post(f"/api/parcels/{q['id']}/fail", json={"reason": "cancelled"})
    assert client.post(f"/api/parcels/{q['id']}/review", json={"decision": "return"}).json()["status"] == "returned"


def test_early_collection_needs_confirmation_and_changes_sequencing():
    late = time.time() + 5 * 3600
    far = _request("Later", 6, pickup={"lat": 6.99, "lng": 79.95, "label": "Far", "earliest": late, "latest": late + 3600})
    near = _delivery("Near", 1)
    assert not far["earlyOk"]
    stops = routing.sequence_stops(delivery.build_run_stops([near, far]))
    pick = next(s for s in stops if s.get("kind") == "pickup")
    assert pick["earliest"] == late
    assert [s.get("kind") for s in stops[1:]].index("pickup") > [s.get("parcelName") for s in stops[1:]].index("Near"), \
        "a pickup before the customer's cut-off waits behind ready stops"
    assert client.post(f"/api/parcels/{near['id']}/early").status_code == 409, "deliveries have no cut-off"
    r = client.post(f"/api/parcels/{far['id']}/early")
    assert r.status_code == 200 and r.json()["earlyOk"]
    stops2 = delivery.build_run_stops([db.load_entity("parcels", far["id"])])
    assert next(s for s in stops2 if s.get("kind") == "pickup")["earliest"] is None


def test_customer_messages_and_tracking_speak_about_collection():
    p = _request("Msg", 7, recipientPhone="+94 71 000 1111", receiverName="Nimal Perera")
    trip = _live_run([p])
    msgs = {m["event"]: m["message"] for m in client.get(f"/api/parcels/{p['id']}/notifications").json()["messages"]}
    assert "collect" in msgs["out_for_delivery"].lower()
    t = client.get(f"/api/track/{p['trackingNumber']}").json()
    assert t["kind"] == "pickup" and t["headline"] == "Driver on the way to collect"
    assert t["steps"][-1] == "Collected"
    client.post(f"/api/parcels/{p['id']}/fail", json={"reason": "customer_unavailable"})
    t = client.get(f"/api/track/{p['trackingNumber']}").json()
    assert "couldn’t collect" in t["headline"] or "couldn't collect" in t["headline"]
    assert trip


def test_rides_next_stop_is_the_pickup_until_it_is_collected():
    from app import rides
    p = _request("Ride", 8)
    trip = _live_run([p])
    ride = next(r for r in rides.rides_at()["rides"] if r["id"] == trip["id"])
    assert ride["nextStop"]["parcel"] == "Ride" and ride["nextStop"]["label"].startswith("Ride street")
    client.post(f"/api/parcels/{p['id']}/collect")
    ride = next(r for r in rides.rides_at()["rides"] if r["id"] == trip["id"])
    assert ride["nextStop"]["label"] == "Colombo Hub" and ride["overdue"] == 0


def test_a_pickup_arriving_mid_day_is_matched_to_the_best_run():
    near_run = _live_run([_delivery("Fit A", 1)])
    # a nearby request fits; one past its collect-by time and one too heavy do not
    p = _request("Fits", 1)
    runs = client.get(f"/api/parcels/{p['id']}/fit").json()["runs"]
    mine = next(r for r in runs if r["tripId"] == near_run["id"])
    assert mine["feasible"] and mine["extraMinutes"] >= 6 and mine["state"] == "under way"
    assert runs == sorted(runs, key=lambda r: (not r["feasible"], r.get("extraMinutes", 10 ** 6) + r.get("waitMinutes", 0)))
    tight = _request("Too late", 2, pickup={"lat": 6.99, "lng": 79.95, "label": "Far", "earliest": None,
                                            "latest": time.time() + 60})
    row = next(r for r in client.get(f"/api/parcels/{tight['id']}/fit").json()["runs"] if r["tripId"] == near_run["id"])
    assert not row["feasible"] and "collect-by" in row["reason"]
    heavy = _request("Heavy", 3, weightKg=1900)
    row = next(r for r in client.get(f"/api/parcels/{heavy['id']}/fit").json()["runs"] if r["tripId"] == near_run["id"])
    assert not row["feasible"] and row["reason"]
    # approving = the existing add-to-run, which re-plans from the vehicle's position
    ok = client.post(f"/api/runs/{near_run['id']}/parcels", json={"parcelIds": [p["id"]]})
    assert ok.status_code == 200, ok.text
    assert db.load_entity("parcels", p["id"])["status"] in ("assigned", "in_transit")
    assert client.get(f"/api/parcels/{p['id']}/fit").status_code == 409


def test_driver_key_may_collect():
    import re
    from app import auth
    assert any(m == "POST" and rx.match("/api/parcels/abc/collect") for m, rx, _ in auth.DRIVER_ROUTES)
    assert re  # imported for the pattern above


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
