"""The registration-first demo, from an empty system: register vehicles and drivers, add parcels,
Suggest runs, approve, and the warehouse sees what to hand each driver. Offline (FAKE_ROUTES; the
AI planner is made to fail so the built-in planner is used).
Run: `venv/bin/python tests/test_fresh_demo.py` (or pytest)."""

import os
import subprocess
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.environ["FAKE_ROUTES"] = "1"
os.environ.setdefault("GOOGLE_API_KEY", "unused")
os.environ.setdefault("MAPS_API_KEY", "unused")
os.environ["DB_PATH"] = os.path.join(tempfile.mkdtemp(), "fresh.db")
subprocess.run([sys.executable, os.path.join(ROOT, "samples", "seed_demo.py"), "--reset", "--users", "--fresh"], check=True,
               env=dict(os.environ), stdout=subprocess.DEVNULL)
sys.path.insert(0, ROOT)

from fastapi.testclient import TestClient     # noqa: E402

from app.main import app                      # noqa: E402
import app.planner.service as planner_service  # noqa: E402


async def _no_ai(*a, **k):
    raise RuntimeError("AI planner unavailable (test)")


planner_service.plan_route = _no_ai
client = TestClient(app)

VEHICLES = [("Van 01", "CAB-4471", "van"), ("Van 02", "CAB-5120", "van"), ("Car 01", "KY-3308", "car"), ("Bike 01", "BKX-210", "bike")]
DRIVERS = [("Nuwan Perera", "+94 77 123 4567", "Van 01"), ("Kamal Silva", "+94 71 555 0182", "Van 02"),
           ("Dilani Fernando", "+94 76 220 9914", "Car 01"), ("Ruwan Jayasinghe", "+94 70 441 7765", "Bike 01")]
PLACES = [("Majestic City", 6.8935, 79.8553), ("Nugegoda Junction", 6.8649, 79.8997), ("Rajagiriya Flyover", 6.9097, 79.8918),
          ("Borella Junction", 6.9147, 79.8776), ("Maharagama Town", 6.8480, 79.9265), ("SLIIT Malabe", 6.9061, 79.9694),
          ("Dehiwala Junction", 6.8511, 79.8650), ("Battaramulla", 6.8993, 79.9179), ("Mount Lavinia", 6.8389, 79.8636),
          ("Kiribathgoda", 6.9780, 79.9300)]


def test_01_the_system_starts_empty_but_set_up():
    assert client.get("/api/vehicles").json() == [] and client.get("/api/drivers").json() == []
    assert client.get("/api/parcels").json() == [] and client.get("/api/trips").json() == []
    assert len(client.get("/api/route-templates").json()) == 3, "the standing routes are ready"
    assert client.get("/api/settings/depot").json()["label"], "the hub is set"
    assert client.get("/api/auth/demo-accounts").json()["enabled"] is False     # (only listed when DEMO_ACCOUNTS=1)


def test_02_register_vehicles_then_drivers():
    ids = {}
    for name, plate, kind in VEHICLES:
        r = client.post("/api/vehicles", json={"name": name, "plate": plate, "type": kind})
        assert r.status_code == 200, r.text
        ids[name] = r.json()["id"]
    for name, phone, veh in DRIVERS:
        r = client.post("/api/drivers", json={"name": name, "phone": phone, "vehicleId": ids[veh]})
        assert r.status_code == 200, r.text
    assert len(client.get("/api/vehicles").json()) == 4 and len(client.get("/api/drivers").json()) == 4


def test_03_parcels_then_suggest_runs_then_the_warehouse_hands_over_per_driver():
    for i, (place, lat, lng) in enumerate(PLACES):
        r = client.post("/api/parcels", json={
            "name": f"Parcel {i + 1}", "size": "small", "weightKg": 1.5, "receiverName": f"Customer {i + 1}",
            "deadline": time.time() + 6 * 3600, "destination": {"lat": lat, "lng": lng, "label": place}})
        assert r.status_code == 200, r.text
    plan = client.post("/api/dispatch/propose", json={"engine": "rules"}).json()
    assert plan["batches"] and not plan["unassigned"], plan
    res = client.post("/api/dispatch/approve", json={"batches": [{"driverId": b["driverId"], "parcelIds": b["parcelIds"]}
                                                                  for b in plan["batches"]]}).json()
    assert len(res["created"]) == len(plan["batches"]) and not res["errors"], res
    board = client.get("/api/warehouse/loading").json()
    drivers = {r["driverName"] for r in board["runs"]}
    assert drivers == {b["driverName"] for b in plan["batches"]}, "one hand-over list per driver"
    assert board["totals"]["parcels"] == len(PLACES) and board["totals"]["loaded"] == 0
    for r in board["runs"]:
        assert r["plate"] and [p["loadOrder"] for p in r["parcels"]] == list(range(1, r["total"] + 1))


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
