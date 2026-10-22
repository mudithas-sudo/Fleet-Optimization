"""A run never reaches across countries: a stop (or a bad GPS fix) hundreds of km from the start is
refused with a clear message instead of drawing a 1,300 km route. No Gemini, no Google (FAKE_ROUTES).
Run: `venv/bin/python tests/test_route_sanity.py` (or pytest)."""

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

HUB = {"lat": 6.93, "lng": 79.84, "label": "Colombo Hub"}
delivery.get_depot = lambda: HUB
client = TestClient(app)


def _parcel(name, lat, lng):
    return delivery.new_parcel({"name": name, "size": "small", "deadline": time.time() + 6 * 3600,
                                "destination": {"lat": lat, "lng": lng, "label": f"{name} addr"}})


def _driver():
    van = delivery.new_vehicle({"name": "Van", "type": "van"})
    return delivery.new_driver({"name": "Sam", "vehicleId": van["id"]})


def test_a_far_away_stop_is_refused_by_the_router():
    stops = [dict(HUB, kind="depot"), {"lat": 6.95, "lng": 79.86, "label": "Near"}, {"lat": 14.98, "lng": 75.22, "label": "Karnataka"}]
    try:
        asyncio.run(routing.compute_route(stops, vehicle_type="van"))
    except routing.RoutingError as e:
        assert "Karnataka" in str(e) and "km" in str(e)
    else:
        raise AssertionError("a route to another country was accepted")
    ok = asyncio.run(routing.compute_route(stops[:2], vehicle_type="van"))
    assert ok["totalDistanceMeters"] > 0


def test_planning_a_run_with_a_stray_address_is_a_clear_error_not_a_crash():
    far = _parcel("Mis-geocoded", 14.98, 75.22)
    drv = _driver()
    r = client.post("/api/runs", json={"parcelIds": [far["id"]], "driverId": drv["id"]})
    assert r.status_code == 422 and "km" in r.json()["detail"], r.text
    assert db.load_entity("parcels", far["id"])["status"] == "pending", "the parcel stays pending"


def test_a_bad_gps_fix_does_not_send_a_live_replan_across_the_sea():
    a, b = _parcel("Live A", 6.95, 79.86), _parcel("Live B", 6.97, 79.88)
    drv = _driver()
    trip = asyncio.run(delivery.create_run([a["id"], b["id"]], drv["id"], deterministic=True))
    trip["status"] = "active"
    trip["lastPosition"] = {"lat": 14.98, "lng": 75.22, "ts": time.time()}      # a GPS fix in India
    store.save_trip(trip)
    c = _parcel("Late add", 6.96, 79.87)
    out = asyncio.run(replan.replan(trip["id"], add_ids=[c["id"]]))
    first = out["route"]["orderedStops"][0]
    assert routing._haversine_m(first, HUB) < 1000, "re-planned from the hub, not from India"
    assert out["route"]["totalDistanceMeters"] < 100_000


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
