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


def test_a_route_whose_road_wanders_off_to_another_country_is_discarded():
    """Whatever the routing service returns, a path far from the stops is never drawn."""
    from app import polyline

    class _Resp:
        status_code = 200

        def json(self):
            good = [(6.93, 79.84), (6.95, 79.86)]
            bad = good + [(14.98, 75.22), (6.96, 79.87)]               # a detour through Karnataka
            return {"routes": [{"polyline": {"encodedPolyline": polyline.encode(bad)}, "distanceMeters": 1_361_000,
                                "duration": "9000s", "legs": [{"distanceMeters": 1_361_000, "duration": "9000s"}]}]}

    class _Client:
        def __init__(self, *a, **k): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def post(self, *a, **k): return _Resp()

    real_client, real_fake = routing.httpx.AsyncClient, os.environ.get("FAKE_ROUTES")
    routing.httpx.AsyncClient = _Client
    os.environ["FAKE_ROUTES"] = "0"
    os.environ["MAPS_API_KEY"] = "unused"
    try:
        stops = [dict(HUB, kind="depot"), {"lat": 6.95, "lng": 79.86, "label": "Near"}]
        try:
            asyncio.run(routing.compute_route(stops, vehicle_type="van"))
        except routing.RoutingError as e:
            assert "discarded" in str(e) and "Near" in str(e) and "14.98" in str(e)
        else:
            raise AssertionError("a route that wandered to India was accepted")
    finally:
        routing.httpx.AsyncClient = real_client
        os.environ["FAKE_ROUTES"] = real_fake or "1"


def test_a_leg_without_distance_or_duration_is_a_zero_leg_not_a_crash():
    """The Routes API drops zero-valued fields: coincident stops come back as a leg with neither."""
    from app import polyline

    class _Resp:
        status_code = 200

        def json(self):
            pts = [(6.93, 79.84), (6.95, 79.86), (6.95, 79.86)]
            return {"routes": [{"polyline": {"encodedPolyline": polyline.encode(pts)}, "distanceMeters": 3000, "duration": "600s",
                                "legs": [{"distanceMeters": 3000, "duration": "600s"}, {}]}]}

    class _Client:
        def __init__(self, *a, **k): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def post(self, *a, **k): return _Resp()

    real_client, real_fake = routing.httpx.AsyncClient, os.environ.get("FAKE_ROUTES")
    routing.httpx.AsyncClient = _Client
    os.environ["FAKE_ROUTES"] = "0"
    os.environ["MAPS_API_KEY"] = "unused"
    try:
        stops = [dict(HUB, kind="depot"), {"lat": 6.95, "lng": 79.86, "label": "A", "kind": "pickup", "parcelId": "x"},
                 {"lat": 6.95, "lng": 79.86, "label": "B", "kind": "pickup", "parcelId": "y"}]
        route = asyncio.run(routing.compute_route(stops, vehicle_type="van", presequenced=True))
        assert [l["distanceMeters"] for l in route["legs"]] == [3000, 0]
        assert route["legs"][1]["durationSeconds"] == 0
    finally:
        routing.httpx.AsyncClient = real_client
        os.environ["FAKE_ROUTES"] = real_fake or "1"


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
