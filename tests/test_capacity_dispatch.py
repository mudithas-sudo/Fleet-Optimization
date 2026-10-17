"""Deterministic checks for capacity (dimensions/weight/volume), the rules
dispatcher and manifest fields — no Gemini, no Google (FAKE_ROUTES).
Run: `venv/bin/python tests/test_capacity_dispatch.py` (or pytest)."""

import os
import sys
import tempfile

os.environ.setdefault("FAKE_ROUTES", "1")
os.environ.setdefault("GOOGLE_API_KEY", "unused")
os.environ.setdefault("MAPS_API_KEY", "unused")
os.environ["DB_PATH"] = os.path.join(tempfile.mkdtemp(), "t.db")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import db, delivery                            # noqa: E402
from app.dispatcher.rules import propose_by_rules       # noqa: E402

DEPOT = {"lat": 6.93, "lng": 79.84, "label": "Hub"}
delivery.get_depot = lambda: DEPOT


def _parcel(name, lat, lng, dims=None, kg=None, size="small", deadline=9e12):
    return delivery.new_parcel({
        "name": name, "size": size, "deadline": deadline, "weightKg": kg,
        "dimensionsCm": dims and {"l": dims[0], "w": dims[1], "h": dims[2]},
        "destination": {"lat": lat, "lng": lng, "label": name},
    })


def _reset():
    for table in ("parcels", "vehicles", "drivers"):
        for e in db.all_entities(table):
            db.delete_entity(table, e["id"])


def test_fit_orientation_weight_volume():
    van = delivery.new_vehicle({"name": "Van", "type": "van"})
    bike = delivery.new_vehicle({"name": "Bike", "type": "bike"})
    long_box = _parcel("Long", 6.9, 79.8, dims=(250, 20, 20), kg=5)   # size class ok, length is not
    assert delivery.check_eligibility(van, [long_box]) is None
    assert "doesn't fit" in delivery.check_eligibility(bike, [long_box])
    heavy = _parcel("Heavy", 6.9, 79.8, kg=50)
    assert "payload" in delivery.check_eligibility(bike, [heavy])
    # van usable volume = 300*170*130*0.85 = 5.6 m³, so 8 cubes of 0.73 m³ (5.8 m³) don't fit
    cubes = [_parcel(f"C{i}", 6.9, 79.8, dims=(90, 90, 90), kg=1) for i in range(8)]
    assert "m³" in delivery.check_eligibility(van, cubes)
    assert delivery.check_eligibility(van, cubes[:7]) is None


def test_rules_engine_uses_fewest_vehicles_and_flags_unfit():
    _reset()
    van = delivery.new_vehicle({"name": "Van 1", "type": "van"})
    bike = delivery.new_vehicle({"name": "Bike 1", "type": "bike"})
    delivery.new_driver({"name": "Vanna", "vehicleId": van["id"]})
    delivery.new_driver({"name": "Bix", "vehicleId": bike["id"]})
    small = [_parcel(f"S{i}", 6.92 + i * 0.002, 79.85, dims=(20, 20, 20), kg=2) for i in range(4)]
    huge = _parcel("Huge", 6.95, 79.9, dims=(900, 300, 300), kg=20, size="large")

    plan = propose_by_rules()
    assert plan["engine"] == "rules"
    assert len(plan["batches"]) == 1, "everything that fits should ride in the van"
    assert plan["batches"][0]["vehicleType"] == "van"
    assert set(plan["batches"][0]["parcelIds"]) == {p["id"] for p in small}
    assert [u["parcelId"] for u in plan["unassigned"]] == [huge["id"]]
    assert "No vehicle in the fleet" in plan["unassigned"][0]["reason"]


def test_rules_engine_spills_to_second_vehicle():
    _reset()
    for n in ("A", "B"):
        v = delivery.new_vehicle({"name": f"Car {n}", "type": "car", "capacity": 2})
        delivery.new_driver({"name": f"Driver {n}", "vehicleId": v["id"]})
    ps = [_parcel(f"P{i}", 6.9 + i * 0.01, 79.85 + i * 0.01) for i in range(5)]
    plan = propose_by_rules()
    assert len(plan["batches"]) == 2
    assert sum(len(b["parcelIds"]) for b in plan["batches"]) == 4
    assert len(plan["unassigned"]) == 1
    assert "No free vehicle" in plan["unassigned"][0]["reason"]
    assert {p["id"] for p in ps} >= {u["parcelId"] for u in plan["unassigned"]}


def test_manifest_fields_and_api():
    from fastapi.testclient import TestClient
    from app.main import app
    _reset()
    c = TestClient(app)
    body = {"name": "Router", "trackingNumber": "TRK-1", "receiverName": "A. Perera",
            "size": "small", "deadline": 9e12, "dimensionsCm": {"l": 30, "w": 20, "h": 10},
            "destination": {"lat": 6.9, "lng": 79.8, "label": "Galle Rd"}}
    r = c.post("/api/parcels", json=body)
    assert r.status_code == 200 and r.json()["trackingNumber"] == "TRK-1"
    assert r.json()["receiverName"] == "A. Perera"
    assert c.post("/api/parcels", json=body).status_code == 409          # duplicate tracking no.
    blank = c.post("/api/parcels", json={**body, "trackingNumber": None}).json()
    assert blank["trackingNumber"].startswith("FO-")                     # auto-generated
    bad = c.post("/api/parcels", json={**body, "trackingNumber": "T2",
                                       "dimensionsCm": {"l": 0, "w": 1, "h": 1}})
    assert bad.status_code == 400
    v = c.post("/api/vehicles", json={"name": "Van", "type": "van"}).json()
    assert v["cargoCm"] == {"l": 300, "w": 170, "h": 130} and v["maxPayloadKg"] == 1000
    d = c.post("/api/drivers", json={"name": "Dee", "vehicleId": v["id"]}).json()
    elig = c.post("/api/runs/eligibility", json={"parcelIds": [r.json()["id"]]}).json()
    assert elig[d["id"]] is None
    assert c.get("/static/runsheet.html").status_code == 200


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
