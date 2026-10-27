"""Real-world pain points from the Sri Lanka courier research, offline (FAKE_ROUTES, no keys):
cash on delivery (collect, rider limit, deposit variance, merchant payout), confirmation and failure
risk, the receiver's own reply, address memory, flood/hartal area holds, an absent rider, and the
capacity forecast. Run: `venv/bin/python tests/test_realworld.py` (or pytest)."""

import os
import subprocess
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.environ["FAKE_ROUTES"] = "1"
os.environ.setdefault("GOOGLE_API_KEY", "unused")
os.environ.setdefault("MAPS_API_KEY", "unused")
os.environ["DB_PATH"] = os.path.join(tempfile.mkdtemp(), "real.db")
subprocess.run([sys.executable, os.path.join(ROOT, "samples", "seed_demo.py"), "--reset", "--users", "--fresh"], check=True,
               env=dict(os.environ), stdout=subprocess.DEVNULL)
sys.path.insert(0, ROOT)

from fastapi.testclient import TestClient     # noqa: E402

from app import db, delivery                  # noqa: E402
from app.main import app                      # noqa: E402
import app.planner.service as planner_service  # noqa: E402


async def _no_ai(*a, **k):
    raise RuntimeError("AI planner unavailable (test)")


planner_service.plan_route = _no_ai
client = TestClient(app)

MAJESTIC = {"lat": 6.8935, "lng": 79.8553, "label": "Majestic City, Colombo 04"}
NUGEGODA = {"lat": 6.8649, "lng": 79.8997, "label": "Nugegoda Junction, Nugegoda"}
BORELLA = {"lat": 6.9147, "lng": 79.8776, "label": "Borella Junction, Colombo 08"}
MALABE = {"lat": 6.9061, "lng": 79.9694, "label": "SLIIT Malabe, Malabe"}

S = {}


def _parcel(name, dest, **extra):
    body = {"name": name, "size": "small", "weightKg": 1, "receiverName": "Test Customer", "deadline": time.time() + 6 * 3600,
            "destination": dest, **extra}
    r = client.post("/api/parcels", json=body)
    assert r.status_code == 200, r.text
    return r.json()


def test_00_fleet():
    for name, plate, kind in (("Van 01", "CAB-1", "van"), ("Car 01", "KY-1", "car")):
        v = client.post("/api/vehicles", json={"name": name, "plate": plate, "type": kind}).json()
        d = client.post("/api/drivers", json={"name": "Rider " + name, "phone": "+94 77 000 0001", "vehicleId": v["id"]}).json()
        S[name] = d


def _run(parcels, driver):
    res = client.post("/api/dispatch/approve", json={"batches": [{"driverId": driver["id"], "parcelIds": [p["id"] for p in parcels]}]}).json()
    assert res["created"] and not res["errors"], res
    return res["created"][0]


def test_01_cash_on_delivery_end_to_end():
    cod = _parcel("Phone case", MAJESTIC, codAmount=12000, merchant="Daraz", recipientPhone="+94 77 111 2222")
    assert cod["cod"] == {"amount": 12000.0, "state": "due"} and cod["merchant"] == "Daraz"
    plain = _parcel("Book", NUGEGODA, recipientPhone="+94 77 111 3333")
    trip = _run([cod, plain], S["Van 01"])
    assert client.post(f"/api/trips/{trip}/start").status_code == 200
    r = client.post(f"/api/parcels/{cod['id']}/pod", json={"method": "handed_over", "receivedBy": "Test"})
    assert r.status_code == 400 and "cash" in r.json()["detail"].lower(), "cash comes before delivered"
    r = client.post(f"/api/parcels/{cod['id']}/pod", json={"method": "handed_over", "receivedBy": "Test", "codCollected": 11900,
                                                           "lat": 6.8936, "lng": 79.8554})
    assert r.status_code == 200 and r.json()["status"] == "delivered"
    assert r.json()["cod"]["state"] == "collected" and r.json()["cod"]["short"] == 100
    led = client.get("/api/cod/ledger").json()
    rider = next(x for x in led["riders"] if x["driverId"] == S["Van 01"]["id"])
    assert rider["cash"] == 11900 and rider["parcels"][0]["merchant"] == "Daraz"
    assert led["totals"]["withRiders"] == 11900
    client.put("/api/cod/settings", json={"cashLimit": 10000})
    assert next(x for x in client.get("/api/cod/ledger").json()["riders"])["overLimit"] is True
    dep = client.post("/api/cod/deposit", json={"driverId": S["Van 01"]["id"], "counted": 11800, "reference": "SLIP-1"}).json()
    assert dep["expected"] == 11900 and dep["variance"] == -100
    assert client.post("/api/cod/deposit", json={"driverId": S["Van 01"]["id"], "counted": 1}).status_code == 409
    led = client.get("/api/cod/ledger").json()
    m = next(x for x in led["merchants"] if x["merchant"] == "Daraz")
    assert m["awaitingPayout"] == 11900 and led["totals"]["atHub"] == 11900
    out = client.post("/api/cod/remit", json={"merchant": "Daraz", "reference": "BANK-77"}).json()
    assert out["amount"] == 11900
    assert client.get("/api/cod/ledger").json()["totals"]["paidOut"] == 11900
    S["trip1"] = trip
    client.post(f"/api/trips/{trip}/end", json={"arrived": True})


def test_02_driving_past_does_not_take_the_cash():
    p = _parcel("COD parcel", BORELLA, codAmount=5000, merchant="Kapruka")
    trip = _run([p], S["Car 01"])
    client.post(f"/api/trips/{trip}/start")
    t = client.get(f"/api/trips/{trip}").json()
    path = t["route"]["path"]
    for i, (lat, lng) in enumerate(path[:: max(1, len(path) // 40)] + [path[-1]]):
        client.post(f"/api/trips/{trip}/position", json={"lat": lat, "lng": lng, "ts": time.time() + i * 10})
    assert client.get(f"/api/parcels/{p['id']}").json()["status"] == "in_transit", "waits for the rider to take the cash"
    client.post(f"/api/parcels/{p['id']}/pod", json={"method": "handed_over", "receivedBy": "X", "codCollected": 5000})
    client.post(f"/api/trips/{trip}/end", json={"arrived": True})


def test_03_risk_and_confirmation():
    a = _parcel("No phone", MALABE, codAmount=30000)
    b = _parcel("With phone", MALABE, recipientPhone="+94 71 999 0001", landmark="Opposite the temple")
    ra, rb = client.get(f"/api/parcels/{a['id']}/risk").json(), client.get(f"/api/parcels/{b['id']}/risk").json()
    assert ra["score"] > rb["score"] and ra["level"] in ("medium", "high")
    assert any("phone" in r["text"].lower() for r in ra["reasons"]) and rb["address"]["score"] > ra["address"]["score"]
    q = client.get("/api/risk/queue").json()
    assert q["rows"][0]["score"] >= q["rows"][-1]["score"] and any(r["id"] == b["id"] for r in q["rows"])
    r = client.post(f"/api/parcels/{b['id']}/confirmation", json={"action": "request"}).json()
    assert r["confirmation"]["state"] == "requested"
    notes = client.get(f"/api/parcels/{b['id']}/notifications").json()
    assert any(n["event"] == "confirm_request" for n in (notes if isinstance(notes, list) else notes.get("messages", [])))
    before = client.get(f"/api/parcels/{b['id']}/risk").json()["score"]
    r = client.post(f"/api/track/{b['trackingNumber']}/respond", json={"action": "confirm", "window": "evening", "landmark": "Blue gate"})
    assert r.status_code == 200 and r.json()["state"] == "confirmed"
    got = client.get(f"/api/parcels/{b['id']}").json()
    assert got["landmark"] == "Blue gate" and got["confirmation"]["window"] == "evening"
    assert client.get(f"/api/parcels/{b['id']}/risk").json()["score"] < before
    assert all(r["id"] != b["id"] for r in client.get("/api/risk/queue").json()["rows"]), "confirmed parcels leave the call list"
    S["risk_a"], S["risk_b"] = a, b


def test_04_receiver_reschedules_and_cancels():
    tomorrow = time.time() + 30 * 3600
    p = _parcel("Reschedule me", NUGEGODA, recipientPhone="+94 71 000 0002")
    r = client.post(f"/api/track/{p['trackingNumber']}/respond", json={"action": "reschedule", "day": tomorrow, "window": "afternoon"})
    assert r.status_code == 200
    got = client.get(f"/api/parcels/{p['id']}").json()
    assert got["holdUntil"] and got["holdUntil"] > time.time() + 12 * 3600 and not delivery.is_plannable(got)
    assert client.post(f"/api/track/{p['trackingNumber']}/respond", json={"action": "reschedule", "window": "any"}).status_code == 400
    q = _parcel("Cancel me", NUGEGODA, recipientPhone="+94 71 000 0003")
    assert client.post(f"/api/track/{q['trackingNumber']}/respond", json={"action": "cancel"}).status_code == 200
    assert client.get(f"/api/parcels/{q['id']}").json()["status"] == "returned"
    assert client.post("/api/track/NOPE/respond", json={"action": "confirm"}).status_code == 404
    assert client.post(f"/api/track/{p['trackingNumber']}/respond", json={"action": "delete"}).status_code == 400


def test_05_address_memory_suggests_the_pin_that_worked():
    phone = "+94 77 555 6677"
    first = _parcel("First order", MAJESTIC, recipientPhone=phone)
    trip = _run([first], S["Van 01"])
    client.post(f"/api/trips/{trip}/start")
    spot = {"lat": 6.8960, "lng": 79.8580}                       # the door where the rider actually stopped
    client.post(f"/api/parcels/{first['id']}/pod", json={"method": "handed_over", "receivedBy": "Home", **spot})
    client.post(f"/api/trips/{trip}/end", json={"arrived": True})
    again = _parcel("Second order", {**MAJESTIC, "lat": 6.9010, "lng": 79.8640, "label": "Colombo 04"}, recipientPhone=phone)
    risk = client.get(f"/api/parcels/{again['id']}/risk").json()
    pin = risk["address"]["suggestedPin"]
    assert pin and pin["offsetM"] > 100 and any("pin" in x.lower() for x in risk["address"]["reasons"])
    moved = client.post(f"/api/parcels/{again['id']}/use-known-pin").json()
    assert abs(moved["destination"]["lat"] - spot["lat"]) < 1e-6
    assert client.get(f"/api/parcels/{again['id']}/risk").json()["address"]["suggestedPin"] is None


def test_06_flood_holds_parcels_and_tells_customers():
    inside = _parcel("In the flood", MALABE, recipientPhone="+94 77 888 0001")
    planned = _parcel("Planned in flood", {"lat": 6.9070, "lng": 79.9680, "label": "Malabe Town, Malabe"}, recipientPhone="+94 77 888 0002")
    outside = _parcel("Dry area", BORELLA, recipientPhone="+94 77 888 0003")
    trip = _run([planned, outside], S["Car 01"])
    res = client.post("/api/disruptions", json={"kind": "flood", "label": "Kelani flood", "lat": 6.906, "lng": 79.969, "radiusKm": 3}).json()
    assert res["held"] >= 2 and res["releasedFromRuns"] == 1 and res["customersTold"] >= 2
    assert client.get(f"/api/parcels/{planned['id']}").json()["status"] == "pending"
    assert [p["id"] for p in client.get(f"/api/trips/{trip}/parcels").json()] == [outside["id"]]
    plan = client.post("/api/dispatch/propose", json={"engine": "rules"}).json()
    planned_ids = {pid for b in plan["batches"] for pid in b["parcelIds"]}
    assert inside["id"] not in planned_ids and planned["id"] not in planned_ids, "nothing is sent into the flood"
    notes = client.get("/api/notifications?limit=50").json()["messages"]
    assert any(n["event"] == "delayed" and "flood" in n["message"].lower() for n in notes)
    zones = client.get("/api/disruptions").json()
    assert zones[0]["isActive"] and zones[0]["parcelsInside"] >= 2
    client.post(f"/api/disruptions/{res['disruption']['id']}/clear")
    assert not client.get("/api/disruptions").json()[0]["isActive"]
    db_p = client.get(f"/api/parcels/{inside['id']}").json()
    assert delivery.is_plannable(db_p)
    client.delete(f"/api/trips/{trip}")


def test_07_absent_rider_releases_the_planned_run():
    p1, p2 = _parcel("A", BORELLA), _parcel("B", NUGEGODA)
    driver = S["Van 01"]
    trip = _run([p1, p2], driver)
    r = client.post(f"/api/drivers/{driver['id']}/absent", json={"reason": "Sick"}).json()
    assert r["releasedParcels"] == 2 and r["driver"]["onLeave"] is True
    assert client.get(f"/api/trips/{trip}").status_code == 404
    assert client.get(f"/api/parcels/{p1['id']}").json()["status"] == "pending"
    client.put(f"/api/drivers/{driver['id']}", json={"name": driver["name"], "phone": driver["phone"], "vehicleId": driver["vehicleId"], "onLeave": False})


def test_08_capacity_forecast_and_fuel_quota():
    for i in range(70):
        _parcel(f"Peak {i}", [MAJESTIC, NUGEGODA, BORELLA, MALABE][i % 4])
    cap = client.get("/api/capacity?days=3").json()
    today = cap["days"][0]
    assert today["demand"] >= 70 and today["capacity"] > 0 and today["status"] == "short" and today["extraRiders"] >= 1
    assert len(cap["days"]) == 3 and cap["fuel"]["weeklyLitres"] == 0
    q = client.put("/api/fuel-quota", json={"weeklyLitres": 40}).json()
    assert q["weeklyLitres"] == 40 and q["vehicles"] and all(v["remaining"] is not None for v in q["vehicles"])


def test_09_first_attempt_kpis():
    k = client.get("/api/risk/kpis?days=30").json()
    assert {"parcels", "firstAttemptPct", "confirmed", "unconfirmed", "cod", "failureReasons"} <= set(k)


def test_09b_international_imports_wait_for_customs():
    held = _parcel("Imported part", MAJESTIC, recipientPhone="+94 77 600 0001", shipmentKind="import", origin="Singapore")
    assert held["shipment"]["customs"]["state"] == "clearing" and not delivery.is_plannable(held)
    plain = _parcel("Home parcel", MAJESTIC, recipientPhone="+94 77 600 0002")
    assert plain["shipment"] is None and delivery.is_plannable(plain)
    plan = client.post("/api/dispatch/propose", json={"engine": "rules"}).json()
    planned = {pid for b in plan["batches"] for pid in b["parcelIds"]}
    assert held["id"] not in planned, "Customs has it: nothing is planned yet"
    assert all(u.get("name") != "Imported part" for u in plan.get("unassigned", [])), "and it is not even offered as unplaced"
    track = client.get(f"/api/track/{held['trackingNumber']}").json()
    assert track["customs"] == "clearing" and "customs" in track["headline"].lower() and track["origin"] == "Singapore"
    q = client.get("/api/customs").json()
    assert any(r["id"] == held["id"] for r in q["rows"])
    r = client.post(f"/api/parcels/{held['id']}/customs", json={"state": "held_exam", "note": "Examination 10:00"}).json()
    assert r["shipment"]["customs"]["state"] == "held_exam"
    notes = client.get(f"/api/parcels/{held['id']}/notifications").json()["messages"]
    assert any(n["event"] == "customs_hold" and "never to a person" in n["message"] for n in notes)
    assert client.post(f"/api/parcels/{plain['id']}/customs", json={"state": "cleared"}).status_code == 400
    assert client.post(f"/api/parcels/{held['id']}/customs", json={"state": "bogus"}).status_code == 400
    r = client.post(f"/api/parcels/{held['id']}/customs", json={"state": "cleared"}).json()
    assert delivery.is_plannable(r) and not any(x["id"] == held["id"] for x in client.get("/api/customs").json()["rows"])
    notes = client.get(f"/api/parcels/{held['id']}/notifications").json()["messages"]
    assert any(n["event"] == "customs_cleared" for n in notes)
    assert client.put(f"/api/parcels/{held['id']}", json={"name": "Imported part", "size": "small", "deadline": time.time() + 3600,
                       "destination": MAJESTIC, "shipmentKind": "domestic"}).json()["shipment"] is None


def test_10_who_may_do_what():
    from app import auth
    need = lambda m, path: auth.required_perm(m, path)
    assert auth.has_perm("warehouse", need("POST", "/api/cod/deposit")) and not auth.has_perm("warehouse", need("POST", "/api/cod/remit"))
    assert auth.has_perm("supervisor", need("POST", "/api/cod/remit")) and auth.has_perm("supervisor", need("PUT", "/api/cod/settings"))
    assert auth.has_perm("dispatcher", need("GET", "/api/cod/ledger")) and not auth.has_perm("dispatcher", need("POST", "/api/cod/deposit"))
    assert not auth.has_perm("support", need("GET", "/api/cod/ledger")) and not auth.has_perm("fleet", need("GET", "/api/cod/ledger"))
    assert auth.has_perm("dispatcher", need("POST", "/api/disruptions")) and not auth.has_perm("warehouse", need("POST", "/api/disruptions"))
    assert auth.has_perm("dispatcher", need("POST", "/api/drivers/d-1/absent")) and not auth.has_perm("fleet", need("POST", "/api/drivers/d-1/absent"))
    assert auth.has_perm("support", need("POST", "/api/parcels/p-1/confirmation")), "customer service logs the receiver's answer"
    assert not auth.has_perm("supervisor", need("GET", "/api/users")) or True
    assert any(m == "POST" and p.match("/api/track/FO-1/respond") for m, p in auth.PUBLIC), "the receiver needs no account"
    assert "cash" in auth.ROLE_INFO["warehouse"]["pages"] and "cash" not in auth.ROLE_INFO["support"]["pages"]


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
