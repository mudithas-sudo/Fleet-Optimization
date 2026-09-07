"""Deterministic checks for the pickup & delivery feature — no Gemini, no
Google (FAKE_ROUTES). Run: `venv/bin/python tests/test_pickup_delivery.py`
(or with pytest if it's installed — functions are named test_*)."""

import asyncio
import os
import sys
import tempfile

os.environ.setdefault("FAKE_ROUTES", "1")
os.environ.setdefault("DB_PATH", os.path.join(tempfile.mkdtemp(), "t.db"))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import routing, delivery                       # noqa: E402
from app.planner.tools import _apply_order, _stop_key   # noqa: E402

DEPOT = {"lat": 6.93, "lng": 79.84, "label": "Depot"}
delivery.get_depot = lambda: DEPOT


def _parcel(pid, name, dest, pickup=None, status="pending", deadline=9e12):
    return {"id": pid, "name": name, "size": "small", "type": "x", "status": status,
            "destination": {"lat": dest[0], "lng": dest[1], "label": name},
            "pickup": pickup, "deadline": deadline}


def test_build_run_stops_shapes():
    a = _parcel("pA", "Alpha", (6.90, 79.86),
                pickup={"lat": 6.95, "lng": 79.90, "label": "WH-A", "latest": 100})
    b = _parcel("pB", "Bravo", (6.88, 79.85))                       # depot-origin
    d = _parcel("pD", "Delta", (6.87, 79.83), status="awaiting_redelivery",
                pickup={"lat": 6.99, "lng": 79.99, "label": "old"})
    stops = delivery.build_run_stops([a, b, d])
    kinds = [s.get("kind") for s in stops]
    assert kinds[0] == "depot"
    assert kinds.count("pickup") == 1                                # only pA
    assert kinds.count("delivery") == 3
    pick = next(s for s in stops if s["kind"] == "pickup")
    assert pick["latest"] == 100
    assert next(s for s in stops if s["kind"] == "delivery")["deadline"] is not None


def test_precedence_order_and_repair():
    def s(kind, pid, xy):
        return {"lat": xy[0], "lng": xy[1], "label": f"{pid} {kind}",
                "parcelId": pid, "kind": kind}
    stops = [dict(DEPOT, kind="depot"),
             s("pickup", "pA", (6.95, 79.90)), s("delivery", "pA", (6.90, 79.86)),
             s("pickup", "pC", (6.92, 79.87)), s("delivery", "pC", (6.91, 79.88))]
    route = asyncio.run(routing.compute_route(stops, vehicle_type="van"))
    keys = [_stop_key(x) for x in route["orderedStops"][1:]]
    for pid in ("pA", "pC"):
        assert keys.index(f"{pid}:pickup") < keys.index(f"{pid}:delivery")

    broken = [s("delivery", "pA", (6.9, 79.86)), s("pickup", "pA", (6.95, 79.9)),
              s("delivery", "pB", (6.88, 79.85))]
    fixed = [_stop_key(x) for x in routing.repair_precedence(broken)]
    assert fixed.index("pA:pickup") < fixed.index("pA:delivery")
    valid = broken[::-1][1:]                                          # already ok
    assert routing.repair_precedence(valid) == valid


def test_apply_agent_order():
    def s(kind, pid, xy):
        return {"lat": xy[0], "lng": xy[1], "label": f"{pid} {kind}",
                "parcelId": pid, "kind": kind}
    stops = [dict(DEPOT, kind="depot"),
             s("pickup", "pA", (6.95, 79.9)), s("delivery", "pA", (6.9, 79.86)),
             s("pickup", "pC", (6.92, 79.87)), s("delivery", "pC", (6.91, 79.88)),
             s("delivery", "pB", (6.88, 79.85))]
    # model omits pA:delivery and pB:delivery, includes a bogus key
    ordered = _apply_order(stops, ["pC:pickup", "pC:delivery", "pA:pickup", "bogus:x"])
    keys = {_stop_key(x) for x in ordered[1:]}
    assert keys == {"pA:pickup", "pA:delivery", "pC:pickup", "pC:delivery", "pB:delivery"}
    seq = [_stop_key(x) for x in ordered[1:]]
    assert seq.index("pA:pickup") < seq.index("pA:delivery")


def test_planned_etas_flag_window_and_deadline():
    def s(kind, pid, xy, **e):
        return {"lat": xy[0], "lng": xy[1], "label": pid, "parcelId": pid, "kind": kind, **e}
    route = asyncio.run(routing.compute_route(
        [dict(DEPOT, kind="depot"),
         s("pickup", "pA", (6.95, 79.9)), s("delivery", "pA", (6.9, 79.86))],
        presequenced=True, vehicle_type="van"))
    pa = _parcel("pA", "Alpha", (6.9, 79.86),
                 pickup={"lat": 6.95, "lng": 79.9, "label": "WH", "latest": 1})
    etas = delivery.compute_planned_etas(route, {"pA": pa}, departure_ts=0.0)
    pick = next(e for e in etas if e["kind"] == "pickup")
    assert pick["atRisk"] is True                                    # latest=1s ago
    assert any(e["kind"] == "delivery" for e in etas)


def test_coincident_pickup_announcement_collapses():
    WH = (6.96, 79.90)
    stops = [dict(DEPOT, kind="depot"),
             {"lat": WH[0], "lng": WH[1], "label": "WH", "parcelId": "pA", "kind": "pickup"},
             {"lat": WH[0], "lng": WH[1], "label": "WH", "parcelId": "pB", "kind": "pickup"},
             {"lat": 6.9, "lng": 79.86, "label": "pA", "parcelId": "pA", "kind": "delivery"},
             {"lat": 6.88, "lng": 79.85, "label": "pB", "parcelId": "pB", "kind": "delivery"}]
    route = asyncio.run(routing.compute_route(stops, presequenced=True, vehicle_type="van"))
    arrives = [x["instruction"] for x in route["steps"] if x["maneuver"] == "ARRIVE"]
    assert arrives.count("Collect at WH") == 1


def test_runtime_progress_and_abort():
    from app import db, store
    import app.main as main
    import app.planner.service as svc

    async def fake_plan(stops, avoid_tolls=False, vehicle_type="car"):
        return await routing.compute_route(stops, vehicle_type=vehicle_type)
    svc.plan_route = fake_plan

    async def run():
        db.set_setting("depot", DEPOT)
        veh = delivery.new_vehicle({"name": "Van", "type": "van"})
        drv = delivery.new_driver({"name": "Sam", "vehicleId": veh["id"]})
        p1 = delivery.new_parcel({"name": "NoPickup", "size": "small", "deadline": 9e12,
                                  "destination": {"lat": 6.9, "lng": 79.86, "label": "d1"}})
        p2 = delivery.new_parcel({"name": "WithPickup", "size": "small", "deadline": 9e12,
                                  "destination": {"lat": 6.88, "lng": 79.88, "label": "d2"},
                                  "pickup": {"lat": 6.95, "lng": 79.9, "label": "WH"}})
        trip = await delivery.create_run([p1["id"], p2["id"]], drv["id"])
        trip["status"] = "active"
        for pid in trip["parcelIds"]:
            delivery.set_parcel_status(db.load_entity("parcels", pid), "in_transit",
                                      trip_id=trip["id"])
        rt = store.runtime(trip["id"])
        bounds, cum = [0.0], 0.0
        for leg in trip["route"]["legs"]:
            cum += leg["distanceMeters"]; bounds.append(cum)
        for i in range(1, len(trip["route"]["orderedStops"])):
            rt["along"] = bounds[i] + 400
            trip["lastPosition"] = dict(trip["route"]["orderedStops"][i])
            main._check_stop_progress(trip, rt)
        assert db.load_entity("parcels", p1["id"])["status"] == "delivered"
        assert db.load_entity("parcels", p2["id"])["status"] == "delivered"
        assert trip["collectedCount"] == 1 and trip["deliveredCount"] == 2

        # abort a run with a collected parcel -> awaiting_redelivery, no pickup on re-plan
        p3 = delivery.new_parcel({"name": "Abortee", "size": "small", "deadline": 9e12,
                                  "destination": {"lat": 6.87, "lng": 79.83, "label": "d3"},
                                  "pickup": {"lat": 6.99, "lng": 79.99, "label": "WH3"}})
        t2 = await delivery.create_run([p3["id"]], drv["id"])
        delivery.set_parcel_status(db.load_entity("parcels", p3["id"]), "picked_up",
                                  trip_id=t2["id"])
        store.save_trip(t2)
        from fastapi.testclient import TestClient
        TestClient(main.app).post(f"/api/trips/{t2['id']}/end", json={"arrived": False})
        assert db.load_entity("parcels", p3["id"])["status"] == "awaiting_redelivery"
        t3 = await delivery.create_run([p3["id"]], drv["id"])
        assert "pickup" not in [s.get("kind") for s in t3["route"]["orderedStops"]]

    asyncio.run(run())


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = 0
    for t in tests:
        try:
            t()
            print(f"  PASS  {t.__name__}")
        except Exception as e:
            failed += 1
            print(f"  FAIL  {t.__name__}: {e!r}")
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    sys.exit(1 if failed else 0)
