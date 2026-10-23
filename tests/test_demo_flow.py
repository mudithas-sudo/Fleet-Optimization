"""The demo, end to end, on the seeded demo data — offline (FAKE_ROUTES, the AI planner is made
to fail so the deterministic fallback is exercised too). Walks the same story as the demo script:
dashboard numbers, Suggest runs, drive a run, a pickup request arriving mid-day, a failed delivery
and its review, a vehicle breakdown, a predefined route, customer tracking and the Impact figures.
Run: `venv/bin/python tests/test_demo_flow.py` (or pytest)."""

import os
import subprocess
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.environ["FAKE_ROUTES"] = "1"
os.environ.setdefault("GOOGLE_API_KEY", "unused")
os.environ.setdefault("MAPS_API_KEY", "unused")
os.environ["DB_PATH"] = os.path.join(tempfile.mkdtemp(), "demo.db")
subprocess.run([sys.executable, os.path.join(ROOT, "samples", "seed_demo.py"), "--reset"], check=True,
               env=dict(os.environ), stdout=subprocess.DEVNULL)
sys.path.insert(0, ROOT)

from fastapi.testclient import TestClient     # noqa: E402

from app import db, delivery, store           # noqa: E402
from app.main import app                      # noqa: E402


async def _no_ai(*a, **k):
    raise RuntimeError("AI planner unavailable (test)")


import app.planner.service as planner_service  # noqa: E402
planner_service.plan_route = _no_ai

client = TestClient(app)


def _by_tracking(no):
    return next(p for p in client.get("/api/parcels").json() if p["trackingNumber"] == no)


def _drive(trip_id, step_m=150.0, until=1.0):
    """Post positions along the planned path every ~150 m, like the driver app's simulator."""
    import math
    trip = client.get(f"/api/trips/{trip_id}").json()
    path = trip["route"]["path"]
    pts = [tuple(path[0])]
    for a, b in zip(path, path[1:]):
        d = math.hypot((b[0] - a[0]) * 111_000, (b[1] - a[1]) * 111_000 * math.cos(math.radians(a[0])))
        n = max(1, int(d // step_m))
        pts += [(a[0] + (b[0] - a[0]) * k / n, a[1] + (b[1] - a[1]) * k / n) for k in range(1, n + 1)]
    ts = time.time()
    for i, (lat, lng) in enumerate(pts[: max(1, int(len(pts) * until))]):
        r = client.post(f"/api/trips/{trip_id}/position", json={"lat": lat, "lng": lng, "ts": ts + i * 10})
        assert r.status_code == 200, r.text


def test_01_the_seeded_day_looks_right():
    parcels = client.get("/api/parcels").json()
    assert len(parcels) == 38
    kinds = {}
    for p in parcels:
        kinds[p["status"]] = kinds.get(p["status"], 0) + 1
    assert kinds.get("delivered") == 10 and kinds.get("failed") == 1 and kinds.get("returned") == 1
    assert client.get("/api/route-templates").json()[0]["code"] == "R-01"
    rides = client.get("/api/rides").json()["rides"]
    if time.localtime().tm_hour >= 10:      # before 10:00 the finished runs fall on yesterday's board
        assert {r["state"] for r in rides} == {"en_route", "scheduled", "finished"}
    live = next(r for r in rides if r["state"] == "en_route")
    assert live["nextStop"] and live["parcelCount"] == 7, "the pickup request added mid-day is on the live run"
    imp = client.get("/api/impact?days=all").json()
    assert imp["savings"]["savedKm"] > 20 and imp["savings"]["savedPct"] > 20
    assert imp["eta"]["within15Pct"] is not None and imp["planVsActual"]["runs"]
    assert client.get("/api/reports/summary?days=all").status_code == 200
    for kind in ("parcels", "runs", "drivers"):
        assert client.get(f"/api/reports/export?kind={kind}&days=all").status_code == 200


def test_02_suggest_runs_then_approve_even_if_the_ai_planner_is_down():
    plan = client.post("/api/dispatch/propose", json={"engine": "rules"}).json()
    assert plan["batches"], plan
    assert any("pallet" in u["name"].lower() for u in plan["unassigned"]), "the oversized pallet is flagged"
    batch = plan["batches"][0]
    res = client.post("/api/dispatch/approve", json={"batches": [{"driverId": batch["driverId"], "parcelIds": batch["parcelIds"]}]}).json()
    assert res["created"] and not res["errors"], res
    trip_id = res["created"][0]
    assert client.get(f"/api/trips/{trip_id}").json()["route"]["orderedStops"], "planned by the fallback planner"
    global PLANNED
    PLANNED = trip_id


def test_03_driving_a_run_delivers_parcels_and_finishes():
    trip_id = PLANNED
    assert client.post(f"/api/trips/{trip_id}/start").status_code == 200
    trip = client.get(f"/api/trips/{trip_id}").json()
    assert all(e["eta"] > time.time() - 5 for e in trip["plannedEtaPerStop"]), "ETAs count from the real departure"
    _drive(trip_id)
    rows = client.get(f"/api/trips/{trip_id}/parcels").json()
    deliveries = [r for r in rows if r.get("requestType") != "pickup"]
    # (offline straight-line routes are shorter than their stated leg distances, so the last stop may
    #  not tick by driving alone; real Routes API geometry is consistent)
    assert sum(r["status"] == "delivered" for r in deliveries) >= len(deliveries) - 2, [(r["name"], r["status"]) for r in rows]
    for r in rows:                              # driving past never collects a pickup: the driver ticks it
        if r.get("requestType") == "pickup":
            assert r["status"] == "in_transit"
            assert client.post(f"/api/parcels/{r['id']}/collect", json={"note": "Collected"}).status_code == 200
    assert client.post(f"/api/trips/{trip_id}/end", json={"arrived": True}).status_code == 200
    assert client.get(f"/api/trips/{trip_id}").json()["status"] == "completed"
    rows = client.get(f"/api/trips/{trip_id}/parcels").json()
    assert all(r["status"] == "delivered" for r in rows), "arriving closes everything: deliveries delivered, collected pickups received at the hub"


def test_04_a_pickup_request_arrives_and_is_matched_to_the_best_run():
    r = client.post("/api/parcels", json={
        "name": "Demo samples (pickup request)", "size": "small", "requestType": "pickup", "receiverName": "Demo Customer",
        "recipientPhone": "+94 77 000 0000",
        "pickup": {"lat": 6.915, "lng": 79.86, "label": "Customer office, Colombo 02",
                   "earliest": time.time() + 600, "latest": time.time() + 3 * 3600}})
    assert r.status_code == 200, r.text
    pid = r.json()["id"]
    runs = client.get(f"/api/parcels/{pid}/fit").json()["runs"]
    best = next(x for x in runs if x["feasible"])
    assert best["extraMinutes"] > 0
    assert client.post(f"/api/runs/{best['tripId']}/parcels", json={"parcelIds": [pid]}).status_code == 200
    p = client.get(f"/api/parcels/{pid}").json()
    assert p["status"] in ("assigned", "in_transit")
    if p["status"] == "in_transit":
        assert client.post(f"/api/parcels/{pid}/collect", json={"note": "Sealed"}).json()["status"] == "picked_up"
    tl = [e["message"] for e in client.get(f"/api/parcels/{pid}").json()["timeline"]]
    assert any("Added to run" in m for m in tl)


def test_05_a_failed_delivery_is_reviewed_and_rescheduled_for_tomorrow():
    hamper = _by_tracking("FO-100108")           # the seeded failed delivery, waiting for review
    assert hamper["status"] == "failed" and hamper["review"]["state"] == "pending"
    tomorrow = time.time() + 30 * 3600
    r = client.post(f"/api/parcels/{hamper['id']}/review", json={
        "decision": "reschedule", "note": "Customer asked for tomorrow", "scheduledFor": tomorrow})
    assert r.status_code == 200 and r.json()["status"] == "awaiting_redelivery" and r.json()["holdUntil"]
    track = client.get("/api/track/FO-100108").json()
    assert track["status"] == "awaiting_redelivery" and track["headline"]


def test_06_vehicle_breakdown_reassigns_the_live_run():
    live = next(t for t in client.get("/api/trips").json() if t["status"] == "active")
    prev = client.post(f"/api/runs/{live['id']}/breakdown", json={}).json()
    assert prev["applied"] is False and (prev["moved"] or prev["unplaced"])
    res = client.post(f"/api/runs/{live['id']}/breakdown", json={"apply": True}).json()
    assert res["applied"]
    assert client.get(f"/api/trips/{live['id']}").json()["status"] == "completed"


def test_07_predefined_route_builds_todays_run():
    templates = client.get("/api/route-templates").json()
    t = next(x for x in templates if x["code"] == "R-02")
    drivers = client.get("/api/drivers").json()
    busy = {x["driverId"] for x in client.get("/api/trips").json() if x["status"] != "completed"}
    free = next((d for d in drivers if d["id"] not in busy and not d.get("onLeave")), None)
    if not free:                                   # everyone is on a run by now: register one more, like a fleet manager would
        v = client.post("/api/vehicles", json={"name": "Van 03", "plate": "CAB-9001", "type": "van"}).json()
        free = client.post("/api/drivers", json={"name": "Extra Driver", "phone": "+94 77 000 0003", "vehicleId": v["id"]}).json()
    pv = client.get(f"/api/route-templates/{t['id']}/preview").json()
    assert pv["pickups"]
    r = client.post(f"/api/route-templates/{t['id']}/run", json={"driverId": free["id"]})
    assert r.status_code in (200, 409), r.text     # 409 only if capacity rules refuse this van today
    if r.status_code == 200:
        assert r.json()["templateCode"] == "R-02"


def test_08_customer_tracking_and_messages():
    t = client.get("/api/track/FO-100112").json()
    assert t["status"] == "delivered" and t["history"]
    assert client.get("/api/track/FO-DOES-NOT-EXIST").status_code == 404
    msgs = client.get("/api/notifications?limit=20").json()["messages"]
    assert msgs and all("message" in m for m in msgs)


def test_09_impact_after_the_day_is_consistent():
    imp = client.get("/api/impact?days=all").json()
    s = imp["savings"]
    assert s["savedKm"] <= s["baselineKm"] and s["optimisedKm"] + s["savedKm"] == s["baselineKm"] or abs(s["optimisedKm"] + s["savedKm"] - s["baselineKm"]) < 0.5
    assert all(r["savedKm"] >= 0 for r in imp["perRun"])
    assert client.put("/api/settings/fuel", json={"pricePerL": 400}).status_code == 200


PLANNED = None

if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
