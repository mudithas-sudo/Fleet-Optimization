"""Predefined routes (route templates): CRUD and validation, the preview, and running a
route for today — recurring pickups created once a day, zone parcels gathered, one
deterministic run. No Gemini, no Google (FAKE_ROUTES).
Run: `venv/bin/python tests/test_route_templates.py` (or pytest)."""

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

from app import db, delivery, route_templates, store  # noqa: E402
from app.main import app                      # noqa: E402

HUB = {"lat": 6.93, "lng": 79.84, "label": "Colombo Hub"}
delivery.get_depot = lambda: HUB
client = TestClient(app)


def _driver(name="Route driver"):
    van = delivery.new_vehicle({"name": f"V-{name}", "type": "van"})
    return delivery.new_driver({"name": name, "vehicleId": van["id"]})


def _template(code="R-01", **over):
    body = {"code": code, "name": "Colombo Central", "zone": {"lat": 6.92, "lng": 79.86, "radiusKm": 5, "label": "Fort"},
            "days": [0, 1, 2, 3, 4, 5, 6], "startTime": "08:00",
            "pickups": [{"label": "Ceylon Print Co.", "lat": 6.93, "lng": 79.85, "name": "Print jobs", "readyBy": "23:59", "collectBy": "23:59"}]}
    body.update(over)
    return body


def _parcel(name, lat, lng):
    return delivery.new_parcel({"name": name, "size": "small", "deadline": time.time() + 6 * 3600,
                                "destination": {"lat": lat, "lng": lng, "label": f"{name} addr"}})


def test_create_validate_edit_and_delete():
    r = client.post("/api/route-templates", json=_template())
    assert r.status_code == 200, r.text
    t = r.json()
    assert t["code"] == "R-01" and t["pickups"][0]["id"], "recurring pickups get ids"
    assert client.post("/api/route-templates", json=_template()).status_code == 409, "codes are unique"
    assert client.post("/api/route-templates", json=_template("R-02", zone={"lat": 6.9, "lng": 79.8, "radiusKm": 0.1})).status_code == 400
    assert client.post("/api/route-templates", json=_template("R-02", days=[9])).status_code == 400
    assert client.post("/api/route-templates", json=_template("R-02", startTime="8am")).status_code == 400
    assert client.post("/api/route-templates", json=_template("R-02", driverId="nope")).status_code == 404
    assert client.post("/api/route-templates", json=_template(" ", name="x")).status_code == 400
    r = client.put(f"/api/route-templates/{t['id']}", json=_template(name="Colombo Fort & Pettah"))
    assert r.status_code == 200 and r.json()["name"] == "Colombo Fort & Pettah" and r.json()["createdAt"] == t["createdAt"]
    listed = client.get("/api/route-templates").json()
    assert any(x["id"] == t["id"] and "zoneParcelCount" in x for x in listed)
    assert client.delete(f"/api/route-templates/{t['id']}").status_code == 200
    assert client.get(f"/api/route-templates/{t['id']}/preview").status_code == 404


def test_preview_shows_what_will_be_included():
    t = client.post("/api/route-templates", json=_template("R-10")).json()
    inside, outside = _parcel("Inside zone", 6.925, 79.855), _parcel("Far away", 7.30, 80.60)
    pv = client.get(f"/api/route-templates/{t['id']}/preview").json()
    ids = [p["id"] for p in pv["zoneParcels"]]
    assert inside["id"] in ids and outside["id"] not in ids
    assert pv["pickups"][0]["state"] == "will be created" and pv["runsToday"] is True


def test_running_a_route_builds_one_run_with_zone_parcels_and_todays_pickups():
    t = client.post("/api/route-templates", json=_template("R-20")).json()
    a, b = _parcel("Zone A", 6.915, 79.86), _parcel("Zone B", 6.93, 79.87)
    drv = _driver("Runner")
    r = client.post(f"/api/route-templates/{t['id']}/run", json={"driverId": drv["id"]})
    assert r.status_code == 200, r.text
    trip = r.json()
    assert trip["templateCode"] == "R-20" and trip["templateName"] == "Colombo Central"
    assert {a["id"], b["id"]} <= set(trip["parcelIds"]) and len(trip["parcelIds"]) >= 3
    pickup = next(db.load_entity("parcels", i) for i in trip["parcelIds"] if db.load_entity("parcels", i).get("templateId"))
    assert pickup["requestType"] == "pickup" and pickup["templateDay"] == route_templates.day_key()
    assert pickup["destination"]["label"] == "Colombo Hub", "recurring pickups come back to the hub"
    assert store.get_trip(trip["id"])["templateCode"] == "R-20"
    summary = next(x for x in client.get("/api/trips").json() if x["id"] == trip["id"])
    assert summary["templateCode"] == "R-20"
    # the same pickup is not created twice in a day
    pv = client.get(f"/api/route-templates/{t['id']}/preview").json()
    assert pv["pickups"][0]["state"] == "on a run"
    again = client.post(f"/api/route-templates/{t['id']}/run", json={"driverId": _driver('Second')['id']})
    assert again.status_code == 409, "nothing left to run"
    assert len([p for p in db.all_entities("parcels") if p.get("templateId") == t["id"]]) == 1


def test_a_failed_run_leaves_no_orphan_pickups():
    t = client.post("/api/route-templates", json=_template("R-30")).json()
    drv = _driver("Busy")
    db.save_entity("drivers", {**drv, "onLeave": True})            # create_run will refuse this driver
    r = client.post(f"/api/route-templates/{t['id']}/run", json={"driverId": drv["id"]})
    assert r.status_code == 409, r.text
    assert not [p for p in db.all_entities("parcels") if p.get("templateId") == t["id"]]
    assert client.post(f"/api/route-templates/{t['id']}/run", json={}).status_code == 400, "a driver is needed"


def test_default_driver_is_used_and_roles():
    drv = _driver("Default one")
    t = client.post("/api/route-templates", json=_template("R-40", driverId=drv["id"])).json()
    _parcel("Default zone", 6.92, 79.86)
    r = client.post(f"/api/route-templates/{t['id']}/run", json={})
    assert r.status_code == 200 and r.json()["driverId"] == drv["id"]
    from app import auth
    assert auth.required_perm("POST", "/api/route-templates") == "routes.edit"
    assert auth.required_perm("DELETE", "/api/route-templates/x") == "routes.edit"
    assert auth.required_perm("POST", "/api/route-templates/x/run") == "runs.plan"
    assert auth.required_perm("GET", "/api/route-templates") == "runs.read"


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
