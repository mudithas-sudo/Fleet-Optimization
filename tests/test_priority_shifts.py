"""Deterministic checks for priorities, delivery windows and driver shifts —
no Gemini, no Google (FAKE_ROUTES). Run: `venv/bin/python tests/test_priority_shifts.py`."""

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

from fastapi.testclient import TestClient            # noqa: E402

from app import db, delivery, routing                 # noqa: E402
from app.dispatcher.rules import propose_by_rules     # noqa: E402
from app.main import app                              # noqa: E402

DEPOT = {"lat": 6.93, "lng": 79.84, "label": "Hub"}
delivery.get_depot = lambda: DEPOT
client = TestClient(app)


def _reset():
    for table in ("parcels", "vehicles", "drivers"):
        for e in db.all_entities(table):
            db.delete_entity(table, e["id"])


def _parcel(name, lat, lng, **kw):
    return delivery.new_parcel({
        "name": name, "size": "small", "deadline": time.time() + 7200,
        "destination": {"lat": lat, "lng": lng, "label": name}, **kw})


def test_sequence_puts_express_first_then_nearest():
    near = {"lat": 6.931, "lng": 79.841, "label": "near", "kind": "delivery", "parcelId": "a"}
    mid = {"lat": 6.95, "lng": 79.86, "label": "mid", "kind": "delivery", "parcelId": "b"}
    far = {"lat": 7.10, "lng": 79.95, "label": "far", "kind": "delivery", "parcelId": "c", "priority": "express"}
    out = routing.sequence_stops([dict(DEPOT, kind="depot"), near, mid, far])
    assert [s["label"] for s in out[1:]] == ["far", "mid", "near"]   # then nearest from where you are


def test_sequence_defers_stops_whose_window_has_not_opened():
    soon = {"lat": 6.931, "lng": 79.841, "label": "not-yet", "kind": "delivery", "parcelId": "a",
            "windowStart": time.time() + 3 * 3600}
    ready = {"lat": 6.99, "lng": 79.90, "label": "ready", "kind": "delivery", "parcelId": "b"}
    out = routing.sequence_stops([dict(DEPOT, kind="depot"), soon, ready])
    assert [s["label"] for s in out[1:]] == ["ready", "not-yet"]


def test_sequence_keeps_pickup_before_delivery():
    pick = {"lat": 7.0, "lng": 79.9, "label": "pick", "kind": "pickup", "parcelId": "a", "priority": "standard"}
    drop = {"lat": 6.931, "lng": 79.841, "label": "drop", "kind": "delivery", "parcelId": "a", "priority": "express"}
    out = routing.sequence_stops([dict(DEPOT, kind="depot"), drop, pick])
    assert [s["label"] for s in out[1:]] == ["pick", "drop"]


def test_driver_availability_and_limits():
    v = delivery.new_vehicle({"name": "V", "type": "van"})
    leave = delivery.new_driver({"name": "Lea", "vehicleId": v["id"], "onLeave": True})
    assert delivery.driver_unavailable_reason(leave) == "on leave"
    night = delivery.new_driver({"name": "Nia", "vehicleId": v["id"], "shiftStart": "00:00", "shiftEnd": "00:01"})
    assert "off shift" in delivery.driver_unavailable_reason(night)
    day = delivery.new_driver({"name": "Dan", "vehicleId": v["id"], "shiftStart": "00:00", "shiftEnd": "23:59",
                               "maxStops": 2})
    assert delivery.driver_unavailable_reason(day) is None
    ps = [_parcel(f"P{i}", 6.93 + i * 0.001, 79.85) for i in range(3)]
    assert "at most 2 stops" in delivery.check_eligibility(v, ps, day)
    assert delivery.check_eligibility(v, ps[:2], day) in (None,) or "shift end" in delivery.check_eligibility(v, ps[:2], day)
    tired = delivery.new_driver({"name": "Tia", "vehicleId": v["id"], "maxHours": 0.2})
    assert "limit" in delivery.check_eligibility(v, ps, tired)


def test_rules_engine_skips_unavailable_drivers_and_prefers_express():
    _reset()
    v1 = delivery.new_vehicle({"name": "Small", "type": "car", "capacity": 2})
    v2 = delivery.new_vehicle({"name": "Big", "type": "van"})
    delivery.new_driver({"name": "Works", "vehicleId": v1["id"]})
    delivery.new_driver({"name": "OnLeave", "vehicleId": v2["id"], "onLeave": True})
    normal = [_parcel(f"N{i}", 6.94 + i * 0.001, 79.85) for i in range(2)]
    rush = _parcel("Rush", 7.05, 79.95, priority="express")
    plan = propose_by_rules()
    assert len(plan["batches"]) == 1 and plan["batches"][0]["driverName"] == "Works"
    assert rush["id"] in plan["batches"][0]["parcelIds"], "express must claim space first"
    assert len(plan["unassigned"]) == 1 and plan["unassigned"][0]["parcelId"] in {p["id"] for p in normal}
    assert any("OnLeave is on leave" in w for w in plan["warnings"])


def test_create_run_with_special_handling_is_deterministic_and_checks_driver():
    from app.delivery import RunError
    _reset()
    v = delivery.new_vehicle({"name": "Van", "type": "van"})
    d = delivery.new_driver({"name": "Dee", "vehicleId": v["id"]})
    a = _parcel("Near", 6.935, 79.845)
    b = _parcel("Rush", 7.0, 79.95, priority="express")
    trip = asyncio.run(delivery.create_run([a["id"], b["id"]], d["id"]))
    assert [s["parcelName"] for s in trip["route"]["orderedStops"][1:]] == ["Rush", "Near"]
    assert "Express" in trip["route"]["briefing"]
    away = delivery.new_driver({"name": "Away", "vehicleId": v["id"], "onLeave": True})
    c = _parcel("Other", 6.9, 79.8)
    try:
        asyncio.run(delivery.create_run([c["id"]], away["id"]))
        raise AssertionError("a driver on leave must not get a run")
    except RunError as e:
        assert "on leave" in str(e)


def test_api_validation():
    _reset()
    base = {"name": "X", "size": "small", "deadline": time.time() + 3600,
            "destination": {"lat": 6.9, "lng": 79.8, "label": "Somewhere"}}
    assert client.post("/api/parcels", json={**base, "priority": "urgent"}).status_code == 400
    assert client.post("/api/parcels", json={**base, "windowStart": time.time() + 7200}).status_code == 400
    ok = client.post("/api/parcels", json={**base, "priority": "express", "windowStart": time.time() + 600})
    assert ok.status_code == 200 and ok.json()["priority"] == "express"
    assert client.post("/api/drivers", json={"name": "D", "shiftStart": "08:00"}).status_code == 400
    assert client.post("/api/drivers", json={"name": "D", "shiftStart": "17:00", "shiftEnd": "08:00"}).status_code == 400
    assert client.post("/api/drivers", json={"name": "D", "shiftStart": "8am", "shiftEnd": "5pm"}).status_code == 400
    good = client.post("/api/drivers", json={"name": "D", "shiftStart": "08:00", "shiftEnd": "17:00", "maxStops": 12})
    assert good.status_code == 200 and good.json()["breakMinutes"] == 30


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
