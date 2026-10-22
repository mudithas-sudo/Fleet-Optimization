"""What the solution is worth: savings vs the as-entered order, fuel and CO2,
planned vs actual, ETA accuracy and vehicle breakdown handling — no Gemini, no
Google (FAKE_ROUTES). Run: `venv/bin/python tests/test_impact.py` (or pytest)."""

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

from app import db, delivery, impact, routing, store  # noqa: E402
from app.main import app                      # noqa: E402

HUB = {"lat": 6.93, "lng": 79.84, "label": "Colombo Hub"}
delivery.get_depot = lambda: HUB
client = TestClient(app)


def _parcel(name, lat, lng, hours=6, **over):
    return delivery.new_parcel({"name": name, "size": "small", "deadline": time.time() + hours * 3600,
                                "destination": {"lat": lat, "lng": lng, "label": f"{name} addr"}, **over})


def _run(parcels, vtype="van", sequence=True, start=False):
    van = delivery.new_vehicle({"name": f"V{len(db.all_entities('vehicles'))}", "type": vtype})
    drv = delivery.new_driver({"name": f"D{len(db.all_entities('drivers'))}", "vehicleId": van["id"]})
    stops = delivery.build_run_stops(parcels)
    if sequence:
        stops = routing.sequence_stops(stops)
    route = asyncio.run(routing.compute_route(stops, vehicle_type=vtype, presequenced=True))
    trip = store.new_trip(stops, route, vehicle_type=vtype)
    trip.update({"vehicleId": van["id"], "driverId": drv["id"], "parcelIds": [p["id"] for p in parcels],
                 "status": "active" if start else "planned", "startedAt": time.time() if start else None,
                 "plannedEtaPerStop": delivery.compute_planned_etas(route, {p["id"]: p for p in parcels}, time.time())})
    store.save_trip(trip)
    for p in parcels:
        delivery.set_parcel_status(p, "assigned", trip_id=trip["id"])
        if start:
            delivery.set_parcel_status(p, "in_transit", trip_id=trip["id"])
    return trip


def _quiet_road():
    """Finish every other run, so a test only sees the runs it creates."""
    for t in store.list_trips():
        if t["status"] != "completed":
            t["status"] = "completed"
            store.save_trip(t)


def _zigzag():
    # entered far, near, far, near: a by-hand run bounces across the city
    return [_parcel("Far A", 6.93, 80.05), _parcel("Near A", 6.93, 79.86),
            _parcel("Far B", 6.93, 80.07), _parcel("Near B", 6.93, 79.88)]


def test_optimised_order_saves_distance_time_fuel_and_co2():
    trip = _run(_zigzag(), vtype="van")
    r = client.get("/api/impact?days=all")
    assert r.status_code == 200, r.text
    d = r.json()
    run = next(x for x in d["perRun"] if x["tripId"] == trip["id"])
    assert run["optimisedKm"] < run["baselineKm"] and run["savedKm"] > 5 and run["savedPct"] > 20
    assert run["savedMin"] > 0 and run["savedLitres"] > 0 and run["savedCo2Kg"] > 0 and run["savedCost"] > 0
    f = impact.get_fuel()
    assert abs(run["savedLitres"] - run["savedKm"] * f["l100"]["van"] / 100) < 0.05
    assert abs(run["savedCo2Kg"] - run["savedLitres"] * f["co2PerL"]) < 0.05
    s = d["savings"]
    assert s["savedKm"] >= run["savedKm"] - 0.1 and s["savedPct"] > 0 and d["footprint"]["litres"] > 0
    assert "straight-line" in d["method"]


def test_a_run_never_reports_a_negative_saving():
    ps = [_parcel("N1", 6.93, 79.86), _parcel("N2", 6.93, 79.88)]      # already entered in the best order
    trip = _run(ps)
    run = next(x for x in client.get("/api/impact?days=all").json()["perRun"] if x["tripId"] == trip["id"])
    assert run["savedKm"] >= 0 and run["savedPct"] >= 0


def test_fuel_depends_on_the_vehicle_and_the_settings_are_editable():
    a = _run([_parcel("T1", 6.93, 80.05), _parcel("T2", 6.93, 79.86), _parcel("T3", 6.93, 80.07)], vtype="truck")
    b = _run([_parcel("C1", 6.93, 80.05), _parcel("C2", 6.93, 79.86), _parcel("C3", 6.93, 80.07)], vtype="car")
    rows = {x["tripId"]: x for x in client.get("/api/impact?days=all").json()["perRun"]}
    assert rows[a["id"]]["litres"] > rows[b["id"]]["litres"] * 2          # a truck burns far more than a car
    r = client.put("/api/settings/fuel", json={"pricePerL": 500, "l100": {"car": 10}})
    assert r.status_code == 200 and r.json()["pricePerL"] == 500 and r.json()["l100"]["car"] == 10
    assert client.put("/api/settings/fuel", json={"pricePerL": -1}).status_code == 400
    rows = {x["tripId"]: x for x in client.get("/api/impact?days=all").json()["perRun"]}
    assert abs(rows[b["id"]]["fuelCost"] - rows[b["id"]]["litres"] * 500) < 2


def test_plan_vs_actual_and_eta_accuracy_use_what_really_happened():
    ps = [_parcel("E1", 6.95, 79.86), _parcel("E2", 6.97, 79.88)]
    trip = _run(ps, start=True)
    started = time.time() - 3600
    planned_s = trip["route"]["totalDurationSeconds"]
    for e in trip["plannedEtaPerStop"]:
        e["eta"] = started + 600 * (1 + trip["plannedEtaPerStop"].index(e))
    trip.update({"status": "completed", "startedAt": started, "endedAt": started + (planned_s + 2 * delivery.SERVICE_SECONDS) * 1.2,
                 "actualDistanceMeters": trip["route"]["totalDistanceMeters"] * 1.1})
    store.save_trip(trip)
    for i, p in enumerate(ps):             # first on time (+5 min), second 40 min late
        p = db.load_entity("parcels", p["id"])
        p["status"], p["tripId"] = "delivered", trip["id"]
        p["deliveredAt"] = started + 600 * (1 + i) + (300 if i == 0 else 2400)
        db.save_entity("parcels", p)
    d = client.get("/api/impact?days=all").json()
    pva = next(x for x in d["planVsActual"]["runs"] if x["tripId"] == trip["id"])
    assert pva["kmVarPct"] == 10 and 15 <= pva["minVarPct"] <= 25
    assert d["eta"]["stops"] >= 2 and d["eta"]["within15Pct"] is not None and d["eta"]["meanErrorMin"] > 0


def test_starting_a_run_promises_times_from_the_real_departure():
    p = _parcel("Late start", 6.95, 79.86)
    trip = _run([p])
    trip["plannedEtaPerStop"][0]["eta"] -= 7200                    # planned long before the driver leaves
    store.save_trip(trip)
    assert client.post(f"/api/trips/{trip['id']}/start").status_code == 200
    eta = store.get_trip(trip["id"])["plannedEtaPerStop"][0]["eta"]
    assert eta > time.time(), "the ETA now counts from when the driver actually left"


def test_vehicle_breakdown_hands_parcels_to_other_runs():
    _quiet_road()
    broken = _run([_parcel("B1", 6.95, 79.86, hours=5), _parcel("B2", 6.96, 79.87, hours=5),
                   _parcel("B3", 6.97, 79.88, hours=5)], start=True)
    helper = _run([_parcel("H1", 6.94, 79.85, hours=6)], start=True)
    prev = client.post(f"/api/runs/{broken['id']}/breakdown", json={}).json()
    assert prev["applied"] is False and len(prev["moved"]) + len(prev["unplaced"]) == 3
    assert all(m["toTripId"] == helper["id"] for m in prev["moved"])
    assert db.load_entity("parcels", broken["parcelIds"][0])["status"] == "in_transit", "a preview changes nothing"
    res = client.post(f"/api/runs/{broken['id']}/breakdown", json={"apply": True}).json()
    assert res["applied"] and len(res["moved"]) == 3 and not res["unplaced"]
    assert store.get_trip(broken["id"])["status"] == "completed"
    assert any(a["type"] == "breakdown" for a in store.get_trip(broken["id"])["alerts"])
    h = store.get_trip(helper["id"])
    assert len(h["parcelIds"]) == 4 and h["routeVersion"] >= 2
    for pid in broken["parcelIds"]:
        assert db.load_entity("parcels", pid)["tripId"] == helper["id"]


def test_breakdown_with_nowhere_to_go_leaves_parcels_pending_and_collected_ones_recovered():
    ps = [_parcel("Lonely 1", 6.95, 79.86), _parcel("Lonely 2", 6.96, 79.87)]
    broken = _run(ps, start=True)
    delivery.set_parcel_status(db.load_entity("parcels", ps[0]["id"]), "picked_up")
    for t in store.list_trips():           # nobody else is on the road
        if t["id"] != broken["id"] and t["status"] != "completed":
            t["status"] = "completed"
            store.save_trip(t)
    res = client.post(f"/api/runs/{broken['id']}/breakdown", json={"apply": True}).json()
    assert len(res["unplaced"]) == 2 and not res["moved"]
    assert db.load_entity("parcels", ps[1]["id"])["status"] == "pending"
    assert db.load_entity("parcels", ps[0]["id"])["status"] == "awaiting_redelivery"
    assert client.post(f"/api/runs/{broken['id']}/breakdown", json={}).status_code == 409, "already finished"


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
