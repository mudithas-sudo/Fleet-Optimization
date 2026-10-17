"""Deterministic checks for reports and CSV export — no keys (FAKE_ROUTES).
Run: `venv/bin/python tests/test_reports.py` (or pytest)."""

import asyncio
import csv
import io
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

from app import db, delivery, reports, routing, store  # noqa: E402
from app.main import app                      # noqa: E402

DEPOT = {"lat": 6.93, "lng": 79.84, "label": "Hub"}
delivery.get_depot = lambda: DEPOT
client = TestClient(app)
_n = [0]


def _parcel(name, deadline_offset=7200):
    _n[0] += 1
    return delivery.new_parcel({
        "name": name, "size": "small", "deadline": time.time() + deadline_offset,
        "destination": {"lat": 6.90 + _n[0] * 0.01, "lng": 79.85, "label": f"{name} addr"}})


def _run(parcels, driver):
    stops = delivery.build_run_stops(parcels)
    route = asyncio.run(routing.compute_route(stops, vehicle_type="van"))
    trip = store.new_trip(stops, route, vehicle_type="van")
    trip.update({"driverId": driver["id"], "vehicleId": driver["vehicleId"], "status": "active",
                 "parcelIds": [p["id"] for p in parcels], "plannedEtaPerStop": []})
    store.save_trip(trip)
    for p in parcels:
        delivery.set_parcel_status(p, "assigned", trip_id=trip["id"])
        delivery.set_parcel_status(p, "in_transit", trip_id=trip["id"])
    return trip


def _scenario():
    van = delivery.new_vehicle({"name": "Van", "type": "van"})
    drv = delivery.new_driver({"name": "Dee", "vehicleId": van["id"]})
    ok, late, bad, evil = _parcel("OnTime"), _parcel("Late", deadline_offset=-60), _parcel("Fails"), _parcel("=HYPERLINK(1)")
    trip = _run([ok, late, bad, evil], drv)
    for p in (ok, late):
        client.post(f"/api/parcels/{p['id']}/pod", json={"method": "handed_over", "receivedBy": "X"})
    client.post(f"/api/parcels/{bad['id']}/fail", json={"reason": "no_one_home"})
    t = store.get_trip(trip["id"])
    t.update({"actualDistanceMeters": 10_000, "startedAt": time.time() - 7200, "endedAt": time.time(), "status": "completed"})
    store.save_trip(t)
    return drv, trip


DRV, TRIP = _scenario()


def test_summary_numbers():
    s = reports.summary(7)
    assert s["parcels"]["delivered"] == 2 and s["parcels"]["onTime"] == 1 and s["parcels"]["onTimeRate"] == 50
    assert s["parcels"]["failedAttempts"] == 1 and s["failuresByReason"] == [{"reason": "Nobody home", "count": 1}]
    assert s["parcels"]["firstAttemptRate"] == 100
    assert s["runs"]["count"] == 1 and s["runs"]["km"] == 10.0 and s["runs"]["hours"] == 2.0
    row = s["perDriver"][0]
    assert row["driver"] == "Dee" and row["deliveries"] == 2 and row["onTimeRate"] == 50 and row["failedAttempts"] == 1
    costs = reports.get_costs()
    expected = round(10 * costs["perKm"] + 2 * costs["perHour"])
    assert row["cost"] == expected and s["cost"]["total"] == expected
    assert s["cost"]["perDelivery"] == round(expected / 2)
    assert s["daily"] and s["daily"][-1]["delivered"] == 2 and s["daily"][-1]["failed"] == 1


def test_windows():
    since, until = reports.window(0)
    assert since <= time.time() < until and time.localtime(since).tm_hour == 0
    assert reports.window(None)[0] == 0.0
    assert reports.summary(0)["parcels"]["delivered"] == 2     # done today
    future = time.time() + 30 * 86400
    since7, until7 = reports.window(7, now=future)               # a window 30 days from now excludes today's work
    assert since7 > time.time() and 7 * 86400 <= until7 - since7 <= 7 * 86400 + 1


def test_costs_roundtrip_changes_the_bill():
    r = client.put("/api/settings/costs", json={"perKm": 100, "perHour": 0, "currency": "usd"})
    assert r.status_code == 200 and r.json() == {"perKm": 100.0, "perHour": 0.0, "currency": "usd"}
    assert reports.summary(7)["cost"]["total"] == 1000
    assert client.put("/api/settings/costs", json={"perKm": -1, "perHour": 0}).status_code == 400
    client.put("/api/settings/costs", json={"perKm": 60, "perHour": 600, "currency": "LKR"})


def test_csv_exports():
    r = client.get("/api/reports/export?kind=parcels&days=7")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/csv")
    assert "attachment" in r.headers["content-disposition"] and r.text.startswith("﻿")
    rows = list(csv.reader(io.StringIO(r.text.lstrip("﻿"))))
    assert rows[0][0] == "tracking_number" and len(rows) == 5
    names = [row[1] for row in rows[1:]]
    assert "'=HYPERLINK(1)" in names, "formula-looking cells must be neutralised"
    late = next(row for row in rows[1:] if row[1] == "Late")
    assert late[rows[0].index("on_time")] == "no" and late[rows[0].index("driver")] == "Dee"
    runs = list(csv.reader(io.StringIO(client.get("/api/reports/export?kind=runs&days=all").text.lstrip("﻿"))))
    assert runs[1][0] == TRIP["id"] and runs[1][runs[0].index("km")] == "10.0"
    drivers = list(csv.reader(io.StringIO(client.get("/api/reports/export?kind=drivers").text.lstrip("﻿"))))
    assert drivers[1][0] == "Dee" and drivers[1][2] == "2"


def test_endpoint_validation():
    assert client.get("/api/reports/export?kind=secrets").status_code == 400
    assert client.get("/api/reports/summary?days=banana").status_code == 400
    assert client.get("/api/reports/summary?days=today").status_code == 200
    assert client.get("/api/reports/summary?days=all").json()["period"]["days"] is None


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
