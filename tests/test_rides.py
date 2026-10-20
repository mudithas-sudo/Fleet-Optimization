"""Deterministic checks for "rides at a point in time" — no keys (FAKE_ROUTES).
Run: `venv/bin/python tests/test_rides.py` (or pytest)."""

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

from app import db, delivery, rides, routing, store  # noqa: E402
from app.main import app                      # noqa: E402

DEPOT = {"lat": 6.93, "lng": 79.84, "label": "Hub"}
delivery.get_depot = lambda: DEPOT
client = TestClient(app)
T0 = time.time() - 3 * 3600          # the run starts three hours ago


def _parcel(name, i):
    return delivery.new_parcel({
        "name": name, "size": "small", "deadline": time.time() + 7200,
        "destination": {"lat": 6.90 + i * 0.01, "lng": 79.85, "label": f"{name} addr"}})


def _set_event_time(parcel_id, kind, ts):
    p = db.load_entity("parcels", parcel_id)
    for e in p["events"]:
        if e["type"] == kind:
            e["ts"] = ts
    db.save_entity("parcels", p)


def _build():
    van = delivery.new_vehicle({"name": "Van", "type": "van", "plate": "CAB-1"})
    drv = delivery.new_driver({"name": "Dee", "vehicleId": van["id"]})
    a, b = _parcel("Alpha", 1), _parcel("Bravo", 2)
    stops = delivery.build_run_stops([a, b])
    route = asyncio.run(routing.compute_route(stops, vehicle_type="van"))
    trip = store.new_trip(stops, route, vehicle_type="van")
    for p in (a, b):
        delivery.set_parcel_status(p, "assigned", trip_id=trip["id"])
        delivery.set_parcel_status(p, "in_transit", trip_id=trip["id"])
    delivery.set_parcel_status(db.load_entity("parcels", a["id"]), "delivered")
    client.post(f"/api/parcels/{b['id']}/fail", json={"reason": "no_one_home"})
    _set_event_time(a["id"], "delivered", T0 + 1800)
    _set_event_time(b["id"], "failed", T0 + 2400)
    trip = store.get_trip(trip["id"])
    trip.update({
        "driverId": drv["id"], "vehicleId": van["id"], "parcelIds": [a["id"], b["id"]], "plannedEtaPerStop": [],
        "createdAt": T0 - 600, "startedAt": T0, "endedAt": T0 + 3600, "status": "completed",
        "alerts": [{"id": "a1", "tripId": trip["id"], "type": "deviation", "ts": T0 + 1000, "message": "off"},
                   {"id": "a2", "tripId": trip["id"], "type": "back_on_route", "ts": T0 + 1500, "message": "ok"}],
    })
    store.save_trip(trip)
    for k in range(0, 3600, 60):                 # one position a minute, moving north-east
        db.add_position(trip["id"], 6.93 + k * 1e-5, 79.84 + k * 1e-5, T0 + k)
    return trip, a, b


TRIP, A, B = _build()


def _ride(at):
    out = rides.rides_at(at)
    return next((r for r in out["rides"] if r["id"] == TRIP["id"]), None), out


def test_a_run_does_not_exist_before_it_was_created():
    ride, _ = _ride(T0 - 1200)
    assert ride is None


def test_scheduled_before_the_driver_starts():
    ride, _ = _ride(T0 - 300)
    assert ride["state"] == "scheduled" and ride["position"]["atStart"] is True
    assert (ride["position"]["lat"], ride["position"]["lng"]) == (DEPOT["lat"], DEPOT["lng"])


def test_en_route_shows_the_position_at_that_moment():
    ride, out = _ride(T0 + 900)
    assert ride["state"] == "en_route" and out["counts"]["en_route"] == 1
    assert abs(ride["position"]["lat"] - (6.93 + 900 * 1e-5)) < 1e-9
    assert ride["delivered"] == 0 and ride["nextStop"]["parcel"] == "Alpha"
    assert ride["driverName"] == "Dee" and ride["vehiclePlate"] == "CAB-1" and ride["polyline"]


def test_progress_follows_the_parcel_history():
    ride, _ = _ride(T0 + 2000)
    assert ride["delivered"] == 1 and ride["failed"] == 0 and ride["nextStop"]["parcel"] == "Bravo"
    ride, _ = _ride(T0 + 2500)
    assert ride["delivered"] == 1 and ride["failed"] == 1


def test_deviation_window_is_reconstructed_from_the_alert_log():
    assert _ride(T0 + 1200)[0]["state"] == "deviating"
    assert _ride(T0 + 900)[0]["state"] == "en_route"
    assert _ride(T0 + 1600)[0]["state"] == "en_route"


def test_finished_runs_stay_on_the_board_for_that_day():
    ride, out = _ride(T0 + 3000)
    assert ride["state"] == "en_route"
    ride, out = _ride(time.time() - 5 * 60)
    assert ride["state"] == "finished" and out["counts"]["finished"] >= 1
    assert out["dayStart"] <= out["at"] < out["dayEnd"]


def test_load_meter_and_exception_counts():
    ride, out = _ride(T0 + 900)
    assert 0 < ride["loadPct"] <= 100 and ride["onBoard"] == 2, ride
    ride2, out2 = _ride(T0 + 2000)
    assert ride2["onBoard"] == 1 and ride2["loadPct"] < ride["loadPct"], "load drops as parcels are delivered"
    assert out["exceptions"]["failed"] == 0
    _, late = _ride(T0 + 2500)
    assert late["exceptions"]["failed"] == 1
    assert set(ride) >= {"overdue", "atRisk"} and out["exceptions"]["ridesAtRisk"] >= 0


def test_live_uses_the_current_status():
    out = rides.rides_at(None)
    assert out["live"] is True
    assert next(r for r in out["rides"] if r["id"] == TRIP["id"])["state"] == "finished"


def test_runs_finished_before_the_day_began_are_left_out():
    old = store.get_trip(TRIP["id"])
    old["id"] = "t-old000"
    old["createdAt"] = rides.day_start(time.time()) - 7200
    old["startedAt"], old["endedAt"] = old["createdAt"] + 60, old["createdAt"] + 1800
    db.save_trip(old)
    assert "t-old000" not in [r["id"] for r in rides.rides_at(None)["rides"]]
    db.delete_trip("t-old000")


def test_api_and_trail():
    r = client.get(f"/api/rides?at={T0 + 900}")
    assert r.status_code == 200 and r.json()["rides"][0]["state"] in ("en_route", "scheduled")
    assert client.get("/api/rides?at=now").json()["live"] is True
    assert client.get("/api/rides").status_code == 200
    assert client.get("/api/rides?at=banana").status_code == 400
    assert client.get("/api/rides?at=-5").status_code == 400
    full = client.get(f"/api/trips/{TRIP['id']}/trail").json()["points"]
    part = client.get(f"/api/trips/{TRIP['id']}/trail?until={T0 + 600}").json()["points"]
    assert len(full) == 60 and len(part) == 11 and part[0] == full[0]
    assert client.get("/api/trips/nope/trail").status_code == 404


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
