"""Deterministic checks for dispatcher run edits (add/remove/move parcels,
change driver) — no Gemini, no Google (FAKE_ROUTES).
Run: `venv/bin/python tests/test_replan.py` (or pytest)."""

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

from app import db, delivery, replan, routing, store  # noqa: E402
from app.main import app                      # noqa: E402

DEPOT = {"lat": 6.93, "lng": 79.84, "label": "Hub"}
delivery.get_depot = lambda: DEPOT
client = TestClient(app)
_n = [0]


def _parcel(name, **kw):
    _n[0] += 1
    return delivery.new_parcel({
        "name": name, "size": kw.pop("size", "small"), "deadline": time.time() + 7200,
        "destination": {"lat": 6.90 + _n[0] * 0.004, "lng": 79.85 + _n[0] * 0.002, "label": f"{name} addr"}, **kw})


def _driver(name, vtype="van", **kw):
    v = delivery.new_vehicle({"name": f"{name}-veh", "type": vtype})
    return delivery.new_driver({"name": name, "vehicleId": v["id"], **kw})


def _run(parcels, driver, status="planned"):
    stops = delivery.build_run_stops(parcels)
    route = asyncio.run(routing.compute_route(stops, vehicle_type="van"))
    trip = store.new_trip(stops, route, vehicle_type=driver and "van")
    trip.update({"driverId": driver["id"], "vehicleId": driver["vehicleId"], "parcelIds": [p["id"] for p in parcels],
                 "plannedEtaPerStop": [], "status": status})
    if status == "active":
        trip["lastPosition"] = {"lat": 6.95, "lng": 79.87, "ts": time.time()}
    store.save_trip(trip)
    for p in parcels:
        delivery.set_parcel_status(p, "assigned", trip_id=trip["id"])
        if status == "active":
            delivery.set_parcel_status(p, "in_transit", trip_id=trip["id"])
    return trip


def _names(trip):
    return [s["parcelName"] for s in trip["route"]["orderedStops"][1:]]


def test_add_to_planned_run():
    d = _driver("Ann")
    a, b, c = _parcel("A"), _parcel("B"), _parcel("C")
    trip = _run([a, b], d)
    out = asyncio.run(replan.replan(trip["id"], add_ids=[c["id"]]))
    assert set(_names(out)) == {"A", "B", "C"} and out["routeVersion"] == 2
    assert db.load_entity("parcels", c["id"])["status"] == "assigned"
    assert out["alerts"][-1]["type"] == "replanned"
    kinds = [e["type"] for e in delivery.timeline(db.load_entity("parcels", c["id"]))]
    assert kinds[-1] == "assigned"


def test_add_to_run_in_progress_starts_from_current_position():
    d = _driver("Bob")
    a, b = _parcel("A"), _parcel("B")
    trip = _run([a], d, status="active")
    out = asyncio.run(replan.replan(trip["id"], add_ids=[b["id"]]))
    first = out["route"]["orderedStops"][0]
    assert first["label"] == "Current location" and abs(first["lat"] - 6.95) < 1e-9
    assert out["status"] == "active" and out["routeVersion"] == 2
    assert db.load_entity("parcels", b["id"])["status"] == "in_transit"


def test_remove_parcel_rules():
    d = _driver("Cy")
    a, b, c = _parcel("A"), _parcel("B"), _parcel("C")
    trip = _run([a, b, c], d, status="active")
    delivery.set_parcel_status(db.load_entity("parcels", a["id"]), "delivered")
    try:
        asyncio.run(replan.replan(trip["id"], remove_ids=[a["id"]]))
        raise AssertionError("a delivered parcel can't be taken off")
    except delivery.RunError as e:
        assert "delivered" in str(e)
    out = asyncio.run(replan.replan(trip["id"], remove_ids=[b["id"]]))
    assert b["id"] not in out["parcelIds"] and _names(out) == ["C"]
    assert db.load_entity("parcels", b["id"])["status"] == "pending"
    assert db.load_entity("parcels", b["id"])["tripId"] is None
    # a parcel already collected goes back as awaiting redelivery
    delivery.set_parcel_status(db.load_entity("parcels", c["id"]), "picked_up")
    d2 = _driver("Cy2")
    other = _run([_parcel("Z"), _parcel("Y")], d2)
    try:
        asyncio.run(replan.replan(trip["id"], remove_ids=[c["id"]]))
        raise AssertionError("can't empty a run")
    except delivery.RunError as e:
        assert "at least one parcel" in str(e)
    asyncio.run(replan.replan(other["id"], remove_ids=[other["parcelIds"][0]]))


def test_capacity_and_availability_guard_edits():
    bike = _driver("Bea", vtype="bike")
    a = _parcel("A")
    trip = _run([a], bike)
    big = _parcel("Big", size="large", dimensionsCm={"l": 200, "w": 100, "h": 100})
    try:
        asyncio.run(replan.replan(trip["id"], add_ids=[big["id"]]))
        raise AssertionError("a bike can't take that")
    except delivery.RunError as e:
        assert "can't take that" in str(e)
    assert db.load_entity("parcels", big["id"])["status"] == "pending"
    taken = _parcel("Taken")
    other = _run([taken], _driver("Dot"))
    try:
        asyncio.run(replan.replan(trip["id"], add_ids=[taken["id"]]))
        raise AssertionError("already on another run")
    except delivery.RunError as e:
        assert "not available" in str(e)
    assert other["id"]


def test_move_parcel_between_runs():
    d1, d2 = _driver("Eve"), _driver("Fay")
    a, b, c = _parcel("A"), _parcel("B"), _parcel("C")
    t1, t2 = _run([a, b], d1), _run([c], d2)
    target, source = asyncio.run(replan.move_parcel(b["id"], t2["id"]))
    assert set(_names(target)) == {"B", "C"} and _names(source) == ["A"]
    assert db.load_entity("parcels", b["id"])["tripId"] == t2["id"]
    kinds = [e["message"] for e in db.load_entity("parcels", b["id"])["events"]]
    assert any("Taken off run" in k for k in kinds) and any("Added to run" in k for k in kinds)
    # moving the last parcel of a planned run cancels that run
    t3 = _run([_parcel("Solo")], _driver("Gus"))
    solo = t3["parcelIds"][0]
    asyncio.run(replan.move_parcel(solo, t2["id"]))
    assert store.get_trip(t3["id"]) is None
    # ... but not off a run that is already under way
    live = _run([_parcel("Live")], _driver("Hal"), status="active")
    try:
        asyncio.run(replan.move_parcel(live["parcelIds"][0], t2["id"]))
        raise AssertionError("must not strand a live run")
    except delivery.RunError as e:
        assert "last parcel" in str(e)
    assert store.get_trip(live["id"])["parcelIds"]


def test_move_to_ineligible_run_leaves_everything_alone():
    van, bike = _driver("Ida"), _driver("Jo", vtype="bike")
    a, big = _parcel("A"), _parcel("Big", size="large", dimensionsCm={"l": 200, "w": 100, "h": 100})
    t_van = _run([a, big], van)
    t_bike = _run([_parcel("Tiny")], bike)
    try:
        asyncio.run(replan.move_parcel(big["id"], t_bike["id"]))
        raise AssertionError("bike can't take it")
    except delivery.RunError:
        pass
    assert db.load_entity("parcels", big["id"])["tripId"] == t_van["id"]
    assert set(_names(store.get_trip(t_van["id"]))) == {"A", "Big"}


def test_reassign_driver():
    d1, d2, busy = _driver("Kim"), _driver("Lee", vtype="car"), _driver("Max")
    a = _parcel("A")
    trip = _run([a], d1)
    _run([_parcel("B")], busy)
    try:
        asyncio.run(replan.replan(trip["id"], driver_id=busy["id"]))
        raise AssertionError("busy driver")
    except delivery.RunError as e:
        assert "unfinished run" in str(e)
    out = asyncio.run(replan.replan(trip["id"], driver_id=d2["id"]))
    assert out["driverId"] == d2["id"] and out["vehicleId"] == d2["vehicleId"]
    live = _run([_parcel("C")], _driver("Ned"), status="active")
    try:
        asyncio.run(replan.replan(live["id"], driver_id=_driver("Oz")["id"]))
        raise AssertionError("live run can't change driver")
    except delivery.RunError as e:
        assert "under way" in str(e)
    away = _driver("Pat", onLeave=True)
    try:
        asyncio.run(replan.replan(out["id"], driver_id=away["id"]))
        raise AssertionError("on leave")
    except delivery.RunError as e:
        assert "on leave" in str(e)


def test_api_endpoints():
    d1, d2 = _driver("Quin"), _driver("Rae")
    a, b = _parcel("A"), _parcel("B")
    t1, t2 = _run([a], d1), _run([b], d2)
    c = _parcel("C")
    r = client.post(f"/api/runs/{t1['id']}/parcels", json={"parcelIds": [c["id"]]})
    assert r.status_code == 200 and r.json()["parcelCount"] == 2
    r = client.post(f"/api/parcels/{c['id']}/move", json={"toTripId": t2["id"]})
    assert r.status_code == 200 and r.json()["to"]["parcelCount"] == 2 and r.json()["from"]["parcelCount"] == 1
    assert client.delete(f"/api/runs/{t2['id']}/parcels/{c['id']}").status_code == 200
    assert client.post(f"/api/runs/nope/parcels", json={"parcelIds": [c["id"]]}).status_code == 404
    assert client.post(f"/api/runs/{t1['id']}/parcels", json={"parcelIds": [a["id"]]}).status_code == 409
    opts = client.get(f"/api/runs/{t1['id']}/options").json()
    assert opts["canReassign"] is True
    assert {o["name"] for o in opts["drivers"]} >= {"Rae"}
    assert next(o for o in opts["drivers"] if o["name"] == "Rae")["reason"] == "has an unfinished run"


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
