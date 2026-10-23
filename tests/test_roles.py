"""Who may do what: every role (admin, supervisor, dispatcher, customer service, warehouse, fleet
manager, analyst, viewer) against the API with sign-in on, plus the warehouse loading bay and
receiving desk. No Gemini, no Google (FAKE_ROUTES).
Run: `venv/bin/python tests/test_roles.py` (or pytest)."""

import asyncio
import os
import sys
import tempfile
import time

os.environ.setdefault("FAKE_ROUTES", "1")
os.environ.setdefault("GOOGLE_API_KEY", "unused")
os.environ.setdefault("MAPS_API_KEY", "unused")
os.environ["AUTH_ENABLED"] = "1"
os.environ["DB_PATH"] = os.path.join(tempfile.mkdtemp(), "t.db")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi.testclient import TestClient     # noqa: E402

from app import auth, db, delivery, main, route_templates, routing, store  # noqa: E402

HUB = {"lat": 6.93, "lng": 79.84, "label": "Colombo Hub"}
delivery.get_depot = lambda: HUB
PW = "correct-horse-9"
ROLES = list(auth.ROLES)

for r in ROLES:
    auth.create_user(f"{r}1", r.title(), r, PW)


def _as(role) -> TestClient:
    c = TestClient(main.app)
    r = c.post("/api/auth/login", json={"username": f"{role}1", "password": PW})
    assert r.status_code == 200, r.text
    return c


C = {r: _as(r) for r in ROLES}


def _parcel(name, lat=6.95, lng=79.86, **over):
    return delivery.new_parcel({"name": name, "size": "small", "deadline": time.time() + 6 * 3600, "weightKg": 2,
                                "destination": {"lat": lat, "lng": lng, "label": f"{name} Road, Colombo"}, **over})


def _planned_run(parcels, vtype="van"):
    van = delivery.new_vehicle({"name": f"Van {len(db.all_entities('vehicles'))}", "type": vtype, "plate": "CAB-1234"})
    drv = delivery.new_driver({"name": "Dee Driver", "phone": "+94 77 000 1111", "vehicleId": van["id"]})
    stops = delivery.build_run_stops(parcels)
    route = asyncio.run(routing.compute_route(stops, vehicle_type=vtype))
    trip = store.new_trip(stops, route, vehicle_type=vtype)
    trip.update({"driverId": drv["id"], "vehicleId": van["id"], "status": "planned",
                 "parcelIds": [p["id"] for p in parcels], "plannedEtaPerStop": []})
    store.save_trip(trip)
    for p in parcels:
        delivery.set_parcel_status(p, "assigned", trip_id=trip["id"])
    return trip


def test_every_role_has_a_label_a_landing_page_and_only_known_permissions():
    roles = C["viewer"].get("/api/auth/roles").json()
    assert [r["id"] for r in roles] == ROLES
    for r in roles:
        assert r["label"] and r["help"] and r["landing"] in r["pages"], r["id"]
        assert set(r["permissions"]) <= auth.ALL_PERMS
        me = C[r["id"]].get("/api/auth/me").json()["user"]
        assert me["permissions"] == r["permissions"] and me["landing"] == r["landing"] and me["pages"] == r["pages"]
    assert "users" in auth.ROLE_PERMS["admin"] and not any("users" in auth.ROLE_PERMS[r] for r in ROLES if r != "admin")


def test_the_permission_matrix():
    """(method, path) -> roles allowed. Everything else must be refused with 403."""
    p = _parcel("Matrix")
    t = _planned_run([_parcel("Matrix run")])
    matrix = [
        ("GET", "/api/parcels", ROLES),
        ("GET", "/api/vehicles", ROLES),
        ("GET", "/api/trips", ROLES),
        ("GET", "/api/warehouse/loading", ["admin", "supervisor", "dispatcher", "warehouse", "viewer"]),
        ("GET", "/api/warehouse/receiving", ["admin", "supervisor", "dispatcher", "warehouse", "viewer"]),
        ("GET", "/api/reports/summary", ["admin", "supervisor", "analyst"]),
        ("GET", "/api/impact", ["admin", "supervisor", "analyst"]),
        ("GET", "/api/notifications", ["admin", "supervisor", "dispatcher", "support"]),
        ("GET", "/api/users", ["admin"]),
        ("POST", f"/api/parcels/{p['id']}/loaded", ["admin", "supervisor", "warehouse"]),
        ("POST", f"/api/parcels/{p['id']}/received", ["admin", "supervisor", "warehouse"]),
        ("POST", "/api/vehicles", ["admin", "supervisor", "fleet"]),
        ("POST", "/api/dispatch/propose", ["admin", "supervisor", "dispatcher"]),
        ("POST", f"/api/runs/{t['id']}/breakdown", ["admin", "supervisor", "dispatcher"]),
        ("POST", "/api/route-templates", ["admin", "supervisor"]),
        ("PUT", "/api/settings/depot", ["admin", "supervisor"]),
        ("PUT", "/api/settings/fuel", ["admin", "supervisor"]),
    ]
    bodies = {"/api/vehicles": {"name": "V", "type": "van"}, "/api/dispatch/propose": {"engine": "rules"},
              f"/api/runs/{t['id']}/breakdown": {}, "/api/route-templates": {"code": "x"},
              "/api/settings/depot": {"lat": 6.93, "lng": 79.84, "label": "Hub"}, "/api/settings/fuel": {"pricePerL": 1}}
    for method, path, allowed in matrix:
        for role in ROLES:
            r = C[role].request(method, path, json=bodies.get(path))
            if role in allowed:
                assert r.status_code != 403, f"{role} should be allowed {method} {path}: {r.status_code} {r.text[:80]}"
            else:
                assert r.status_code == 403, f"{role} must be refused {method} {path}: {r.status_code}"


def test_parcel_work_is_split_between_dispatch_and_customer_service():
    body = {"name": "Call-centre parcel", "size": "small", "deadline": time.time() + 7200,
            "destination": {"lat": 6.95, "lng": 79.86, "label": "Somewhere, Colombo"}}
    for role in ("support", "dispatcher"):
        assert C[role].post("/api/parcels", json=body).status_code == 200
    for role in ("warehouse", "fleet", "analyst", "viewer"):
        assert C[role].post("/api/parcels", json=body).status_code == 403
    # customer service reviews a failed delivery and reschedules it, but can't plan or record deliveries
    f = _parcel("Failed one")
    t = _planned_run([f])
    t["status"] = "active"
    store.save_trip(t)
    delivery.set_parcel_status(db.load_entity("parcels", f["id"]), "in_transit", trip_id=t["id"])
    assert C["support"].post(f"/api/parcels/{f['id']}/fail", json={"reason": "no_one_home"}).status_code == 403
    assert C["dispatcher"].post(f"/api/parcels/{f['id']}/fail", json={"reason": "no_one_home"}).status_code == 200
    r = C["support"].post(f"/api/parcels/{f['id']}/review", json={"decision": "reschedule", "scheduledFor": time.time() + 30 * 3600})
    assert r.status_code == 200 and r.json()["status"] == "awaiting_redelivery"
    assert C["warehouse"].post(f"/api/parcels/{f['id']}/review", json={"decision": "return"}).status_code == 403


def test_driver_keys_are_hidden_from_roles_that_cannot_use_them():
    D = C["dispatcher"].get("/api/drivers").json()
    assert D and all("token" in d for d in D)
    for role in ("warehouse", "analyst", "viewer", "support"):
        assert all("token" not in d for d in C[role].get("/api/drivers").json()), role
    assert all("token" in d for d in C["fleet"].get("/api/drivers").json()), "the fleet manager shares the driver link"


def test_loading_bay_lists_parcels_in_loading_order_and_tracks_ticks():
    a, b, c = _parcel("Near stop", 6.94, 79.85), _parcel("Mid stop", 6.99, 79.90), _parcel("Far stop", 7.05, 79.95, fragile=True)
    pk = delivery.new_parcel({"name": "Collected on the way", "size": "small", "requestType": "pickup", "deadline": time.time() + 7200,
                              "destination": HUB, "pickup": {"lat": 6.97, "lng": 79.88, "label": "Customer", "latest": time.time() + 7200}})
    t = _planned_run([a, b, c, pk])
    board = C["warehouse"].get("/api/warehouse/loading").json()
    run = next(r for r in board["runs"] if r["tripId"] == t["id"])
    names = [p["name"] for p in run["parcels"]]
    assert "Collected on the way" not in names, "pickups are not loaded at the hub"
    assert names == ["Far stop", "Mid stop", "Near stop"], "the last stop goes on first"
    assert [p["loadOrder"] for p in run["parcels"]] == [1, 2, 3]
    assert run["driverName"] == "Dee Driver" and run["plate"] == "CAB-1234" and run["total"] == 3 and run["loaded"] == 0
    assert run["parcels"][0]["fragile"] is True
    # tick one
    r = C["warehouse"].post(f"/api/parcels/{c['id']}/loaded", json={"loaded": True})
    assert r.status_code == 200 and r.json()["loadedBy"] == "warehouse1"
    run = next(r for r in C["warehouse"].get("/api/warehouse/loading").json()["runs"] if r["tripId"] == t["id"])
    assert run["loaded"] == 1 and not run["ready"]
    tl = [e["message"] for e in C["viewer"].get(f"/api/parcels/{c['id']}").json()["timeline"]]
    assert any(m.startswith("Loaded onto") and "warehouse1" in m for m in tl)
    # a pickup can't be ticked; untick works
    assert C["warehouse"].post(f"/api/parcels/{pk['id']}/loaded", json={"loaded": True}).status_code == 409
    assert C["warehouse"].post(f"/api/parcels/{c['id']}/loaded", json={"loaded": False}).json()["loadedAt"] is None
    # everything in one go
    assert C["warehouse"].post(f"/api/runs/{t['id']}/loaded", json={"loaded": True}).json()["parcels"] == 3
    run = next(r for r in C["warehouse"].get("/api/warehouse/loading").json()["runs"] if r["tripId"] == t["id"])
    assert run["ready"] and run["loaded"] == 3
    # the dispatcher can see progress but not tick for the warehouse
    assert C["dispatcher"].post(f"/api/parcels/{a['id']}/loaded", json={"loaded": False}).status_code == 403
    # moving a parcel to another run clears its tick
    delivery.set_parcel_status(db.load_entity("parcels", a["id"]), "pending", trip_id=None)
    assert db.load_entity("parcels", a["id"])["loadedAt"] is None


def test_loaded_stays_ticked_when_the_run_starts_and_leaves_the_loading_board():
    p = _parcel("Goes out")
    t = _planned_run([p])
    C["warehouse"].post(f"/api/runs/{t['id']}/loaded", json={})
    t = store.get_trip(t["id"])
    t["status"] = "active"
    store.save_trip(t)
    delivery.set_parcel_status(db.load_entity("parcels", p["id"]), "in_transit", trip_id=t["id"])
    assert db.load_entity("parcels", p["id"])["loadedAt"], "still loaded while out for delivery"
    assert all(r["tripId"] != t["id"] for r in C["warehouse"].get("/api/warehouse/loading").json()["runs"])


def test_receiving_desk_checks_in_pickups_failures_and_returns():
    coll = delivery.new_parcel({"name": "Customer package", "size": "small", "requestType": "pickup", "receiverName": "Mr Silva",
                                "deadline": time.time() + 7200, "destination": HUB,
                                "pickup": {"lat": 6.97, "lng": 79.88, "label": "Silva Street", "latest": time.time() + 7200}})
    delivery.set_parcel_status(db.load_entity("parcels", coll["id"]), "picked_up")
    fail = _parcel("Bounced parcel")
    t = _planned_run([fail])
    delivery.set_parcel_status(db.load_entity("parcels", fail["id"]), "in_transit", trip_id=t["id"])
    delivery.record_failed_attempt(db.load_entity("parcels", fail["id"]), "no_one_home", None, t["id"])
    ret = _parcel("Sent back")
    delivery.set_parcel_status(db.load_entity("parcels", ret["id"]), "returned")
    board = C["warehouse"].get("/api/warehouse/receiving").json()
    kinds = {i["name"]: i["kind"] for i in board["items"]}
    assert kinds["Customer package"] == "collected" and kinds["Bounced parcel"] == "not_delivered" and kinds["Sent back"] == "returned"
    assert next(i for i in board["items"] if i["name"] == "Bounced parcel")["reason"] == "Nobody home"
    r = C["warehouse"].post(f"/api/parcels/{fail['id']}/received", json={"note": "Box dented"})
    assert r.status_code == 200 and r.json()["receivedBy"] == "warehouse1"
    after = C["warehouse"].get("/api/warehouse/receiving").json()
    assert "Bounced parcel" not in [i["name"] for i in after["items"]] and after["counts"]["not_delivered"] == board["counts"]["not_delivered"] - 1
    assert C["warehouse"].post(f"/api/parcels/{_parcel('Not coming back')['id']}/received", json={}).status_code == 409
    # when it moves again it will need checking in again
    delivery.set_parcel_status(db.load_entity("parcels", fail["id"]), "pending", trip_id=None)
    assert db.load_entity("parcels", fail["id"])["receivedAt"] is None


def test_new_users_can_be_given_any_role_and_the_admin_screen_lists_them():
    adm = C["admin"]
    for role in ROLES:
        r = adm.post("/api/users", json={"username": f"new-{role}", "name": role, "role": role, "password": PW})
        assert r.status_code == 200 and r.json()["role"] == role and r.json()["landing"]
    assert adm.post("/api/users", json={"username": "new-boss", "role": "boss", "password": PW}).status_code == 400


def test_demo_accounts_are_listed_only_when_the_server_is_started_for_a_demo():
    anon = TestClient(main.app)                                    # not signed in
    off = anon.get("/api/auth/demo-accounts").json()
    assert off == {"enabled": False, "accounts": []}, "never listed on a real deployment"
    for r in ROLES:                                                # mark a few as demo accounts, as the seed does
        u = auth.find_by_username(f"{r}1")
        u["demo"] = r != "viewer"
        db.save_entity("users", u)
    os.environ["DEMO_ACCOUNTS"] = "1"
    try:
        on = anon.get("/api/auth/demo-accounts").json()
    finally:
        os.environ.pop("DEMO_ACCOUNTS")
    assert on["enabled"] and on["password"]
    roles = [a["role"] for a in on["accounts"]]
    assert roles == [r for r in ROLES if r != "viewer"], "one card per group, in the usual order"
    for a in on["accounts"]:
        assert a["username"] == f"{a['role']}1" and a["roleLabel"] and a["help"] and a["landing"] in a["pages"]
        assert "passwordHash" not in a and "token" not in a


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
